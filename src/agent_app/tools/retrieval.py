"""混合检索：符号精确 ∪ FTS 关键词 ∪ 标题链 ∪ 术语别名 ∪ 向量语义，RRF 合并排序。

对照规划第四节：
- 函数名/类名/路由/错误码 → 符号精确 + 关键词优先。
- 描述性问题 → 向量语义补充。
- 合并用 Reciprocal Rank Fusion，避免两路量纲不可比。

契约接入（知识源接入契约）：
- **标题链通道**：文档标题里的词本身就是可独立召回的信号。它与正文同等进 FTS，
  同时另开一路独立通道，以便单独调权、单独归因（评测里能看到它贡献了多少）。
- **术语通道**：知识源声明的术语别名（如「低秩适配」→ LoRA）直接挂接对应块。
- **意图映射**：知识源声明的「中文意图 → 本库路径」替代全局硬编码路径表。
- **结构可信度**：声明提供标题链却缺标题链的块降权——结构信息缺失时，
  语义召回的可信度下降。
"""

from __future__ import annotations

import os
import re

from ..indexing.metadata_store import MetadataStore

RRF_K = 60
# 查询中疑似符号的 token（驼峰、下划线、全大写常量、含 ::/@/. 限定符等）
_SYMBOLISH = re.compile(r"^[A-Za-z_][A-Za-z0-9_@$.#]{2,}$")
# 测试文件降权：定位生产代码时测试不应排在前面
_TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|e2e)/|Test\.php$|\.(test|spec)\.[jt]sx?$")
# 以下权重可用环境变量覆盖，便于逐通道做评测归因（ADR-0008 的做法）：
#   DEV_KB_HEADING_WEIGHT / DEV_KB_ALIAS_WEIGHT / DEV_KB_INTENT_HINT_WEIGHT
#   DEV_KB_STRUCTURE_TRUST
# 声明了标题链能力却缺标题链的块降权（结构缺失 → 语义召回不可靠）
STRUCTURE_TRUST = float(os.environ.get("DEV_KB_STRUCTURE_TRUST", "0.7"))
# 标题链独立通道权重：与"意图路径提示"同档
HEADING_WEIGHT = float(os.environ.get("DEV_KB_HEADING_WEIGHT", "0.7"))
# 标题链通道是否允许 OR 宽松降级。
# 默认关闭：宽松降级会捞进大量只命中单个 token 的弱候选，实测 r@1 -0.02、
# r@5 -0.02（破坏 q061/q080/q100）。标题通道要的是精度——问"核心架构"命中
# "### 模块"这种父标题命中子节——AND 精确匹配即可。
HEADING_FALLBACK = os.environ.get("DEV_KB_HEADING_FALLBACK", "0") != "0"
# 术语别名通道权重
ALIAS_WEIGHT = float(os.environ.get("DEV_KB_ALIAS_WEIGHT", "1.0"))
# 知识源意图映射的路径提示权重
INTENT_HINT_WEIGHT = float(os.environ.get("DEV_KB_INTENT_HINT_WEIGHT", "0.7"))

# 代码定位意图：这类问题的答案在代码而非文档，给代码块加权、文档块降权
_CODE_SEEK = re.compile(
    r"在哪|哪里|哪个文件|哪个类|哪个函数|哪个方法|定义在|入口|"
    r"怎么[^，。？?]{0,8}(实现|处理|校验|获取|持久化|恢复|存储|计算|解析)|"
    r"如何[^，。？?]{0,8}(实现|处理|恢复|通知|同步)|支持哪些"
)
# 明确找文档的问题不适用代码加权
_DOC_SEEK = re.compile(r"文档|规范|方案|设计|介绍|有哪些要求|学习路径")


def _extract_symbols(query: str) -> list[str]:
    out = []
    for tok in re.split(r"[\s，。,.'\"'""''（）()：:？?]+", query):
        if _SYMBOLISH.match(tok) and len(tok) >= 3:
            out.append(tok)
    return out


