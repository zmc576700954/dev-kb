---
状态: 稳定
最后更新: 2026-11-15
下次复核: 2027-11-15
---

# 检索质量回归评测

> 本文说明检索质量的回归评测方法：用例集构成、命中判定口径、基线复现命令与参数调优规则。

方法论：JSONL 用例集 + recall@k/MRR + McNemar 配对检验。所有新运行记录完整配置、模型、源码指纹、索引内容指纹和题集指纹，不依赖 Git。

## 用例组成

- `cases_v1.jsonl` — 100 题开发/回归题集，字段：`id / project / difficulty / question / gold.any_of`。
- `cases_holdout.jsonl` — 12 题独立验证集，未参与 v1–v7 的参数选择与拒绝实验；只在冻结配置后验收。
- `cases_semantic_failures.jsonl` — 5 个历史语义失败题，用于通道诊断。
- `cases_expansion_guard.jsonl` — 5 个扩展噪声与自我引用守护题。
- `routing_cases_v1.jsonl` — 12 个项目路由案例，覆盖唯一识别、未命名项目、歧义与显式全项目搜索。

检索判定：Top-5 证据的 path 或 symbol 包含 `gold.any_of` 任一关键词即命中。路由评测单独计算项目识别，不把检索项目参数当作路由能力。

## 通道权重的归因方法

契约带来四个新机制（标题链独立通道、术语别名通道、意图映射路径提示、结构可信度降权）。每个都能用环境变量单独开关，便于**逐通道归因**——这是本项目一贯的做法（ADR-0008）：

| 环境变量 | 含义 | 默认 |
|---|---|---|
| `DEV_KB_HEADING_WEIGHT` | 标题链独立通道权重（0 = 关闭） | 0.7 |
| `DEV_KB_HEADING_FALLBACK` | 标题链通道是否允许 OR 宽松降级（0 = 只用 AND 精确匹配） | 1 |
| `DEV_KB_ALIAS_WEIGHT` | 术语别名通道权重（0 = 关闭） | 1.0 |
| `DEV_KB_INTENT_HINT_WEIGHT` | 意图映射路径提示权重（0 = 关闭） | 0.7 |
| `DEV_KB_STRUCTURE_TRUST` | 结构可信度降权系数（0 = 不降权） | 0.7 |

只改一个变量、其余置 0，即可测出该通道单独的贡献。**这是本轮改动能定位到四个 bug 的原因**——例如标题链通道最初因为缺 AND→OR 降级而一次都没命中，看起来"无害"，实际上是没在工作。

## 当前基线复现

```bash
# 冻结基线的完整配置
HF_HUB_OFFLINE=1 PYTHONPATH=src .venv/bin/python evals/run_eval.py \
  --cases evals/cases_v1.jsonl --name baseline-v8 \
  --rerank --blend 0.3 --expand

# 新实验必须与冻结基线使用相同题集
HF_HUB_OFFLINE=1 PYTHONPATH=src .venv/bin/python evals/run_eval.py \
  --cases evals/cases_v1.jsonl --name my-experiment \
  --rerank --blend 0.3 --expand

PYTHONPATH=src .venv/bin/python evals/compare.py \
  evals/baselines/baseline-v8.json evals/results/my-experiment.json
```

`compare.py` 对题集 ID 或内容 SHA-256 不一致的运行拒绝直接比较；旧结果缺少元数据、索引变化、实现变化会明确告警。检索参数差异会展示，不会被误当作不兼容。

评测实验产物不入库（`.gitignore` 忽略 `evals/results/`），只保留冻结基线与独立验证集结果。复现命令即上。

## baseline-v8（100 题，2026-11-15）

本轮把"知识库规范"改成"知识源接入契约"后，索引与检索都变了，因此重新冻结基线。逐通道归因结果见下表——每行只开一个通道，其余置 0。

