# dev-kb — 多项目研发知识库与开发协作 Agent

> 对本地多个研发项目（Laravel / TypeScript / Rust / Python）建立统一索引，回答“这段代码在哪、怎么实现的、文档该不该更新、下一步该做什么”这类问题。所有回答的证据（代码段）都实时读取自原文件并附路径行号，可人工复核。

## 它能做什么

| 能力 | 入口 | 说明 |
|---|---|---|
| 知识源扫描 | `python -m agent_app.scan_cli` | 只读扫描登记项目，分类文件、识别技术栈、输出报告（M1） |
| 增量索引 | `python -m agent_app.sync_cli` | SHA-1 哈希比对 → tree-sitter 切块 → SQLite + Qdrant 入库（M2） |
| 证据问答 | `python -m agent_app.ask_cli "<问题>"` | 混合检索 + 回读原文件 + LLM 归纳，逐条引用（M3） |
| 只读 MCP | `python -m agent_app.mcp_server` | stdio 工具：`index_status` / `search` / `read_source`，不暴露同步或写入 |
| Spec 进度 | `python -m agent_app.spec_cli progress <项目>` | 从 specs/tasks/plans 文档解析任务图，计算可并行工作流（M4） |
| 项目画像 | `python -m agent_app.profile_cli <项目>` | 技术栈事实（带来源）、目录结构、入口与关键文档（M5） |
| 文档维护闭环 | `python -m agent_app.docs_cli analyze <项目>` | Git 变更 → 受影响文档 → 提案 → 人工审批 → 写入（M6） |
| 知识库规范 MCP | `python -m agent_app.kb_spec.mcp_server` | stdio 服务：18 个工具 + 6 个 prompt 模板，规范校验、查重、骨架生成、提案式写入（M7） |
| 知识库规范 CLI | `python -m agent_app.kb_spec.cli` | 人工入口：校验、查重、提案审批与落盘 |

已登记的知识源见 `projects.yaml`（五个本地项目 + dev-kb 自举索引自身）。

## 快速开始

```bash
# 索引与问答
PYTHONPATH=src .venv/bin/python -m agent_app.sync_cli --stats
PYTHONPATH=src .venv/bin/python -m agent_app.ask_cli "tiangong 的 canTransition 在哪定义"

# 问题中没有唯一项目名时，显式指定项目或全项目范围
PYTHONPATH=src .venv/bin/python -m agent_app.ask_cli "canTransition 在哪定义" --project tiangong
PYTHONPATH=src .venv/bin/python -m agent_app.ask_cli "数据库迁移文件在哪" --all-projects

# 只读 MCP stdio 服务
HF_HUB_OFFLINE=1 PYTHONPATH=src .venv/bin/python -m agent_app.mcp_server

# 知识库规范 MCP stdio 服务
PYTHONPATH=src .venv/bin/python -m agent_app.kb_spec.mcp_server

# 知识库规范人工入口（校验 / 提案审批）
PYTHONPATH=src .venv/bin/python -m agent_app.kb_spec.cli validate ai-video-kb
PYTHONPATH=src .venv/bin/python -m agent_app.kb_spec.cli proposals list
```

MCP `search` 只有在问题中唯一识别项目名时才自动限定项目；否则返回候选项目。必须显式提供 `project`，或设置 `all_projects=true` 才会进行跨项目搜索。`read_source` 只接受项目内相对路径，并阻止目录越界。

环境依赖见 `requirements.lock`；BGE-M3 首次运行从 HF 镜像下载约 2.3 GB。评测与复现命令见 `evals/README.md`。

## docs

- [架构总览](./docs/architecture.md) — 本文说明 dev-kb 的模块划分、三条主数据流与存储设计，逐层给出职责边界与不可违反的硬约束。
- [技术决策记录（ADR）](./docs/decisions.md) — 本文按时间顺序记录 dev-kb 的技术决策，每条说明背景、决策内容、理由与代价，是改动前必读的判断依据。
- [技术决策记录（一）：索引与检索](./docs/01-索引与检索决策.md) — 本文记录存储选型、切块策略、混合检索与评测调参的决策，覆盖 M2–M3 与 v3–v6 演进。
- [技术决策记录（二）：分层与写入门禁](./docs/02-分层与写入门禁决策.md) — 本文记录事实与推断分层、LLM 后端选型、文档提案闭环与 MCP 只读门禁的决策。
- [技术决策记录（三）：规范与工程化](./docs/03-规范与工程化决策.md) — 本文记录规范修正、批量策略、server 健壮性与范围限定。
- [知识点地图](./docs/knowledge-map.md) — 本文汇总项目涉及的知识领域，每条给出用在哪、关键概念与一手参考文档，便于向外查证与深化理解。

## evals

- [检索质量回归评测](./evals/README.md) — 本文说明检索质量的回归评测方法：用例集构成、命中判定口径、基线复现命令与参数调优规则。

## 变更记录

- [演进时间线](./CHANGELOG.md) — M1→M7 里程碑与关键修改历史
