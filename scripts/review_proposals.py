#!/usr/bin/env python3
"""按文档分组的提案审查/应用——薄封装，批量逻辑已并入 kb_cli。

用法：
  # 审查（默认）
  PYTHONPATH=src .venv/bin/python scripts/review_proposals.py ai-video-kb

  # 应用指定文档的提案
  PYTHONPATH=src .venv/bin/python scripts/review_proposals.py ai-video-kb --apply 01-业务概览/01-行业现状与产业链.md

  # 批量应用该项目全部已批准提案
  PYTHONPATH=src .venv/bin/python scripts/review_proposals.py ai-video-kb --apply-all
"""

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent_app.kb_spec import cli
from agent_app.kb_spec.service import KBSpecService


def describe(proposal: dict) -> str:
    """生成提案的人类可读描述。"""
    ops_desc = []
    for op in proposal.get("ops", []):
        if op.get("after"):
            after = op["after"]
            if "状态: 草案" in after and "最后更新:" in after:
                ops_desc.append("补 frontmatter")
            if any(tag in after for tag in ("```bash", "```text", "```python", "```json")):
                ops_desc.append("代码块语言标注")
            if "## 相关文档" in after:
                ops_desc.append("补相关文档段")
        elif op["op"] == "delete":
            ops_desc.append(f"删除 {op['path']}")
    return " + ".join(set(ops_desc)) if ops_desc else "未知修改"


def main():
    parser = argparse.ArgumentParser(description="按文档分组的提案审查/应用")
    parser.add_argument("project")
    parser.add_argument("--apply", metavar="DOC", help="应用指定文档的提案")
    parser.add_argument("--apply-all", action="store_true", help="应用全部已批准提案")
    args = parser.parse_args()

    service = KBSpecService()
    pending = service.list_proposals(args.project, status="pending")
    if not pending["proposals"]:
        print("无待审批提案")
        return

    by_doc = defaultdict(list)
    for p in pending["proposals"]:
        by_doc[p["target"]].append(p)

    if args.apply:
        if args.apply in by_doc:
            ids = [str(p["id"]) for p in sorted(by_doc[args.apply], key=lambda x: x["id"])]
            print(f"应用 {args.apply} 的 {len(ids)} 个提案")
            for p in sorted(by_doc[args.apply], key=lambda x: x["id"]):
                print(f"  #{p['id']} {describe(p)}")
            code = cli._cmd_apply(service, ids, args.project, False, False)
            return code
        print(f"文档 {args.apply} 无待审批提案")
        return 1
    if args.apply_all:
        code = cli._cmd_apply(service, [], args.project, True, False)
        return code

    # 审查模式
    print(f"项目 {args.project} 待审批提案审查")
    print(f"总计：{len(pending['proposals'])} 个提案，{len(by_doc)} 个文档\n")
    for doc in sorted(by_doc.keys()):
        props = sorted(by_doc[doc], key=lambda x: x["id"])
        print(f"{'=' * 60}")
        print(f"文档：{doc}（{len(props)} 个提案）")
        print(f"{'=' * 60}")
        for p in props:
            print(f"  #{p['id']} [{p['status']}] {describe(p)}")
        print()
    print("应用指定文档：--apply <文档路径>")
    print("应用全部：--apply-all（或用 kb_cli apply --project <项目>）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
