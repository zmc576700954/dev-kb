"""任务依赖图与并行计算：全部由确定性图算法得出，不让模型猜。

节点：工作流（canonical spec key，即一份任务跟踪文件）。
边：spec_deps（前置任务/依赖链接）。

判定规则（对照规划第四节）：
- 可执行：有未完成任务 且 所有前置工作流已完成。
- 并行组：所有可执行工作流两两无依赖（构造保证）。
- 阻塞：前置工作流存在且未全部完成。
"""

from __future__ import annotations

from ..indexing.metadata_store import MetadataStore


class TaskGraph:
    def __init__(self, store: MetadataStore, project: str):
        self.project = project
        docs = store.spec_docs(project)
        # 每个 key 关联的文档（design/plan/task 可能多份）
        self.docs_by_key: dict[str, list] = {}
        for d in docs:
            self.docs_by_key.setdefault(d["spec_key"], []).append(d)

        self.tasks_by_key: dict[str, list] = {}
        for t in store.spec_tasks(project):
            self.tasks_by_key.setdefault(t["spec_key"], []).append(t)

        self.deps: dict[str, set[str]] = {}
        for r in store.spec_deps(project):
            if r["spec_key"] != r["depends_on"]:
                self.deps.setdefault(r["spec_key"], set()).add(r["depends_on"])

    def progress(self, key: str) -> dict:
        tasks = self.tasks_by_key.get(key, [])
        total = len(tasks)
        done = sum(1 for t in tasks if t["done"])
        if total == 0:
            state = self._design_state(key)
        elif done == total:
            state = "done"
        elif done > 0:
            state = "in_progress"
        else:
            state = "pending"
        docs = self.docs_by_key.get(key, [])
        task_doc = next((d for d in docs if d["kind"] == "task"), docs[0] if docs else None)
        dep_keys = self.deps.get(key, set())
        unfinished_deps = [d for d in sorted(dep_keys) if self.progress(d)["state"] != "done"]
        return {
            "key": key,
            "title": task_doc["title"] if task_doc else key,
            "state": state,
            "total": total, "done": done, "pending": total - done,
            "status_text": task_doc["status_text"] if task_doc else "",
            "branch": task_doc["branch"] if task_doc else "",
            "source_path": task_doc["path"] if task_doc else "",
            "depends_on": sorted(dep_keys),
            "blocked_by": unfinished_deps,
            "executable": state in ("pending", "in_progress") and not unfinished_deps,
        }

    def _design_state(self, key: str) -> str:
        """无任务清单时，用设计文档状态行兜底。"""
        for d in self.docs_by_key.get(key, []):
            if d["kind"] == "design" and d["status"]:
                return {"done": "done"}.get(d["status"], d["status"])
        return "unknown"

    def all_keys(self) -> list[str]:
        keys = set(self.docs_by_key) | set(self.tasks_by_key)
        return sorted(keys)

    def summary(self) -> dict:
        states = [self.progress(k) for k in self.all_keys()]
        by_state: dict[str, list[str]] = {}
        for s in states:
            by_state.setdefault(s["state"], []).append(s["key"])
        total_tasks = sum(s["total"] for s in states)
        done_tasks = sum(s["done"] for s in states)
        return {
            "project": self.project,
            "workstreams": len(states),
            "by_state": {k: v for k, v in sorted(by_state.items())},
            "tasks_total": total_tasks,
            "tasks_done": done_tasks,
            "tasks_in_progress": sum(s["total"] - s["done"] for s in states if 0 < s["done"] < s["total"]),
            "tasks_pending": sum(s["total"] for s in states if s["done"] == 0 and s["total"] > 0),
            "blocked": [s["key"] for s in states
                        if s["blocked_by"] and s["state"] in ("pending", "in_progress")],
        }

    def parallel_groups(self) -> list[dict]:
        """当前可并行执行的工作流组。"""
        executable = [self.progress(k) for k in self.all_keys()]
        executable = [s for s in executable if s["executable"]]
        return [{
            "key": s["key"], "title": s["title"],
            "pending_tasks": [
                {"task_id": t["task_id"], "title": t["title"][:80],
                 "source": f"{t['source_path']}:{t['start_line']}"}
                for t in self.tasks_by_key.get(s["key"], []) if not t["done"]
            ],
            "source_path": s["source_path"],
        } for s in executable]