| 配置 | recall@1 | recall@3 | recall@5 | MRR@5 | 结论 |
|---|---:|---:|---:|---:|---|
| baseline-v7（改造前） | 0.780 | 0.860 | 0.960 | 0.8413 | 对照 |
| 仅索引变化（新通道全关） | 0.790 | 0.860 | 0.950 | 0.8422 | 持平——标题链修复本身无退化 |
| 标题链通道（OR 宽松降级） | 0.770 | 0.840 | 0.930 | 0.8217 | **退化**：宽松降级捞进弱候选，挤掉正确答案 |
| 标题链通道（AND 精确匹配） | 0.800 | 0.860 | 0.950 | 0.8488 | 采纳——"问父标题命中子节"要的就是精度 |
| 术语通道（文档侧匹配） | 0.760 | 0.830 | 0.920 | 0.8122 | **退化**：按路径长度取前 N 是任意排序 |
| 术语通道（查询侧扩展） | 0.790 | 0.860 | 0.950 | 0.8422 | 持平——机制正确，但评测题未覆盖别名问法 |
| 意图映射路径提示（仅 ai-video-kb 声明） | 0.800 | 0.870 | 0.960 | 0.8522 | **采纳**：单独测里唯一带来改进的通道 |
| 结构可信度降权 | 0.790 | 0.860 | 0.950 | 0.8422 | 无变化——回填后所有文档块都有标题链，降权不触发 |
| **baseline-v8（冻结组合）** | **0.800** | **0.870** | **0.960** | **0.8527** | 对 baseline-v7：r@1 +0.02、r@3 +0.01、MRR +0.0114，McNemar **恶化 0 / 改善 0** |

**冻结配置**（即代码默认值）：`HEADING_WEIGHT=0.7` + `HEADING_FALLBACK=0`（只用 AND 精确匹配）+ `ALIAS_WEIGHT=1.0`（查询侧扩展）+ `INTENT_HINT_WEIGHT=0.7` + `STRUCTURE_TRUST=0.7`。

四条结论：

1. **标题链通道的宽松降级是退化来源，精确匹配是价值来源。** 不能因为降级有害就砍掉整条通道——"问『核心架构』命中『### 模块』这种父标题命中子节"正是 AND 精确匹配的能力。
2. **术语通道必须做查询侧扩展。** 文档侧匹配（按路径长度取前 N）是任意排序，实测 r@5 -0.04；改成"别名 → canonical 词，再交给正常通道排序"后不再伤害，且保留了"用户自定义术语表改善检索"这条通路。评测题里没有别名问法，所以看不到收益也看不到损失——这是**机制正确但题集未覆盖**，不是"没用"。
3. **意图映射是本轮最有价值的机制**，把"中文意图 → 本库路径"从全局硬编码表搬到了知识源声明里。
4. **全局硬编码路径表已被彻底取代并删除。** 取代它花了三轮：先按源写声明（比全局表差，r@1 -0.04），再叠加（更差，稀释），最后发现关键差别——全局表用的是**短片段**（`api`、`Auth`、`Middleware`），能同时匹配到**文件名**（`ApiController.php`），而按目录写的映射匹配不到。把全局表的条目按源分发、逐条用本库实际内容过滤后，per-source 声明全面不劣于全局表（r@3 +0.01、MRR +0.0014、McNemar 零退化），全局表随即删除。

**已知未命中**：q044 / q057 / q076 / q078（判定为真实检索短板，非评测噪声）。

## 索引重建

契约字段与标题链都落在索引里，因此改了切块器或声明之后要重建：

```bash
# 按声明回填契约字段 + 标题链（不重扫源文件、不重算向量）
PYTHONPATH=src .venv/bin/python -m agent_app.sync_cli --backfill

# 重建全文索引与标题链索引（不重算向量）
PYTHONPATH=src .venv/bin/python -m agent_app.sync_cli --rebuild-fts

# 补挂未生成向量的块
PYTHONPATH=src .venv/bin/python -m agent_app.sync_cli --embed-pending
```

## 旧基线

baseline-v7 及更早的结果文件不再入库（实验产物可重新生成），历史数据见 [CHANGELOG](../CHANGELOG.md) 的基线演进表与 [ADR-0007 / ADR-0008](./../docs/01-索引与检索决策.md)。
