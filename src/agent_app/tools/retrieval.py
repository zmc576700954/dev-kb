"""混合检索：符号精确 ∪ FTS 关键词 ∪ 向量语义，RRF 合并排序。

对照规划第四节：
- 函数名/类名/路由/错误码 → 符号精确 + 关键词优先。
- 描述性问题 → 向量语义补充。
- 合并用 Reciprocal Rank Fusion，避免两路量纲不可比。
"""

from __future__ import annotations

import re

from ..indexing.metadata_store import MetadataStore

RRF_K = 60
# 查询中疑似符号的 token（驼峰、下划线、全大写常量、含 ::/@/. 限定符等）
_SYMBOLISH = re.compile(r"^[A-Za-z_][A-Za-z0-9_@$.#]{2,}$")
# 测试文件降权：定位生产代码时测试不应排在前面
_TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|e2e)/|Test\.php$|\.(test|spec)\.[jt]sx?$")
# 意图 → 路径提示：结构类问题直接加权对应目录
_PATH_HINTS = {
    "路由": "routes/", "中间件": "iddleware", "迁移": "migrations/",
    "配置": "config", "命令": ["Console", "Commands"], "模型": "Models/",
    # 以下为评测驱动扩充（baseline-v3 短板：中文问法与英文目录名无映射）
    "状态管理": ["store/", "Store"], "存储": ["store", "storage"],
    "持久化": ["Repository", "store"], "仓库": ["Repository", "repo"],
    "鉴权": ["Auth", "Middleware"], "权限": ["Auth", "Middleware"],
    "组件": "components/", "页面": ["views/", "pages/"],
    "接口": ["Api", "api"], "服务": "Service",
    "文档": "docs/", "脚本": "scripts/",
    "工具": ["utils", "helpers"], "样式": ["styles", "css"],
}

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
                 expand: bool = False, expand_use_llm: bool = True):
        self.store = store
        self.vs = vector_store
        self.rerank = rerank
        self.blend = blend
        self.code_boost = code_boost  # 代码定位意图时代码块加权
        self.doc_damp = doc_damp      # 代码定位意图时文档块降权
        self.expand = expand          # LLM 查询扩展（中文→英文符号候选）
        self.expand_use_llm = expand_use_llm  # 只读 MCP 可限制为仅使用已有缓存

    def search(self, query: str, project: str | None = None, limit: int = 8,
               diagnostics: dict | None = None) -> list[dict]:
        """返回检索结果；传入 diagnostics 字典时同步记录召回与融合信息。"""
        ranks: dict[int, float] = {}
        sources: dict[int, set[str]] = {}
        channel_ranks: dict[str, dict[int, int]] = {}

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

        # 3. 意图路径提示（路由/配置/中间件等结构类问题）
        for kw, hint in _PATH_HINTS.items():
            if kw in query:
                hints = [hint] if isinstance(hint, str) else hint
                for h in hints:
                    add(self.store.path_search(h, project, limit=3), "path-hint", lambda r: r["id"], 0.7)

        # 4. 向量语义
        if self.vs is not None:
            from ..indexing import embedder
            qv = embedder.embed_texts([query])[0]
            add(self.vs.search(qv, project=project, limit=limit * 2), "vector", lambda p: p.id, 0.8)

        top_ids = sorted(ranks, key=lambda i: -ranks[i])[: limit * 3]
        results = []
        for cid in top_ids:
            row = self.store.get_chunk(cid)
            if not row:
                continue
            score = ranks[cid]
            if _TEST_PATH.search(row["path"]):
                score *= 0.4  # 测试文件降权
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
            })
        if self.rerank and results:
            from ..indexing import reranker
            results = reranker.rerank(query, results, top=limit, blend=self.blend)
        return results[:limit]
