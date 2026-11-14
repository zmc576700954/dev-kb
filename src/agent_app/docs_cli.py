"""M6 文档维护闭环命令：分析 → 提案 → 审批 → 应用 → 自动重建索引。

用法：
    python -m agent_app.docs_cli analyze tiangong --since "7 days ago"
    python -m agent_app.docs_cli list [project]
    python -m agent_app.docs_cli show <id>
    python -m agent_app.docs_cli approve <id> / reject <id>
    python -m agent_app.docs_cli apply <id>     # 仅 approved 可应用；写入后自动增量索引
    python -m agent_app.docs_cli redraft <id>   # 用 LLM 基于 git diff 重生成 pending 提案的草案
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_app.indexing.metadata_store import MetadataStore
from agent_app.proposals.apply_update import apply_proposal
from agent_app.proposals.impact_analysis import analyze_impact, draft_proposal
from agent_app.tools.projects import project_root

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def main() -> int:
    parser = argparse.ArgumentParser(description="文档更新闭环（M6）")
    parser.add_argument("command", choices=["analyze", "list", "show", "approve", "reject", "apply", "redraft", "redraft-all"])
    parser.add_argument("target", nargs="?", default="all", help="项目名或提案 id")
    parser.add_argument("--since", help='如 "7 days ago"')
    parser.add_argument("--ref", help="git ref，如 HEAD~5")
    args = parser.parse_args()

    store = MetadataStore(DATA_DIR / "meta.db")

    if args.command == "analyze":
        root = project_root(args.target)
        if not root:
            print(f"未登记的项目: {args.target}")
            return 1
        impacts = analyze_impact(store, args.target, root, args.since, args.ref)
        if not impacts:
            print("未发现受影响的文档。")
            return 0
        # 分级：含 Spec 显式引用的为高置信，生成提案；仅正文提及的进观察名单，不生提案
        high = [i for i in impacts if any(e["type"] == "spec_ref" for e in i["evidence"])]
        low = [i for i in impacts if i not in high]
        # 去重：同文档已有相同变更文件集合的 pending 提案时跳过（定时任务每天跑会重复发现）
        existing = {
            p["doc_path"]: set(json.loads(p["evidence"]).get("changed_files", []))
            for p in store.proposals(args.target, "pending")
        }
        created, skipped = 0, 0
        for imp in high:
            files = {e["changed_file"] for e in imp["evidence"]}
            if existing.get(imp["doc_path"]) == files:
                skipped += 1
                continue
            pid = draft_proposal(store, args.target, imp["doc_path"], imp["evidence"])
            refs = [e for e in imp["evidence"] if e["type"] == "spec_ref"]
            print(f"  提案 #{pid}: {imp['doc_path']}")
            print(f"    ← {len(files)} 个变更文件，其中 {len(refs)} 处为文档内行级引用")
            created += 1
        print(f"影响分析完成：新增高置信提案 {created} 份"
              + (f"，跳过重复 {skipped} 份" if skipped else "")
              + f"，观察名单 {len(low)} 份\n")
        if low:
            print("观察名单（仅正文提及变更文件，未生成提案）：")
            for imp in low[:15]:
                print(f"  · {imp['doc_path']}")
            if len(low) > 15:
                print(f"  ……共 {len(low)} 份")
        return 0

    if args.command == "list":
        for p in store.proposals(args.target if args.target != "all" else None):
            print(f"#{p['id']:3} [{p['status']:8}] {p['project']}/{p['doc_path']}  {p['title']}")
        return 0

    if args.command == "redraft-all":
        # 批量把 pending 模板草案升级为 LLM 草案（每日巡检调用）
        import time
        from agent_app.proposals.llm_draft import llm_draft
        t0, done, already = time.time(), 0, 0
        for p in store.proposals(status="pending"):
            if "## 变更同步" in p["draft"]:
                already += 1
                continue
            if time.time() - t0 > 480:
                print("达到时间预算，剩余提案下次巡检继续")
                break
            draft = llm_draft(project_root(p["project"]), p["doc_path"],
                              json.loads(p["evidence"])["evidence"])
            if draft:
                store.update_draft(p["id"], draft)
                done += 1
                print(f"  #{p['id']} ✓ {p['doc_path']}")
        print(f"LLM 草案升级 {done} 份（已是 LLM 草案 {already} 份）")
        return 0

    pid = int(args.target)
    p = store.get_proposal(pid)
    if not p:
        print(f"提案不存在: {pid}")
        return 1

    if args.command == "show":
        print(f"提案 #{p['id']} [{p['status']}] {p['project']}/{p['doc_path']}")
        print(f"原因：{p['reason']}")
        ev = json.loads(p["evidence"])
        print("证据：")
        for e in ev["evidence"]:
            loc = f"，引用于第 {e['ref_line']} 行" if e.get("ref_line") else ""
            print(f"  - {e['changed_file']}（{e['how']} @ {e.get('commit', '')}）{loc}")
        print(f"\n草案：{p['draft']}")
        if p["diff"]:
            print(f"\n应用 diff：\n{p['diff']}")
        return 0

    if args.command in ("approve", "reject"):
        store.decide_proposal(pid, "approved" if args.command == "approve" else "rejected")
        print(f"提案 #{pid} 已{'批准' if args.command == 'approve' else '驳回'}。")
        return 0

    if args.command == "redraft":
        if p["status"] != "pending":
            print(f"提案 #{pid} 状态为 {p['status']}，只有 pending 可重新生成草案")
            return 1
        root = project_root(p["project"])
        from agent_app.proposals.llm_draft import llm_draft
        evidence = json.loads(p["evidence"])["evidence"]
        draft = llm_draft(root, p["doc_path"], evidence)
        if not draft:
            print("LLM 不可用或未生成内容，保留原草案")
            return 1
        store.update_draft(pid, draft)
        print(f"提案 #{pid} 草案已升级为 LLM 版本：")
        print(draft)
        return 0

    if args.command == "apply":
        root = project_root(p["project"])
        r = apply_proposal(store, root, pid)
        if "error" in r:
            print(f"❌ {r['error']}")
            return 1
        print(f"✅ 已写入 {r['applied']}（diff {r['diff_lines']} 行）")
        # 应用后自动增量索引
        import yaml
        from agent_app.indexing.vector_store import VectorStore
        from agent_app.ingestion.sync import SyncEngine
        cfg = yaml.safe_load(open("projects.yaml", encoding="utf-8"))
        proj = next(x for x in cfg["projects"] if x["name"] == p["project"])
        engine = SyncEngine(store, VectorStore(DATA_DIR / "qdrant"))
        sr = engine.sync_project(p["project"], proj["path"], proj.get("extra_exclude_dirs"))
        print(f"增量索引完成：变更={sr['changed']} 块数={sr['chunks']}")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
