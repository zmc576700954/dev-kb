---
状态: 稳定
最后更新: 2026-11-14
下次复核: 2027-11-14
---

# 知识点地图

> 本文汇总项目涉及的知识领域，每条给出用在哪、关键概念与一手参考文档，便于向外查证与深化理解。

## 1. 全文检索与关键词索引

### SQLite FTS5
- **用在哪**：`metadata_store.py` 的 `chunk_fts` 虚拟表；`bm25()` 排序；`fts_tokenizer.py` 的写入/查询两侧预处理。
- **关键概念**：FTS5 虚拟表与 rowid 关联、BM25 排序、`MATCH` 表达式语法、AND/OR 查询组合。
- **参考**：
  - SQLite FTS5 官方文档：https://sqlite.org/fts5.html
  - FTS5 查询语法（MATCH 表达式）：https://sqlite.org/fts5.html#full_text_query_syntax
  - BM25 排序说明：https://sqlite.org/fts5.html#the_bm25_function

### 中文 bigram 分词
- **用在哪**：`fts_tokenizer.py`（CJK → 二元 bigram，拉丁/数字按词）。
- **关键概念**：FTS5 内置 unicode61 不切中文；应用层预处理保证索引与查询 token 一致；bigram 的召回/膨胀权衡。
- **参考**：
  - Unicode 中日韩统一表意文字范围（U+4E00–U+9FFF）：https://unicode.org/charts/PDF/U4E00.pdf
  - 《信息检索导论》第 2 章词条化与语言学处理（Manning, Raghavan & Schütze，免费在线版）：https://nlp.stanford.edu/IR-book/html/htmledition/tokenization-1.html

## 2. 向量检索与语义排序

### Embedding 模型（BGE-M3）
- **用在哪**：`embedder.py`（1024 维、归一化、余弦相似度、MPS 加速）。
- **关键概念**：稠密向量、余弦相似度、模型标识与向量空间绑定（ADR-0005）、按长度排序分批减少 padding。
- **参考**：
  - BGE-M3 模型卡（Hugging Face）：https://huggingface.co/BAAI/bge-m3
  - BGE-M3 论文（ACL 2024）：https://aclanthology.org/2024.acl-long.532/
  - Sentence-Transformers 文档：https://www.sbert.net/

### Qdrant 向量库
- **用在哪**：`vector_store.py`（本地文件模式、payload 过滤、point id 复用 SQLite chunk id）。
- **参考**：
  - Qdrant Python 客户端文档：https://qdrant.tech/documentation/concepts/
  - 本地模式（embedded / local file）：https://qdrant.tech/documentation/guides/installation/#local-mode
  - 向量搜索中的过滤：https://qdrant.tech/documentation/concepts/filtering/

### 重排（Reranking）
- **用在哪**：`reranker.py`（bge-reranker-v2-m3 CrossEncoder，max_length=512，sigmoid 归一，blend 混合）。
- **关键概念**：双塔检索（bi-encoder）vs 交叉编码器（cross-encoder）；重排分与融合分归一化后再加权——纯重排显著退化的实验教训见 ADR-0007。
- **参考**：
  - bge-reranker-v2-m3 模型卡：https://huggingface.co/BAAI/bge-reranker-v2-m3
  - Sentence-Transformers CrossEncoder 文档：https://www.sbert.net/examples/cross_encoder/applications/README.html

### 混合检索与 RRF
- **用在哪**：`retrieval.py` 五通道 + `RRF_K = 60` 合并。
- **关键概念**：Reciprocal Rank Fusion 只消费排名、免疫分数量纲差异；多路召回互补（符号精确 vs 语义 vs 关键词）。
- **参考**：
  - RRF 原始论文（Cormack, Clarke & Buettcher, SIGIR 2009）：https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf
  - RRF 的社区解释（Cormack 本人回答）：https://news.ycombinator.com/item?id=32477355

## 3. 代码解析与切块

### tree-sitter
- **用在哪**：`code_parser.py` 解析 PHP/TS/TSX/Python/Rust 按符号切块；`_const_decl_name` / `_pair_function_name` 提取 JS 特殊定义形态（zustand store 等）。
- **关键概念**：增量解析、AST 节点类型（`function_definition` / `method_definition` / `lexical_declaration` / `pair`）、`child_by_field_name("name")`、限定符号名（`Class@method`）。
- **参考**：
  - tree-sitter 官方文档：https://tree-sitter.github.io/tree-sitter/
  - tree-sitter Python 绑定：https://tree-sitter.github.io/py-tree-sitter/
  - tree-sitter-typescript 语法库：https://github.com/tree-sitter/tree-sitter-typescript
  - tree-sitter-php：https://github.com/tree-sitter/tree-sitter-php