_CJK_RUN = re.compile(r"[一-鿿]{2,}")


def _subtokens(term: str) -> list[str]:
    """把扩展候选词拆成子词：snake_case/路径分隔 + 驼峰拆分，过滤短词。

    例：validate_entry_filename → [validate, entry, filename]
    """
    out: list[str] = []
    for part in re.split(r"[_/@$.#-]+", term):
        for tok in re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z]+|[a-z]+|\d+", part):
            t = tok.lower()
            if len(t) >= 3 and t.isalpha():
                out.append(t)
    return out


def _extract_cjk_terms(query: str) -> list[str]:
    """提取中文词串并拆为 2~4 gram，用于路径/目录名匹配（如"视频模型"命中 `04-视频模型/`）。

    排序：更长的 gram 优先，同长度按出现位置靠前优先（句首词根更可能是主题）。
    """
    terms: list[tuple[str, int]] = []
    for run in _CJK_RUN.findall(query):
        terms.append((run, 0))
        for n in (4, 3, 2):
            for i in range(0, len(run) - n + 1):
                terms.append((run[i:i + n], i))
    seen: set[str] = set()
    out: list[str] = []
    for t, _ in sorted(terms, key=lambda x: (-len(x[0]), x[1])):
        if t not in seen:
            seen.add(t)
            out.append(t)
        if len(out) >= 12:
            break
    return out


