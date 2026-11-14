"""基线对比：同题集校验 + 指标差值 + McNemar 配对精确检验。

题集不一致会拒绝比较；旧结果缺少元数据、索引或实现发生变化会明确告警。
用法：.venv/bin/python evals/compare.py <baseline.json> <new-run.json>
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path


def mcnemar(a: list[bool], b: list[bool]) -> dict:
    """a=基线命中, b=新结果命中。返回配对精确检验 p 值。"""
    only_b = sum(1 for x, y in zip(a, b) if not x and y)
    only_a = sum(1 for x, y in zip(a, b) if x and not y)
    n = only_a + only_b
    if n == 0:
        p = 1.0
    else:
        tail = sum(math.comb(n, i) for i in range(0, min(only_a, only_b) + 1))
        p = min(1.0, 2 * tail / 2 ** n)
    return {"regressed": only_a, "improved": only_b, "discordant": n,
            "p_exact": round(p, 4)}


def validated_case_ids(base: dict, new: dict) -> list[str]:
    """比较必须覆盖完全相同的题集，不能用交集掩盖漏题或换题。"""
    base_ids = {row["id"] for row in base["rows"]}
    new_ids = {row["id"] for row in new["rows"]}
    if base_ids != new_ids:
        missing = sorted(base_ids - new_ids)
        extra = sorted(new_ids - base_ids)
        raise ValueError(f"题集 ID 不一致：新结果缺少={missing}，新增={extra}")
    base_hash = base.get("cases", {}).get("sha256")
    new_hash = new.get("cases", {}).get("sha256")
    if base_hash and new_hash and base_hash != new_hash:
        raise ValueError("题集内容 SHA-256 不一致，拒绝直接比较")
    return sorted(base_ids)


def compatibility_warnings(base: dict, new: dict) -> list[str]:
    warnings = []
    for label, run in (("基线", base), ("新结果", new)):
        missing = [key for key in ("cases", "config", "implementation", "index") if key not in run]
        if missing:
            warnings.append(f"{label}缺少元数据 {missing}，属于旧格式，复现能力有限")
    base_index = base.get("index", {}).get("sha256")
    new_index = new.get("index", {}).get("sha256")
    if base_index and new_index and base_index != new_index:
        warnings.append("索引内容指纹不同：指标变化可能来自语料或索引变化")
    base_impl = base.get("implementation", {}).get("sources_sha256")
    new_impl = new.get("implementation", {}).get("sources_sha256")
    if base_impl and new_impl and base_impl != new_impl:
        warnings.append("检索实现指纹不同：请结合源码变更解释指标变化")
    return warnings


def config_differences(base: dict, new: dict) -> list[str]:
    base_config = base.get("config", {})
    new_config = new.get("config", {})
    differences = []
    for key in sorted(set(base_config) | set(new_config)):
        before = base_config.get(key, "<未记录>")
        after = new_config.get(key, "<未记录>")
        if before != after:
            differences.append(f"{key}: {before} → {after}")
    return differences


def main() -> int:
    if len(sys.argv) != 3:
        print("用法：compare.py <baseline.json> <new-run.json>")
        return 2
    base = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    new = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    try:
        common = validated_case_ids(base, new)
    except ValueError as exc:
        print(str(exc))
        return 1
    if not common:
        print("两次运行没有共同用例，无法对比")
        return 1

    b_rows = {r["id"]: r for r in base["rows"]}
    n_rows = {r["id"]: r for r in new["rows"]}
    print(f"基线 {base['run']}  vs  新结果 {new['run']}  （完全相同题集 {len(common)} 题）")
    for warning in compatibility_warnings(base, new):
        print(f"⚠️ {warning}")
    differences = config_differences(base, new)
    if differences:
        print("配置差异：" + "；".join(differences))

    print(f"\n{'指标':12s} {'基线':>8s} {'新结果':>8s} {'差值':>8s}")
    for k in ("recall@1", "recall@3", "recall@5", "mrr@5"):
        bv, nv = base["metrics"].get(k, 0), new["metrics"].get(k, 0)
        print(f"{k:12s} {bv:8.4f} {nv:8.4f} {nv - bv:+8.4f}")

    mc = mcnemar([b_rows[i]["hit5"] for i in common],
                 [n_rows[i]["hit5"] for i in common])
    print(f"\nMcNemar（recall@5）：恶化 {mc['regressed']} 题 / 改善 {mc['improved']} 题，"
          f"p={mc['p_exact']}")

    regressed = [i for i in common if b_rows[i]["hit5"] and not n_rows[i]["hit5"]]
    improved = [i for i in common if not b_rows[i]["hit5"] and n_rows[i]["hit5"]]
    if regressed:
        print(f"恶化题目：{regressed}")
        for i in regressed:
            print(f"  {i}: {n_rows[i]['question']}")
    if improved:
        print(f"改善题目：{improved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
