---
状态: 稳定
最后更新: 2026-11-14
下次复核: 2027-11-14
---

# 架构总览

> 本文说明 dev-kb 的模块划分、三条主数据流与存储设计，逐层给出职责边界与不可违反的硬约束。

## 设计目标与硬约束

1. **本地优先**：全部索引与模型都在本机，免 Docker、免外部服务（Qdrant 支持 Docker 服务模式但默认回退本地文件模式）。
2. **证据可复核**：回答中的代码段必须由程序实时读取原文件返回，不用索引里的旧副本；每条证据带 `项目/路径:行号` 与 Git 提交号。
3. **事实与推断分层**：工具层只输出确定性事实（清单、哈希、图计算），归纳与建议只发生在 LLM 编排层，且必须标注是推断。
4. **写操作有门禁**：文档更新绝不自动落盘，走 提案 → 人工审批 → 应用 闭环。

## 模块划分（src/agent_app/）

```text
ingestion/          数据进入
  scanner.py        M1 只读扫描：遍历、分类（code/document/config/data）、排除规则、项目类型识别
  excludes.py       默认排除清单（目录 / 文件模式 / 二进制扩展名 / 2MB 上限）
  code_parser.py    tree-sitter 按符号切块（php/ts/tsx/python/rust）
  document_parser.py Markdown 按标题切块，保留 heading_path
  manifest_parser.py 清单解析（composer.json / package.json / Cargo.toml / .venv fallback）
  sync.py           M2 增量同步：SHA-1 比对 → 删旧块 → 重建 → 入库 → 生成向量

indexing/           存储与向量化
  metadata_store.py SQLite：files / chunks / chunk_fts(FTS5) / sync_errors / spec_* / doc_proposals
  fts_tokenizer.py  CJK bigram 分词器（写入与查询两侧同一预处理）
  embedder.py       BGE-M3（MPS 加速，1024 维），预算制分批防 OOM
  vector_store.py   Qdrant 本地文件模式；Collection 名带模型标识
  reranker.py       bge-reranker-v2-m3 交叉编码器精排（辅助信号，blend ≤ 0.3）

tools/              确定性工具层（只输出事实）
  retrieval.py      混合检索：符号精确 ∪ FTS ∪ 向量 ∪ 路径提示，RRF 合并
  profile.py        M5 项目画像：技术栈 / 目录 / 入口 / 关键文档
  source.py         read_source：按行号读原文件，检测索引后变更
  projects.py       projects.yaml 注册表

config.py           配置定位：显式路径 > 环境变量 > 仓库根默认；缺失时给出「复制模板副本」提示
                   （真实配置 projects.yaml / kb_projects.yaml 属本地状态，不入库，
                     仓库只分发 *.example.yaml 模板副本）

specs/              M4 Spec 结构化
  parser.py         解析 design/plan/task 三类文档：状态行、复选框、前置依赖、代码引用
  task_graph.py     任务依赖图：进度、阻塞、并行组（纯确定性图算法）

proposals/          M6 文档维护闭环
  impact_analysis.py  Git 变更 × spec_code_refs / 正文提及 → 受影响文档
  llm_draft.py        LLM 读 git diff 生成实质性更新草案（仍走审批门禁）
  apply_update.py     仅 approved 提案可写入；生成 unified diff；触发增量索引

llm/                LLM 编排层（唯一允许"归纳"的地方）
  backend.py        OpenAI 兼容接口优先，缺省回退本地 MLX（Qwen3-4B）
  query_expansion.py 中文问题 → 英文符号候选（磁盘缓存，仅代码定位意图触发）
  answer.py         证据包 → 自然语言答案（强制引用，证据不足明说）

*_cli.py            六个命令入口（scan / sync / ask / spec / profile / docs）
```

## 数据流

### 索引流（sync_cli）

```text
projects.yaml ──► scanner（分类+排除）──► 逐文件 SHA-1 与 files 表比对
      │ 新/变更                                    │ 未变
      ▼                                            ▼
 code_parser（tree-sitter 符号块）          跳过（补 Spec 解析的例外）
 document_parser（标题块）
      ▼
 chunks + chunk_fts（FTS5 同步写入）
      ▼
 embedder（BGE-M3，预算制分批）──► Qdrant upsert（point id = chunk id）──► mark_embedded
```

