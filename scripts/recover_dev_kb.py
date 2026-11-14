#!/usr/bin/env python3
"""恢复 dev-kb 被破坏的围栏结构，并重新规范化。

背景：normalize_dev_kb.py 早期版本用配对式逻辑补代码块语言，把「纯围栏 closing」
误当成新 opening，破坏了 4 篇文档的围栏配对（closing 变成 ```text，后续正文被
错误包进代码块）。本脚本：
1. 用 fix_fences 的成对逻辑修复围栏（opening 保留语言、closing 去标注）；
2. 重新执行规范化（tag_code_blocks 已改为状态机，不会再破坏结构）。

用法：
  PYTHONPATH=src .venv/bin/python scripts/recover_dev_kb.py            # 预览
  PYTHONPATH=src .venv/bin/python scripts/recover_dev_kb.py --apply    # 生成并应用提案
"""

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from agent_app.kb_spec.service import KBSpecService
from fix_fences import fix_fences
from normalize_dev_kb import (RELATED, SUMMARIES, ensure_related, ensure_frontmatter,
                             replace_summary, tag_code_blocks)

PROJECT = "dev-kb"
TODAY = date(2026, 11, 14)


def repair(text: str) -> tuple[str, list[str]]:
    """修复围栏配对：closing 去掉语言标注，opening 缺语言则补 text。"""
    fixed, issues = fix_fences(text)
    return fixed, issues


def rewrite_readme(text: str) -> str:
    from normalize_dev_kb import rewrite_readme as _rw
    return _rw(text)


def main():
    parser = argparse.ArgumentParser(description="恢复 dev-kb 围栏并重新规范化")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    svc = KBSpecService()
    _, snap = svc.build_snapshot(PROJECT)

    plans = []
    total_issues = 0
    for path, content in sorted(snap.files.items()):
        new, issues = repair(content)
        total_issues += len(issues)
        if path == "README.md":
            new = rewrite_readme(new)
            new = tag_code_blocks(new)
        elif path in SUMMARIES:
            new = ensure_frontmatter(new)
            new = replace_summary(new, SUMMARIES[path])
            new = tag_code_blocks(new)
            new = ensure_related(new, RELATED.get(path, []))
        else:
            continue
        if new != content:
            plans.append((path, new, issues))

    print(f"项目 {PROJECT}：{len(plans)} 篇需修复，围栏问题 {total_issues} 处")
    for path, _, issues in plans:
        print(f"  {path}" + (f"（围栏 {len(issues)} 处）" if issues else ""))

    if not args.apply:
        print("\n（预览模式；加 --apply 生成并应用提案）")
        return
    ok = 0
    for path, new, _ in plans:
        r = svc.propose_update(PROJECT, path, new, snap.sha_of(path))
        if r["status"] != "proposal_created":
            print(f"  {path}: {r['status']} {r.get('message', '')[:70]}")
            for f in r.get("reject_findings", [])[:4]:
                print(f"      {f['rule_id']}: {f['message'][:70]}")
            continue
        pid = r["proposal_id"]
        svc.proposals.decide(pid, "approved")
        res = svc.apply_proposal(pid)
        if res["status"] == "applied":
            ok += 1
            print(f"  {path}: 已应用")
        else:
            print(f"  {path}: {res['status']} {res.get('message', '')[:70]}")
    print(f"\n应用成功 {ok}/{len(plans)}")
    v = svc.kb_validate_project(PROJECT)
    print(f"最终：error={v['summary']['errors']} warning={v['summary']['warnings']} "
          f"review={v['summary']['reviews']}")


if __name__ == "__main__":
    main()
