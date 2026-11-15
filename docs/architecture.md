---
状态: 稳定
最后更新: 2026-11-15
下次复核: 2027-11-15
---

# 架构总览

> 本文说明 dev-kb 的模块划分、三条主数据流与存储设计，逐层给出职责边界与不可违反的硬约束。

## 设计目标与硬约束

1. **本地优先**：全部索引与模型都在本机，免 Docker、免外部服务（Qdrant 支持 Docker 服务模式但默认回退本地文件模式）。
2. **证据可复核**：回答中的代码段必须由程序实时读取原文件返回，不用索引里的旧副本；每条证据带 `项目/路径:行号` 与 Git 提交号。
3. **事实与推断分层**：工具层只输出确定性事实（清单、哈希、图计算），归纳与建议只发生在 LLM 编排层，且必须标注是推断。
4. **接入面不增长**：新增一种知识源只写声明，不改检索器。契约见 [知识源接入契约](./00-知识源契约.md)。
5. **写操作有门禁**：索引之外的写入（文档更新）已移出本项目范围。

## 模块划分（src/agent_app/）

```text
ingestion/          数据进入
  scanner.py        只读扫描：遍历、分类（code/document/config/data）、排除规则、项目类型识别
  excludes.py       跨项目通用的排除清单（目录 / 文件模式 / 二进制扩展名 / 2MB 上限）
  code_parser.py    tree-sitter 按符号切块（php/ts/tsx/python/rust）
  document_parser.py Markdown 按标题切块，保留 heading_path（祖先链 + 自身标题）
  manifest_parser.py 清单解析（composer.json / package.json / Cargo.toml / .venv fallback）
  sync.py           增量同步：SHA-1 比对 → 删旧块 → 重建 → 入库 → 生成向量；
                    按声明填入契约字段（category / doc_role / aliases）

indexing/           存储与向量化
  metadata_store.py SQLite：files / chunks / chunk_fts(FTS5) / chunk_headings(FTS5) / sync_errors
  fts_tokenizer.py  CJK bigram 分词器（写入与查询两侧同一预处理）
  embedder.py       BGE-M3（MPS 加速，1024 维），预算制分批防 OOM
  vector_store.py   Qdrant 本地文件模式；Collection 名带模型标识
  reranker.py       bge-reranker-v2-m3 交叉编码器精排（辅助信号，blend ≤ 0.3）

tools/              确定性工具层（只输出事实）
  retrieval.py      混合检索：符号精确 ∪ FTS ∪ 标题链 ∪ 术语别名 ∪ 向量 ∪ 路径提示，RRF 合并
  profile.py        项目画像：技术栈 / 目录 / 入口 / 关键文档
  source.py         read_source：按行号读原文件，检测索引后变更
  projects.py       projects.yaml 注册表
  routing.py        项目路由：唯一识别才自动限定，歧义返回候选

contract/           知识源接入契约（只读）
  declaration.py    声明模型：provides / content_roots / entry_docs / intent_map / roles / terms
  rules.py          7 条核心规则
  checks.py         规则的只读检查实现
  service.py        只读服务
  cli.py            人工入口（sources / rules / validate / suggest-hints）
  mcp_server.py     只读 MCP stdio 服务（4 个工具）

config.py           配置定位：显式路径 > 环境变量 > 仓库根默认；缺失时给出「复制模板副本」提示
                   （真实配置 projects.yaml 属本地状态，不入库，仓库只分发 *.example.yaml）

llm/                LLM 编排层（唯一允许"归纳"的地方）
  backend.py        OpenAI 兼容接口优先，缺省回退本地 MLX（Qwen3-4B）
  query_expansion.py 中文问题 → 英文符号候选（磁盘缓存，仅代码定位意图触发）
  answer.py         证据包 → 自然语言答案（强制引用，证据不足明说）

*_cli.py            命令入口（scan / sync / ask / profile）
```

## 数据流

### 索引流（sync_cli）

```text
projects.yaml ──► scanner（分类+排除+内容边界）──► 逐文件 SHA-1 与 files 表比对
      │ 新/变更                                    │ 未变
      ▼                                            ▼
 code_parser（tree-sitter 符号块）          跳过
 document_parser（标题块，heading_path = 祖先链）
      ▼
 chunks + chunk_fts（标题链也进可检索文本）+ chunk_headings（标题链独立索引）
      ▼
 按声明填契约字段（category / doc_role / aliases）
      ▼
 embedder（BGE-M3，预算制分批）──► Qdrant upsert（point id = chunk id）──► mark_embedded
```

三个关键设计：

- **逐文件提交**（`sync.py` 中每个文件处理完即 `commit()`）：长同步被中断后可断点续跑，`--embed-pending` 可补齐中断时未生成向量的块。
- **三存储联动删除**：文件变更/删除时，chunks、chunk_fts、chunk_headings、Qdrant 向量同步清理，point id 直接用 SQLite 的 chunk id 关联，无额外映射表。
- **错误不静默**：解析失败写入 `sync_errors` 表并计入报告，而不是悄悄跳过。