三个关键设计：

- **逐文件提交**（`sync.py` 中每个文件处理完即 `commit()`）：长同步被中断后可断点续跑，`--embed-pending` 可补齐中断时未生成向量的块。
- **三存储联动删除**：文件变更/删除时，chunks、chunk_fts、Qdrant 向量同步清理，point id 直接用 SQLite 的 chunk id 关联，无额外映射表。
- **错误不静默**：解析失败写入 `sync_errors` 表并计入报告，而不是悄悄跳过。

### 问答流（ask_cli）

```text
问题 ──► 项目名自动识别（projects.yaml 注册表子串匹配）
     ──► HybridSearch.search：
           ① 符号精确通道（权重 2.0）：查询中的英文 token 命中 symbol 列
           ② 路径子串通道（0.6~0.9）：英文 token + 中文 2~4 gram 命中路径
           ③ LLM 查询扩展（1.2/0.9/0.3 三档，仅代码定位意图）
           ④ FTS5 关键词（AND 优先，落空降级 OR，降级权重减半）
           ⑤ 意图路径提示（_PATH_HINTS：路由→routes/ 等 18 项）
           ⑥ 向量语义（BGE-M3，权重 0.8）
           ── RRF（k=60）合并 ── 测试文件 ×0.4 ── 代码意图 code×1.5/doc×0.7
           ── bge-reranker 精排（blend 0.3，融合分主导）
     ──► read_source 逐条回读原文件（防索引滞后，拒绝目录越界）
     ──► LLM 归纳（强制 [证据N] 引用；后端不可用则只输出证据包）
```

项目范围由 `tools/routing.py` 控制：只有唯一识别项目名才自动限定；歧义或未知项目返回候选，跨项目检索必须显式选择。`mcp_server.py` 以 stdio 暴露 `index_status` / `search` / `read_source` 三个只读工具，不提供索引同步或文件写入。

检索器可输出目标块、扩展词、各通道排名、融合排名与重排后排名。评测结果记录完整配置、模型、源码指纹、索引内容指纹和题集指纹，无需 Git 即可核对运行版本。

### 文档维护流（docs_cli，M6）

```text
git log/status 变更文件 ──► 两通道找受影响文档：
    ① spec_code_refs：Spec 文档中显式 `path:line` 引用（M4 解析时提取）
    ② chunks 正文 LIKE：文档内容提及该路径
──► draft_proposal（模板草案）/ redraft（LLM 基于 git diff 的实质草案）
──► 人工 approve / reject ──► apply：写文件 + unified diff 存档 + 增量索引
```

## 存储设计（data/meta.db，单文件 170MB 级）

| 表 | 内容 | 关键设计 |
|---|---|---|
| files | 文件清单 + SHA-1 + git 提交 | 主键 (project, path)，增量比对依据 |
| chunks | 块原文 + 元数据 | embedded 标志位支持断点补挂 |
| chunk_fts | FTS5 全文索引 | rowid = chunk id；写入查询两侧同一 CJK 分词 |
| sync_errors | 解析失败记录 | 不静默跳过 |
| spec_docs / spec_tasks / spec_deps / spec_code_refs | M4 Spec 结构化 | spec_key 为 canonical key，三类文档据此配对 |
| doc_proposals | M6 提案 | status 机：pending → approved/rejected → applied |

Qdrant：`data/qdrant/` 本地文件模式，Collection 名 `chunks_BAAI_bge_m3` —— **换 Embedding 模型 = 新 Collection = 全量重建**，不混向量。

## 评测体系（evals/）

100 题用例集（`cases_v1.jsonl`）覆盖四种难度：literal（精确符号）/ semantic（中文语义）/ doc（纯文档）/ cross（跨项目）。指标 recall@1/3/5 + MRR@5；任何检索参数修改必须过 `compare.py` 的 McNemar 配对检验（recall@5 不允许显著退化）。方法论与框架沿用 kaiwu/evals。详见 [evals/README.md](../evals/README.md)。

## 相关文档

- [技术决策记录（ADR）](./decisions.md)
- [知识点地图](./knowledge-map.md)
- [检索评测与实验记录](../evals/README.md)