class HybridSearch:
    def __init__(self, store: MetadataStore, vector_store=None, rerank: bool = False,
                 blend: float = 0.5, code_boost: float = 1.5, doc_damp: float = 0.7,
                 expand: bool = False, expand_use_llm: bool = True, declarations: dict | None = None):
        self.store = store
        self.vs = vector_store
        self.rerank = rerank
        self.blend = blend
        self.code_boost = code_boost  # 代码定位意图时代码块加权
        self.doc_damp = doc_damp      # 代码定位意图时文档块降权
        self.expand = expand          # LLM 查询扩展（中文→英文符号候选）
        self.expand_use_llm = expand_use_llm  # 只读 MCP 可限制为仅使用已有缓存
        self._declarations = declarations
        self._coverage_cache: dict[str, bool] = {}
        self._query_lower = ""

    # ---- 契约声明 ----

    @property
    def declarations(self) -> dict:
        if self._declarations is None:
            from ..contract.declaration import load_declarations
            self._declarations = load_declarations()
        return self._declarations

    def _decl_for(self, project: str | None):
        if project:
            return self.declarations.get(project)
        return None

    def _intent_map(self, project: str | None) -> dict[str, tuple[str, ...]]:
        """项目确定时用该源自己的意图映射；跨项目检索时聚合所有源。

        跨项目题（如"数据库迁移文件在哪个目录"）不知道该用哪个源的意图表，
        因此把所有源声明的意图合并——这也正是全局硬编码表原来的行为，
        但现在每条映射都来自具体知识源自己的结构声明。
        """
        if project:
            decl = self.declarations.get(project)
            return decl.intent_map if decl else {}
        merged: dict[str, tuple[str, ...]] = {}
        for decl in self.declarations.values():
            for intent, patterns in decl.intent_map.items():
                bucket = list(merged.get(intent, ()))
                for pat in patterns:
                    if pat not in bucket:
                        bucket.append(pat)
                merged[intent] = tuple(bucket)
        return merged

    def _heading_trusted(self, project: str | None) -> bool:
        """该源是否声明了标题链能力，且实际覆盖率达标（R-02 的判定）。"""
        if not project:
            return False
        if project in self._coverage_cache:
            return self._coverage_cache[project]
        decl = self._decl_for(project)
        trusted = False
        if decl is not None and decl.provides_field("heading_path"):
            total, missing = self.store.heading_coverage(project)
            trusted = total > 0 and (total - missing) / total >= 0.95
        self._coverage_cache[project] = trusted
        return trusted

    def _alias_expansions(self, project: str | None) -> list[tuple[str, str]]:
        """查询里命中的术语别名 → canonical term。

        术语通道是**查询侧扩展**：用户问"低秩适配"就扩展成"LoRA"，再交给
        符号/路径/FTS 通道排序。早期实现是文档侧匹配（按路径长度取前 N），
        那是任意排序，会挤掉正确结果。
        """
        if project:
            decls = [d for d in (self.declarations.get(project),) if d is not None]
        else:
            decls = list(self.declarations.values())
        out: list[tuple[str, str]] = []
        for decl in decls:
            if not decl.provides_field("aliases"):
                continue
            for surface, canonical in decl.alias_to_canonical().items():
                if len(surface) >= 2 and surface.lower() in self._query_lower:
                    if canonical not in [c for _s, c in out]:
                        out.append((surface, canonical))
        return out

    def search(self, query: str, project: str | None = None, limit: int = 8,
               diagnostics: dict | None = None) -> list[dict]:
        """返回检索结果；传入 diagnostics 字典时同步记录召回与融合信息。"""
        ranks: dict[int, float] = {}
        sources: dict[int, set[str]] = {}
        channel_ranks: dict[str, dict[int, int]] = {}
        self._query_lower = query.lower()

        def add(rows, source: str, get_id, base_weight: float = 1.0):
            for rank, row in enumerate(rows):
                cid = get_id(row)
                ranks[cid] = ranks.get(cid, 0.0) + base_weight / (RRF_K + rank + 1)
                sources.setdefault(cid, set()).add(source)
                best = channel_ranks.setdefault(source, {}).get(cid)
                channel_ranks[source][cid] = min(best, rank + 1) if best else rank + 1

        code_seeking = bool(_CODE_SEEK.search(query)) and not _DOC_SEEK.search(query)

        # 1. 符号精确（权重最高）+ 路径子串（符号 token 与中文词串）
        for sym in _extract_symbols(query):
            add(self.store.symbol_search(sym, project, limit=limit), "symbol", lambda r: r["id"], 2.0)
            add(self.store.path_search(sym, project, limit=limit), "path", lambda r: r["id"], 0.9)
        expanded_terms: list[str] = []
        if self.expand and code_seeking:
            # 完整复合词可做子串挂接；纯单词禁用，避免 migrations 误中 MapMigrationStore。
            from ..llm.query_expansion import expand
            expanded_terms = expand(query, use_llm=self.expand_use_llm)
            for term in expanded_terms:
                add(self.store.symbol_search(term, project, limit=limit), "expand", lambda r: r["id"], 1.2)
                add(self.store.path_search(term, project, limit=limit), "expand", lambda r: r["id"], 0.9)
                subterms = _subtokens(term)
                full_hits = (
                    self.store.symbol_substring_search(term, project, limit=3)
                    if len(subterms) > 1 else []
                )
                add(full_hits, "expand-full-sub", lambda r: r["id"], 0.6)
                for sub in subterms:
                    sub_hits = self.store.symbol_substring_search(sub, project, limit=2)
                    add(sub_hits, "expand-sub", lambda r: r["id"], 0.3)
        for term in _extract_cjk_terms(query):
            add(self.store.path_search(term, project, limit=3), "path", lambda r: r["id"], 0.6)

        # 2. FTS 关键词（AND 优先；落空时覆盖率降级，权重减半防稀释向量结果）
        try:
            fts_rows = self.store.fts_search(query, project, limit=limit * 2, or_fallback=False)
            if fts_rows:
                add(fts_rows, "fts", lambda r: r["id"], 1.0)
            else:
                weak = self.store.fts_search(query, project, limit=limit, or_fallback=True)
                add(weak, "fts-weak", lambda r: r["id"], 0.5)
        except Exception:
            pass  # 查询无法构成合法 MATCH 表达式时跳过

        # 3. 标题链独立通道：只看文档标题里的词，与正文通道分开归因
        if HEADING_WEIGHT > 0:
            try:
                heading_rows = self.store.heading_search(query, project, limit=limit,
                                                         or_fallback=HEADING_FALLBACK)
                if heading_rows:
                    add(heading_rows, "heading", lambda r: r["id"], HEADING_WEIGHT)
            except Exception:
                pass  # 标题索引为空或查询不合法时跳过

        # 4. 术语别名通道：知识源声明的别名直接挂接对应块
        if ALIAS_WEIGHT > 0:
            for _surface, canonical in self._alias_expansions(project):
                # 三路命中合并去重后只加一次：同一批块若分三次 add，
                # RRF 分数会被重复计入，等于给术语通道三倍权重。
                merged: dict[int, int] = {}
                for rows in (
                    self.store.symbol_search(canonical, project, limit=limit),
                    self.store.path_search(canonical, project, limit=limit),
                ):
                    for rank, r in enumerate(rows):
                        merged.setdefault(r["id"], rank)
                try:
                    for rank, r in enumerate(
                        self.store.fts_search(canonical, project, limit=limit,
                                              or_fallback=False)
                    ):
                        merged.setdefault(r["id"], rank)
                except Exception:
                    pass
                ordered = [{"id": cid} for cid in sorted(merged, key=lambda i: merged[i])]
                add(ordered, "alias", lambda r: r["id"], ALIAS_WEIGHT)

        # 5. 意图路径提示：知识源声明的"中文意图 → 本库路径"
        if INTENT_HINT_WEIGHT > 0:
            for intent, patterns in self._intent_map(project).items():
                if intent not in query:
                    continue
                for pattern in patterns:
                    add(self.store.path_search(pattern, project, limit=3),
                        "intent-hint", lambda r: r["id"], INTENT_HINT_WEIGHT)

        # 6. 向量语义
        if self.vs is not None:
            from ..indexing import embedder
            qv = embedder.embed_texts([query])[0]
            add(self.vs.search(qv, project=project, limit=limit * 2), "vector", lambda p: p.id, 0.8)

        trusted = self._heading_trusted(project)
        top_ids = sorted(ranks, key=lambda i: -ranks[i])[: limit * 3]
        results = []
        for cid in top_ids:
            row = self.store.get_chunk(cid)
            if not row:
                continue
            score = ranks[cid]
            if _TEST_PATH.search(row["path"]):
                score *= 0.4  # 测试文件降权
            if STRUCTURE_TRUST > 0 and trusted and row["source_type"] == "document":
                heading = row["heading_path"]
                if not heading or heading == "[]":
                    score *= STRUCTURE_TRUST  # 结构缺失降权
            if code_seeking:
                if row["source_type"] == "code":
                    score *= self.code_boost
                elif row["source_type"] == "document":
                    score *= self.doc_damp
            results.append({
                "chunk_id": cid,
                "score": round(score, 4),
                "matched_by": sorted(sources[cid]),
                "project": row["project"],
                "path": row["path"],
                "source_type": row["source_type"],
                "language": row["language"],
                "symbol": row["symbol"],
                "heading_path": row["heading_path"],
                "category": row["category"] or "",
                "doc_role": row["doc_role"] or "",
                "start_line": row["start_line"],
                "end_line": row["end_line"],
                "content": row["content"],
            })
        results.sort(key=lambda r: -r["score"])
        if diagnostics is not None:
            diagnostics.update({
                "code_seeking": code_seeking,
                "expanded_terms": expanded_terms,
                "candidate_ids": list(ranks),
                "candidate_sources": {cid: sorted(items) for cid, items in sources.items()},
                "channel_ranks": channel_ranks,
                "fusion_ids": [r["chunk_id"] for r in results],
                "heading_trusted": trusted,
            })
        if self.rerank and results:
            from ..indexing import reranker
            results = reranker.rerank(query, results, top=limit, blend=self.blend)
        return results[:limit]
