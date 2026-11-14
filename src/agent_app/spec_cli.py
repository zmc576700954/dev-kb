"""M4 Spec 进度命令：进度总览 / 单工作流详情 / 并行任务计算。

用法：
    python -m agent_app.spec_cli progress tiangong
    python -m agent_app.spec_cli detail tiangong d10b-yjs-awareness
    python -m agent_app.spec_cli parallel tiangong
    python -m agent_app.spec_cli list repository-manage
"""

from __future__ import annotations

import argparse
from pathlib import Path

from agent_app.indexing.metadata_store import MetadataStore
from agent_app.specs.task_graph import TaskGraph

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def main() -> int:
    parser = argparse.ArgumentParser(description="Spec 进度与并行任务（M4）")
    parser.add_argument("command", choices=["list", "progress", "detail", "parallel"])
    parser.add_argument("project")
    parser.add_argument("key", nargs="?", help="detail 命令的工作流 key")
    args = parser.parse_args()

    store = MetadataStore(DATA_DIR / "meta.db")
    graph = TaskGraph(store, args.project)

    if args.command == "list":
        for d in store.spec_docs(args.project):
            status = d["status"] or d["status_raw"] or "-"
            print(f"[{d['kind']:6}] {d['spec_key']:40} {status:16} {d['title'][:40]}")
        return 0

    if args.command == "progress":
        s = graph.summary()
        print(f"项目：{s['project']}")
        print(f"工作流总数：{s['workstreams']}")
        print(f"任务：总 {s['tasks_total']} / 已完成 {s['tasks_done']} / 待办 {s['tasks_pending']}")
        print("\n按状态分布：")
        for state, keys in s["by_state"].items():
            print(f"  {state}（{len(keys)}）")
        if s["blocked"]:
            print(f"\n⚠️ 被阻塞的工作流：{', '.join(s['blocked'])}")
        print("\n各工作流明细：")
        for k in graph.all_keys():
            p = graph.progress(k)
            bar = f"{p['done']}/{p['total']}" if p["total"] else "-"
            block = f" ⛔ 等 {','.join(p['blocked_by'])}" if p["blocked_by"] else ""
            print(f"  [{p['state']:11}] {p['key']:38} 任务 {bar:8} {p['source_path']}{block}")
        return 0

    if args.command == "detail":
        if not args.key:
            print("detail 需要指定 key")
            return 1
        p = graph.progress(args.key)
        if not p["source_path"]:
            print(f"未找到工作流：{args.key}")
            return 1
        print(f"工作流：{p['key']} — {p['title']}")
        print(f"状态：{p['state']}（任务 {p['done']}/{p['total']}）")
        if p["status_text"]:
            print(f"当前状态摘要：{p['status_text'][:200]}")
        if p["branch"]:
            print(f"工作分支：{p['branch']}")
        if p["depends_on"]:
            print(f"前置依赖：{', '.join(p['depends_on'])}")
        if p["blocked_by"]:
            print(f"⛔ 阻塞于：{', '.join(p['blocked_by'])}")
        print(f"来源：{p['source_path']}")
        print("\n任务清单：")
        for t in store.spec_tasks(args.project, args.key):
            mark = "✅" if t["done"] else "⬜"
            print(f"  {mark} {t['task_id']:12} {t['title'][:70]}  ({t['source_path']}:{t['start_line']})")
        return 0

    if args.command == "parallel":
        groups = graph.parallel_groups()
        if not groups:
            print("当前没有可并行执行的工作流。")
            return 0
        print(f"当前可并行执行的工作流共 {len(groups)} 组（前置依赖均已完成，两两无依赖）：\n")
        for g in groups:
            print(f"▶ {g['key']} — {g['title']}")
            print(f"  来源：{g['source_path']}")
            for t in g["pending_tasks"][:6]:
                print(f"    ⬜ {t['task_id']:12} {t['title']}  ({t['source']})")
            if len(g["pending_tasks"]) > 6:
                print(f"    ……另有 {len(g['pending_tasks']) - 6} 项")
            print()
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
