"""查询扩展缓存预生成：对评测用例逐题调用 LLM 生成英文候选词，落盘缓存、可断点续跑。

用法：PYTHONPATH=src .venv/bin/python evals/pregen_expansions.py
"""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_app.llm.query_expansion import expand, _load_cache  # noqa: E402

cases = [json.loads(l) for l in (ROOT / "evals/cases_v1.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
t0 = time.time()
done = 0
for c in cases:
    if time.time() - t0 > 220:
        break
    q = c["question"]
    if q in _load_cache():
        continue
    terms = expand(q)
    done += 1
    print(f"{c['id']} {q} -> {terms}", flush=True)

cached = sum(1 for c in cases if c["question"] in _load_cache())
print(f"本轮生成 {done} 题，缓存覆盖 {cached}/{len(cases)}")
