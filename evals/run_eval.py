"""检索质量回归评测：完整运行元数据 + 逐题召回/排序诊断。

判定：Top-k 证据中任一条的 path 或 symbol 包含 gold.any_of 中任一关键词即命中。
输出：evals/results/<run-name>.json（含配置指纹、逐题排名与失败诊断）。

用法：
    PYTHONPATH=src .venv/bin/python evals/run_eval.py [--cases evals/cases_v1.jsonl] [--name run1]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from eval_metadata import (  # noqa: E402
    cases_metadata,
    implementation_metadata,
    index_metadata,
    retrieval_metadata,
)
from agent_app.indexing.metadata_store import MetadataStore  # noqa: E402
from agent_app.indexing.vector_store import VectorStore  # noqa: E402
from agent_app.tools.retrieval import HybridSearch  # noqa: E402

K = (1, 3, 5)


def hit_rank(results: list, any_of: list[str]) -> int | None:
    """返回首个命中证据的排名（1 起），未命中返回 None。"""
    for i, r in enumerate(results[: max(K)], 1):
        text = f"{r['path']} {r.get('symbol') or ''}"
        if any(kw in text for kw in any_of):
            return i
    return None


def failure_reason(gold_candidates: list[dict], diagnostics: dict, rank: int | None) -> str | None:
    """区分未收录、未召回、融合阶段截断和重排掉出 Top-5。"""
    if rank is not None:
        return None
    if not gold_candidates:
        return "gold_not_indexed"
    gold_ids = {item["chunk_id"] for item in gold_candidates}
    if not gold_ids.intersection(diagnostics.get("candidate_ids", [])):
        return "not_retrieved"
    if not gold_ids.intersection(diagnostics.get("fusion_ids", [])):
        return "filtered_before_rerank"
    return "reranked_out"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="evals/cases_v1.jsonl")
    ap.add_argument("--name", default=None, help="结果文件名（默认按时间戳）")
    ap.add_argument("--rerank", action="store_true", help="启用 bge-reranker 精排")
    ap.add_argument("--blend", type=float, default=0.5, help="重排分权重（1=纯重排，0=纯融合）")
    ap.add_argument("--expand", action="store_true", help="启用 LLM 查询扩展（先跑 evals/pregen_expansions.py 预热缓存）")
    ap.add_argument("--code-boost", type=float, default=None, help="代码定位意图时代码块加权（默认用检索器配置）")
    ap.add_argument("--doc-damp", type=float, default=None, help="代码定位意图时文档块加权（默认用检索器配置）")
    args = ap.parse_args()

    cases_path = ROOT / args.cases
    cases = [
        json.loads(line)
        for line in cases_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    store = MetadataStore(str(ROOT / "data/meta.db"))
    kw = {"rerank": args.rerank, "blend": args.blend, "expand": args.expand}
    if args.code_boost is not None:
        kw["code_boost"] = args.code_boost
    if args.doc_damp is not None:
        kw["doc_damp"] = args.doc_damp
    engine = HybridSearch(store, VectorStore(str(ROOT / "data/qdrant")), **kw)

    rows = []
    t0 = time.time()
    for c in cases:
        diagnostics: dict = {}
        results = engine.search(
            c["question"], project=c["project"], limit=max(K), diagnostics=diagnostics
        )
        rank = hit_rank(results, c["gold"]["any_of"])
        gold_candidates = store.find_candidate_chunks(
            c["project"], c["gold"]["any_of"]
        )
        fusion_ranks = {
            chunk_id: index
            for index, chunk_id in enumerate(diagnostics.get("fusion_ids", []), 1)
        }
        for gold in gold_candidates:
            chunk_id = gold["chunk_id"]
            gold["retrieval"] = {
                "matched_by": diagnostics.get("candidate_sources", {}).get(chunk_id, []),
                "channel_ranks": {
                    source: ranks[chunk_id]
                    for source, ranks in diagnostics.get("channel_ranks", {}).items()
                    if chunk_id in ranks
                },
                "fusion_rank": fusion_ranks.get(chunk_id),
            }
        rows.append({
            "id": c["id"],
            "project": c["project"],
            "difficulty": c["difficulty"],
            "question": c["question"],
            "rank": rank,
            "hit5": rank is not None,
            "failure_reason": failure_reason(gold_candidates, diagnostics, rank),
            "gold_candidates": gold_candidates,
            "diagnostics": {
                "code_seeking": diagnostics.get("code_seeking", False),
                "expanded_terms": diagnostics.get("expanded_terms", []),
                "candidate_count": len(diagnostics.get("candidate_ids", [])),
            },
            "top_results": [
                {
                    "chunk_id": r["chunk_id"],
                    "path": r["path"],
                    "symbol": r.get("symbol") or "",
                    "matched_by": r.get("matched_by", []),
                    "score": r["score"],
                    "rerank_score": r.get("rerank_score"),
                    "fusion_rank": fusion_ranks.get(r["chunk_id"]),
                }
                for r in results
            ],
        })

    def recall_at(k: int, subset: list[dict]) -> float:
        n = sum(1 for r in subset if r["rank"] is not None and r["rank"] <= k)
        return round(n / len(subset), 4) if subset else 0.0

    mrr = sum(1.0 / r["rank"] for r in rows if r["rank"]) / len(rows)
    metrics = {
        f"recall@{k}": recall_at(k, rows) for k in K
    } | {"mrr@5": round(mrr, 4), "n": len(rows)}

    by_diff = {}
    for d in sorted({r["difficulty"] for r in rows}):
        sub = [r for r in rows if r["difficulty"] == d]
        by_diff[d] = {f"recall@{k}": recall_at(k, sub) for k in K} | {"n": len(sub)}

    config, models = retrieval_metadata(args, engine)
    out = {
        "run": args.name or datetime.now().strftime("%Y%m%d-%H%M%S"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "cases_file": args.cases,
        "cases": cases_metadata(cases_path, cases),
        "config": config,
        "models": models,
        "implementation": implementation_metadata(),
        "index": index_metadata(store),
        # 保留旧字段，兼容已有结果查看脚本。
        "rerank": args.rerank,
        "blend": args.blend if args.rerank else None,
        "expand": args.expand,
        "code_boost": engine.code_boost,
        "doc_damp": engine.doc_damp,
        "elapsed_s": round(time.time() - t0, 1),
        "metrics": metrics,
        "by_difficulty": by_diff,
        "rows": rows,
    }
    path = ROOT / "evals/results" / f"{out['run']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"用例 {len(rows)} 题，耗时 {out['elapsed_s']}s")
    print(f"recall@1={metrics['recall@1']}  recall@3={metrics['recall@3']}  "
          f"recall@5={metrics['recall@5']}  MRR@5={metrics['mrr@5']}")
    for d, m in by_diff.items():
        print(f"  [{d:9s}] n={m['n']:3d}  r@1={m['recall@1']:.2f}  r@5={m['recall@5']:.2f}")
    fails = [r for r in rows if not r["hit5"]]
    if fails:
        print(f"未命中 {len(fails)} 题：{[r['id'] for r in fails]}")
        for row in fails:
            print(f"  {row['id']}: {row['failure_reason']}")
    print(f"索引指纹 {out['index']['sha256'][:12]}，实现指纹 "
          f"{out['implementation']['sources_sha256'][:12]}")
    print(f"结果已写入 {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
