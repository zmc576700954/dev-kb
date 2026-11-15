# dev-kb — 多项目研发知识库与开发协作 Agent

> 对本地多个研发项目（Laravel / TypeScript / Rust / Python）建立统一索引，回答"这段代码在哪、怎么实现的、文档该不该更新、下一步该做什么"这类问题。所有回答的证据（代码段）都实时读取自原文件并附路径行号，可人工复核。

## 它能做什么

| 能力 | 入口 | 说明 |
|---|---|---|
| 知识源扫描 | `python -m agent_app.scan_cli` | 只读扫描登记的知识源，分类文件、识别技术栈、输出报告 |
| 增量索引 | `python -m agent_app.sync_cli` | SHA-1 哈希比对 → 切块 → SQLite + Qdrant 入库 |
| 证据问答 | `python -m agent_app.ask_cli "<问题>"` | 混合检索 + 回读原文件 + LLM 归纳，逐条引用 |
| 只读 MCP | `python -m agent_app.mcp_server` | stdio 工具：`index_status` / `search` / `read_source` |
| 知识源接入契约 | `python -m agent_app.contract.cli` | 校验知识源声明与结构能力（7 条规则），只读 |

## 知识源接入契约

契约解决的是**接入面随知识源类型增长而失控**的问题：每多一种库就要在检索器里写一个特例，改一个动全部。

契约规定每个知识源**提供哪些结构字段**、**内容边界在哪**、**中文意图对应哪些路径**，检索器按声明消费；声明里没有的字段一律降级处理，**不拒绝接入**。新增一种知识源不需要新增任何枚举或特例。

声明写在 `projects.yaml` 的对应条目里（注册表只放契约，调优表另放 `tuning/<source>.yaml`）：

```yaml
projects:
  - name: ai-video-kb
    path: ~/ai-video-kb
    provides: [heading_path, category, doc_role, aliases]   # 声明提供的结构字段
    content_roots: ["."]                                    # 内容边界（必须显式声明）
    entry_docs: [README.md]                                 # 入口文档
    tuning: [tuning/ai-video-kb.yaml]                       # 别名 / 意图映射 / 文档角色
```

契约字段在索引侧有对应落点：`chunks` 表的 `category` / `doc_role` / `aliases` 三列与 `heading_path` 一起构成统一 chunk schema，检索器据此做标题链召回、术语挂接与意图路径提示。

规则共 7 条，全部可机械判定，只针对"检索能不能用"，不评判文字质量：

| ID | 规则 | 级别 |
|---|---|---|
| R-01 | 内容边界必须显式声明 | error |
| R-02 | 声明的结构能力必须真实存在（标题链覆盖率 ≥ 95%） | error |
| R-03 | 意图映射的目标必须存在 | error |
| R-04 | 入口文档必须存在 | error |
| R-05 | 文档块应有标题链 | warning |
| R-06 | 术语别名必须有出处 | warning |
| R-07 | 声明不得超出契约字段集 | error |

完整口径见 [知识源接入契约](./docs/00-知识源契约.md)。

## 快速开始

配置含本机项目路径，属本地状态，不随仓库分发。仓库只提供模板副本，先复制一份并填入实际路径：

```bash
cp projects.example.yaml projects.yaml
```

`path` 支持 `~` 与环境变量（如 `$HOME/xxx`）。配置也可放在仓库外，用环境变量指定：

```bash
export DEV_KB_PROJECTS=~/my-configs/projects.yaml
```

```bash
# 索引与问答
PYTHONPATH=src .venv/bin/python -m agent_app.sync_cli --stats
PYTHONPATH=src .venv/bin/python -m agent_app.ask_cli "tiangong 的 canTransition 在哪定义"

# 问题中没有唯一项目名时，显式指定项目或全项目范围
PYTHONPATH=src .venv/bin/python -m agent_app.ask_cli "canTransition 在哪定义" --project tiangong
PYTHONPATH=src .venv/bin/python -m agent_app.ask_cli "数据库迁移文件在哪" --all-projects

# 只读 MCP stdio 服务
HF_HUB_OFFLINE=1 PYTHONPATH=src .venv/bin/python -m agent_app.mcp_server

# 知识源接入契约：查声明 / 查规则 / 校验
PYTHONPATH=src .venv/bin/python -m agent_app.contract.cli sources
PYTHONPATH=src .venv/bin/python -m agent_app.contract.cli rules
PYTHONPATH=src .venv/bin/python -m agent_app.contract.cli validate ai-video-kb
```

MCP `search` 只有在问题中唯一识别项目名时才自动限定项目；否则返回候选项目。必须显式提供 `project`，或设置 `all_projects=true` 才会进行跨项目搜索。`read_source` 只接受项目内相对路径，并阻止目录越界。

环境依赖见 `requirements.lock`；BGE-M3 首次运行从 HF 镜像下载约 2.3 GB。评测与复现命令见 `evals/README.md`。

## docs

- [知识源接入契约](./docs/00-知识源契约.md) — 契约的目标、声明模型、7 条规则与扩展方式。
- [架构总览](./docs/architecture.md) — 模块划分、三条主数据流与存储设计。
- [技术决策记录（ADR）](./docs/decisions.md) — 按编号索引的全部决策，含已废弃条目标记。
- [技术决策记录（一）：索引与检索](./docs/01-索引与检索决策.md) — 存储选型、切块策略、混合检索与评测调参。
- [技术决策记录（二）：分层与写入门禁](./docs/02-分层与写入门禁决策.md) — 事实与推断分层、LLM 后端与提案闭环（提案部分已废弃）。
- [技术决策记录（三）：规范与工程化](./docs/03-规范与工程化决策.md) — 知识库规范时期的工程化决策（整体已废弃）。
- [知识点地图](./docs/knowledge-map.md) — 涉及的知识领域、关键概念与一手参考文档。

## evals

- [检索质量回归评测](./evals/README.md) — 用例集构成、命中判定口径、基线复现命令与参数调优规则。

## 变更记录

- [演进时间线](./CHANGELOG.md) — 里程碑与关键修改历史，含 2026-11-15 的方向修正。