### 问答流（ask_cli）

```text
问题 ──► 项目名自动识别（projects.yaml 注册表子串匹配）
     ──► HybridSearch.search：
           ① 符号精确通道（权重 2.0）：查询中的英文 token 命中 symbol 列
           ② 路径子串通道（0.9 / 0.6）：英文 token + 中文 2~4 gram 命中路径
           ③ LLM 查询扩展（1.2/0.9/0.3 三档，仅代码定位意图）
           ④ FTS5 关键词（AND 优先，落空降级 OR，降级权重减半）
           ⑤ 标题链独立通道（0.7）：只匹配标题里的词（AND 精确匹配，不做宽松降级）
           ⑥ 术语别名通道（1.0）：别名 → canonical 词的查询侧扩展
           ⑦ 意图路径提示（0.7）：知识源声明的"中文意图 → 本库路径"
           ⑧ 向量语义（BGE-M3，权重 0.8）
           ── RRF（k=60）合并 ── 测试文件 ×0.4 ── 代码意图 code×1.5/doc×0.7
           ── 结构缺标题链 ×0.7（仅对声明了 heading_path 且覆盖率达标���源生效）
           ── bge-reranker 精排（blend 0.3，融合分主导）
     ──► read_source 逐条回读原文件（防索引滞后，拒绝目录越界）
     ──► LLM 归纳（强制 [证据N] 引用；后端不可用则只输出证据包）
```

项目范围由 `tools/routing.py` 控制：只有唯一识别项目名才自动限定；歧义或未知项目返回候选，跨项目检索必须显式选择。`mcp_server.py` 以 stdio 暴露 `index_status` / `search` / `read_source` 三个只读工具，不提供索引同步或文件写入。

检索器可输出目标块、扩展词、各通道排名、融合排名与重排后排名。评测结果记录完整配置、模型、源码指纹、索引内容指纹和题集指纹，无需 Git 即可核对运行版本。

### 契约校验流（contract_cli）

```text
projects.yaml 声明 ──► declaration（合并 tuning 表）
     ──► 7 条规则：声明层（R-01/03/04/07，读文件系统）
                   索引层（R-02/05/06，读 meta.db；无索引时不适用）
     ──► findings：error 阻断 / warning 提示
     ──► contract_check.sh 作为 CI 门禁（projects.ci.yaml 校验 dev-kb 自身）
```

契约层**只读**：不改知识源、不改索引、不生成提案。

## 存储设计（data/meta.db，单文件 170MB 级）

| 表 | 内容 | 关键设计 |
|---|---|---|
| files | 文件清单 + SHA-1 + git 提交 | 主键 (project, path)，增量比对依据 |
| chunks | 块原文 + 契约元数据（category / doc_role / aliases） | embedded 标志位支持断点补挂 |
| chunk_fts | FTS5 全文索引 | rowid = chunk id；可检索文本含标题链；写入查询两侧同一 CJK 分词 |
| chunk_headings | FTS5 标题链索引 | 独立召回通道，可单独调权与归因 |
| sync_errors | 解析失败记录 | 不静默跳过 |

Qdrant：`data/qdrant/` 本地文件模式，Collection 名 `chunks_BAAI_bge_m3` —— **换 Embedding 模型 = 新 Collection = 全量重建**，不混向量。payload 只存标识与定位信息；契约字段的唯一事实源是 SQLite。

## 规模红线

防止子系统重新长回来（红线值见 [README](../README.md) 与本仓库的 CI）：

| 红线 | 内容 |
|---|---|
| L1 | README 能力表 ≤ 6 行 |
| L2 | 非检索主链代码 ≤ 检索主链代码的 100% |
| L3 | 新增顶层目录必须在 README 回答"它站在检索链条哪一步" |
| L4 | `evals/results` + `evals/baselines` ≤ 2 MB |
| L5 | 仓库 tracked 总字节 ≤ 3 MB |
| L6 | 每个知识源的注册表声明 ≤ 30 行 |

## 评测体系（evals/）

100 题用例集（`cases_v1.jsonl`）覆盖四种难度：literal（精确符号）/ semantic（中文语义）/ doc（纯文档）/ cross（跨项目）。指标 recall@1/3/5 + MRR@5；任何检索参数修改必须过 `compare.py` 的 McNemar 配对检验（recall@5 不允许显著退化）。新增通道必须能**单独归因**——为此各通道权重可由环境变量覆盖。方法论与框架沿用 kaiwu/evals。详见 [evals/README.md](../evals/README.md)。

## 相关文档

- [知识源接入契约](./00-知识源契约.md)
- [技术决策记录（ADR）](./decisions.md)
- [知识点地图](./knowledge-map.md)
- [检索评测与实验记录](../evals/README.md)
