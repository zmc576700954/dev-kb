#!/usr/bin/env python3
"""生成知识库待办清单：按文档分组，含每处的位置、规则、问题与建议动作。

用途：
- 人工整改的工作单（warning/review 级问题都需要人判断，工具不自动改）
- CI 报告的格式基础（同一份 Markdown 可直接贴进 PR 评论）

用法：
  PYTHONPATH=src .venv/bin/python scripts/todo_report.py ai-video-kb
  PYTHONPATH=src .venv/bin/python scripts/todo_report.py ai-video-kb --output reports/kb-todo.md
  PYTHONPATH=src .venv/bin/python scripts/todo_report.py ai-video-kb --severity warning --json
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent_app.kb_spec.rules import RULES
from agent_app.kb_spec.service import KBSpecService

SEVERITY_ORDER = {"error": 0, "warning": 1, "review": 2}
SEVERITY_LABEL = {"error": "必须修复", "warning": "建议优化", "review": "需人工判断"}


def build(project: str, severities: set[str]) -> dict:
    svc = KBSpecService()
    result = svc.kb_validate_project(project)
    findings = [f for f in result["findings"] if f["severity"] in severities]
    # 按文档分组
    by_doc: dict[str, list[dict]] = defaultdict(list)
    for f in findings:
        loc = f.get("location") or ""
        doc = loc.rsplit(":", 1)[0] if loc.count(":") == 1 else (loc or "(项目级)")
        by_doc[doc].append(f)
    # 按规则统计
    by_rule: dict[str, int] = defaultdict(int)
    for f in findings:
        by_rule[f["rule_id"]] += 1
    return {
        "project": project,
        "summary": result["summary"],
        "documents": result["documents"],
        "by_doc": {k: sorted(v, key=lambda x: (SEVERITY_ORDER.get(x["severity"], 9), x["rule_id"]))
                   for k, v in sorted(by_doc.items())},
        "by_rule": dict(sorted(by_rule.items(), key=lambda x: -x[1])),
    }


def render_markdown(data: dict) -> str:
    s = data["summary"]
    lines = [
        f"# 知识库待办清单：{data['project']}",
        "",
        f"生成日期：{date.today().isoformat()}　|　文档数：{data['documents']}",
        "",
        f"**必须修复 {s['errors']}　建议优化 {s['warnings']}　需人工判断 {s['reviews']}**",
        "",
    ]
    if not data["by_doc"]:
        lines.append("无待办项。")
        return "\n".join(lines) + "\n"

    lines.append("## 按规则统计")
    lines.append("")
    lines.append("| 规则 | 严重级 | 处数 | 说明 |")
    lines.append("|---|---|---:|---|")
    for rule, n in data["by_rule"].items():
        sev, desc = RULES.get(rule, ("review", ""))
        lines.append(f"| `{rule}` | {SEVERITY_LABEL.get(sev, sev)} | {n} | {desc} |")
    lines.append("")

    lines.append("## 按文档")
    lines.append("")
    for doc, fs in data["by_doc"].items():
        lines.append(f"### {doc}")
        lines.append("")
        for f in fs:
            sev = f["severity"]
            loc = f.get("location", "")
            lines.append(f"- **[{SEVERITY_LABEL.get(sev, sev)}]** `{f['rule_id']}`"
                         + (f"　位置：{loc}" if loc else ""))
            lines.append(f"  - 问题：{f['message']}")
            if f.get("fix_hint"):
                lines.append(f"  - 建议：{f['fix_hint']}")
            if f.get("evidence"):
                ev = f["evidence"]
                evs = "；".join(f"{k}={v}" for k, v in ev.items()
                                if isinstance(v, (str, int, float)))
                lines.append(f"  - 证据：{evs}")
        lines.append("")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="生成知识库待办清单")
    parser.add_argument("project")
    parser.add_argument("--severity", default="warning,review",
                        help="包含哪些严重级（error,warning,review）")
    parser.add_argument("--output", default=None, help="写入文件（默认打印）")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    severities = {x.strip() for x in args.severity.split(",") if x.strip()}
    data = build(args.project, severities)

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    text = render_markdown(data)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"已写入 {out}（{len(text.splitlines())} 行）")
    else:
        print(text)


if __name__ == "__main__":
    main()
