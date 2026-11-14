---
状态: 稳定
最后更新: 2026-11-14
下次复核: 2027-11-14
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

## 当前基线复现

```bash
# 当前 baseline-v7 的完整配置
HF_HUB_OFFLINE=1 PYTHONPATH=src .venv/bin/python evals/run_eval.py \
  --cases evals/cases_v1.jsonl --name baseline-v7 \
  --rerank --blend 0.3 --expand

# 新实验必须与冻结基线使用相同题集
HF_HUB_OFFLINE=1 PYTHONPATH=src .venv/bin/python evals/run_eval.py \
  --cases evals/cases_v1.jsonl --name my-experiment \
  --rerank --blend 0.3 --expand

PYTHONPATH=src .venv/bin/python evals/compare.py \
  evals/baselines/baseline-v7.json evals/results/my-experiment.json
```

`compare.py` 对题集 ID 或内容 SHA-256 不一致的运行拒绝直接比较；旧结果缺少元数据、索引变化、实现变化会明确告警。检索参数差异会展示，不会被误当作不兼容。

## baseline-v7（100 题）

| 指标 | 值 | 分层 | r@1 | r@5 |
|---|---:|---|---:|---:|
| recall@1 | 0.780 | literal (34) | 1.00 | 1.00 |
| recall@3 | 0.860 | semantic (35) | 0.54 | 0.89 |
| recall@5 | 0.960 | doc (20) | 0.80 | 1.00 |
| MRR@5 | 0.8413 | cross (11) | 0.82 | 1.00 |

相对旧 baseline-v6：recall@1 +0.01、recall@5 +0.01、MRR@5 +0.0091；McNemar 翻转题为改善 1、恶化 0，改善 q080。

### v7 采纳与拒绝项

- **采纳：完整复合扩展词的符号子串通道**（权重 0.6、limit 3）。`report_json` 可挂接 `print_report_json`；纯单词不启用，避免 `migrations` 跨驼峰边界误中 `MapMigrationStore`。
- **拒绝：弱扩展候选保留**。虽然让 q044/q076/q078 进入精排，但没有转化为 Top-5 收益，还引入噪声。
- **拒绝：新增“数据库”路径提示**。未修复 q057，且干扰其他查询。
- **索引污染修复**：`.pytest_cache`、`cases_*.jsonl`、`routing_cases_*.jsonl`、冻结基线与评测结果不得进入知识索引，避免跨项目检索命中题目或答案自身。

旧 baseline-v6 保留为历史记录，缺少新版元数据，不再作为新实验的对比入口。

## 独立验证与诊断

```bash
# 配置冻结后运行；不得根据本结果反复调参后继续称其为独立验证集
HF_HUB_OFFLINE=1 PYTHONPATH=src .venv/bin/python evals/run_eval.py \
  --cases evals/cases_holdout.jsonl --name holdout-v7 \
  --rerank --blend 0.3 --expand

# 历史语义失败的通道诊断
HF_HUB_OFFLINE=1 PYTHONPATH=src .venv/bin/python evals/run_eval.py \
  --cases evals/cases_semantic_failures.jsonl --name semantic-diagnostics \
  --rerank --blend 0.3 --expand

# 项目路由，不加载模型
PYTHONPATH=src .venv/bin/python evals/run_routing_eval.py
```

每条检索结果记录目标候选块、扩展词、召回通道及排名、融合排名和最终排名。失败分为：

- `gold_not_indexed`：目标未进入索引。
- `not_retrieved`：目标未进入任何召回通道。
- `filtered_before_rerank`：目标被召回，但未进入融合候选池。
- `reranked_out`：目标进入融合候选池，但最终掉出 Top-5。

v7 剩余 4 个未命中：q044/q076/q078 为 `filtered_before_rerank`，q057 为 `not_retrieved`。先依据这些证据修召回或候选保留，不再无差别叠加权重。

## 路由基线

当前 12 题结果：明确目标项目的 8 题中自动覆盖 3 题（37.5%），其余 5 题安全地要求澄清；错误路由 0，歧义保护 100%。后续提高覆盖率时必须保持“项目不明确不隐式全局搜索”。

## 调参流程

1. 只用开发题集和守护题做实验，不查看独立验证集结果来选择参数。
2. 跑完整 100 题并对比当前冻结基线，检查逐题翻转与失败诊断。
3. recall@5 不允许显著退化；任何净退化方案默认拒绝，即使个别题改善。
4. 采纳后生成带完整元数据的新基线，同步本文档数值。
5. 最后运行一次独立验证集并如实记录；若还需针对其结果调参，应另建新的验证题。

## 相关文档

- [架构总览](../docs/architecture.md)
- [技术决策记录（ADR）](../docs/decisions.md)