### Markdown 结构化切块
- **用在哪**：`document_parser.py`（markdown-it token 流、标题层级、heading_path、大小块二次切分/合并）。
- **参考**：
  - markdown-it-py 文档：https://markdown-it-py.readthedocs.io/
  - CommonMark 规范（标题/段落结构）：https://spec.commonmark.org/

## 4. 本地大模型推理

### MLX / mlx-lm（Apple Silicon）
- **用在哪**：`llm/backend.py` 本地兜底后端（Qwen3-4B-Instruct-2507-4bit）；`query_expansion.py` 查询扩展。
- **关键概念**：统一内存架构、chat template、4bit 量化、生成参数（temperature 0.1~0.2 保证扩展稳定）。
- **参考**：
  - MLX 官方仓库：https://github.com/ml-explore/mlx
  - mlx-lm 文档（generate / load / chat template）：https://ml-explore.github.io/mlx/build/html/index.html
  - Qwen3 模型系列：https://github.com/QwenLM/Qwen3

### OpenAI 兼容接口
- **用在哪**：`backend.py` `_chat_openai`（urllib 直连 `/chat/completions`，无 SDK 依赖）。
- **参考**：
  - Chat Completions API 规范：https://platform.openai.com/docs/api-reference/chat
  - LiteLLM 支持的 OpenAI 兼容服务列表：https://docs.litellm.ai/docs/providers

### PyTorch MPS 加速
- **用在哪**：`embedder.py` / `reranker.py`（`torch.backends.mps.is_available()`）。
- **关键概念**：MPS 后端、注意力显存随序列平方增长（ADR-0006 预算制分批的直接原因）。
- **参考**：
  - PyTorch MPS 后端文档：https://pytorch.org/docs/stable/notes/mps.html

## 5. 评测与统计检验

### 检索评测指标
- **用在哪**：`evals/run_eval.py`（recall@k、MRR@5、按难度分层）。
- **参考**：
  - 检索指标综述（recall/MRR/NDCG）：https://en.wikipedia.org/wiki/Evaluation_measures_(information_retrieval)
  - 《信息检索导论》第 8 章评价：https://nlp.stanford.edu/IR-book/html/htmledition/evaluation-in-information-retrieval-1.html

### McNemar 配对检验
- **用在哪**：`evals/compare.py`（精确二项检验，只看翻转题）。
- **关键概念**：配对样本检验、discordant pairs、精确 p 值在小样本（100 题）下比卡方近似更可靠。
- **参考**：
  - McNemar 检验（Wikipedia）：https://en.wikipedia.org/wiki/McNemar%27s_test
  - Dietterich《Approximate Statistical Tests for Comparing Supervised Classification Learning Algorithms》（1998，比较 ML 系统的经典论文）：https://direct.mit.edu/neco/article/10/7/1895/6223

## 6. 数据工程与系统

### 增量同步（内容寻址）
- **用在哪**：`sync.py`（SHA-1 内容哈希比对、逐文件提交、断点续跑、三存储联动删除）。
- **关键概念**：内容寻址 vs mtime 寻址（mtime 会被 git checkout / 编辑器触碰而失真）；write-ahead 逐文件提交。

### 图算法（任务依赖图）
- **用在哪**：`specs/task_graph.py`（拓扑状态传播、可执行判定、并行组 = 可执行节点集）。
- **关键概念**：DAG 可达性、"可执行 = 有未完成任务且全部前置完成"；构造保证并行组内两两无依赖。
- **参考**：
  - NetworkX 图算法库（项目依赖，备用）：https://networkx.org/documentation/stable/
  - 拓扑排序（Wikipedia）：https://en.wikipedia.org/wiki/Topological_sorting

### 配置与 CLI
- **用在哪**：`projects.yaml`（知识源登记）、六个 argparse CLI 入口。
- **参考**：
  - PyYAML 文档：https://pyyaml.org/wiki/PyYAMLDocumentation
  - argparse 文档：https://docs.python.org/3/library/argparse.html

## 7. Git 自动化
- **用在哪**：`impact_analysis.py` / `sync.py`（`git log --since` / `diff --name-only` / `status --porcelain` / `rev-parse --short HEAD`）。
- **关键概念**：porcelain 格式解析（脚本应使用 porcelain 而非面向人类的格式）；`--pretty=format:` 空格式只取文件名。
- **参考**：
  - Git 官方文档（git-log / git-status / git-diff）：https://git-scm.com/docs
  - 《Pro Git》免费在线版：https://git-scm.com/book/zh/v2

## 相关文档

- [架构总览](./architecture.md)
- [技术决策记录（ADR）](./decisions.md)
