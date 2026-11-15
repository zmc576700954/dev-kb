"""契约校验的人工入口（只读）。

用法::

    PYTHONPATH=src python -m agent_app.contract.cli sources
    PYTHONPATH=src python -m agent_app.contract.cli validate ai-video-kb
    PYTHONPATH=src python -m agent_app.contract.cli validate --json
    PYTHONPATH=src python -m agent_app.contract.cli rules
    PYTHONPATH=src python -m agent_app.contract.cli suggest-hints ai-video-kb

退出码：存在 error 级发现时为 1，否则 0。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .service import ContractService

ROOT = Path(__file__).resolve().parents[2]

SEVERITY_MARK = {"error": "✗", "warning": "!", "review": "?"}


def _print_sources(svc: ContractService) -> None:
    sources = svc.sources()
    if not sources:
        print("未登记任何知识源（检查 projects.yaml）")
        return
    print(f"已登记 {len(sources)} 个知识源：\n")
    for s in sources:
        provides = ", ".join(s["provides"]) or "（未声明）"
        roots = ", ".join(s["content_roots"]) or "（未声明）"
        print(f"  {s['name']}")
        print(f"    路径        {s['path']}")
        print(f"    provides    {provides}")
        print(f"    内容边界    {roots}")
        if s["entry_docs"]:
            print(f"    入口文档    {', '.join(s['entry_docs'])}")
        if s["intent_map"]:
            pairs = ", ".join(f"{k}→{'/'.join(v)}" for k, v in s["intent_map"].items())
            print(f"    意图映射    {pairs}")
        if s["terms"]:
            print(f"    术语条目    {s['terms']}")


def _print_result(result: dict) -> None:
    summary = result["summary"]
    print(f"=== {result['source']} ===")
    if result.get("path"):
        print(f"路径：{result['path']}")
    if result.get("provides") is not None:
        print(f"provides：{', '.join(result['provides']) or '（未声明）'}")
    print(f"判定：{result['verdict']}（error {summary['errors']} / warning {summary['warnings']}）")
    if result.get("message"):
        print(result["message"])
    findings = result.get("findings") or []
    if findings:
        print()
        for f in findings:
            mark = SEVERITY_MARK.get(f["severity"], "-")
            print(f"  {mark} [{f['rule_id']}] {f['message']}")
            if f.get("location"):
                print(f"      位置：{f['location']}")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="contract_cli", description="知识源接入契约校验（只读）")
    parser.add_argument("--config", default=None, help="projects.yaml 路径")
    parser.add_argument("--db", default=None, help="meta.db 路径")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("sources", help="列出已登记知识源及其声明")
    sub.add_parser("rules", help="列出契约规则")

    p_validate = sub.add_parser("validate", help="校验知识源是否满足契约")
    p_validate.add_argument("source", nargs="?", help="知识源名；省略则校验全部")
    p_validate.add_argument("--json", action="store_true", help="输出 JSON")

    p_hint = sub.add_parser("suggest-hints", help="列出一级目录，供编写 intent_map")
    p_hint.add_argument("source")

    args = parser.parse_args(argv)

    with ContractService(db_path=args.db, config=args.config) as svc:
        if args.command == "sources":
            _print_sources(svc)
            return 0

        if args.command == "rules":
            for r in svc.rules():
                mark = SEVERITY_MARK.get(r["severity"], "-")
                applies = f"（适用字段：{', '.join(r['applies_to'])}）" if r["applies_to"] else ""
                print(f"{mark} [{r['id']}] {r['title']} — {r['severity']}{applies}")
                print(f"    {r['statement']}")
            return 0

        if args.command == "suggest-hints":
            info = svc.suggest_hints(args.source)
            if not info.get("directories"):
                print(info.get("message", "未找到目录"))
                return 1
            print(f"{info['source']} 的一级目录（可作为 intent_map 的目标）：")
            for d in info["directories"]:
                print(f"  {d}")
            print()
            print(info["hint"])
            return 0

        # validate
        results = [svc.validate(args.source)] if args.source else svc.validate_all()
        if args.json:
            print(json.dumps(results[0] if args.source else results, ensure_ascii=False, indent=2))
        else:
            for r in results:
                _print_result(r)
        errors = sum(r["summary"]["errors"] for r in results)
        if not args.json:
            print(f"合计 error {errors}")
        return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
