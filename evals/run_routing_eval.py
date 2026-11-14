"""项目路由评测：衡量唯一识别覆盖率、错误路由与歧义保护，不加载模型。"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_app.tools.projects import load_registry  # noqa: E402
from agent_app.tools.routing import route_project  # noqa: E402


def main() -> int:
    cases_path = ROOT / "evals/routing_cases_v1.jsonl"
    cases = [json.loads(line) for line in cases_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    registry = load_registry(ROOT / "projects.yaml")
    rows = []
    for case in cases:
        route = route_project(
            case["question"], registry,
            project=case.get("project_arg"),
            all_projects=case.get("all_projects", False),
        )
        expected = case["expected_project"]
        predicted = route["project"] if route["status"] == "ok" else None
        expected_candidates = case.get("expected_candidates")
        candidates_ok = (
            expected_candidates is None
            or sorted(route.get("candidates", [])) == sorted(expected_candidates)
        )
        rows.append({
            "id": case["id"],
            "question": case["question"],
            "expected_project": expected,
            "predicted_project": predicted,
            "status": route["status"],
            "candidates": route.get("candidates", []),
            "correct": predicted == expected and candidates_ok,
            "wrong_route": route["status"] == "ok" and predicted != expected,
            "abstained": route["status"] == "project_required" and expected is not None,
        })
    targeted = [row for row in rows if row["expected_project"] is not None]
    ambiguous = [row for row in rows if row["expected_project"] is None]
    metrics = {
        "n": len(rows),
        "targeted_n": len(targeted),
        "targeted_accuracy": round(sum(row["correct"] for row in targeted) / len(targeted), 4),
        "targeted_coverage": round(
            sum(row["correct"] and row["status"] == "ok" for row in targeted) / len(targeted), 4
        ),
        "abstained": sum(row["abstained"] for row in rows),
        "wrong_route": sum(row["wrong_route"] for row in rows),
        "ambiguous_preserved": round(sum(row["correct"] for row in ambiguous) / len(ambiguous), 4),
    }
    out = {
        "run": "routing-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "cases_file": "evals/routing_cases_v1.jsonl",
        "metrics": metrics,
        "rows": rows,
    }
    output = ROOT / "evals/results/routing-v1.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    for row in rows:
        if not row["correct"]:
            print(f"未通过 {row['id']}: expected={row['expected_project']} "
                  f"actual={row['predicted_project']} status={row['status']}")
    print(f"结果已写入 {output}")
    return 1 if metrics["wrong_route"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
