"""KB 提案存储：meta.db 内的 kb_proposals 表（决策 Q12：同库新表）。

生命周期与 M6 doc_proposals 一致：pending → approved / rejected → applied。
ops 以 JSON 存自包含文件操作列表（决策 Q14：after 恒为完整目标内容）。
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from .operations import Plan

SCHEMA = """
CREATE TABLE IF NOT EXISTS kb_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL,
    kind TEXT NOT NULL,             -- create|update|move|deprecate|archive|delete
    target TEXT NOT NULL,
    title TEXT,
    reason TEXT,
    ops TEXT NOT NULL,              -- JSON [{op,path,after,before_sha}]
    findings TEXT,                  -- JSON 提案时的 warning/review
    status TEXT DEFAULT 'pending',  -- pending|approved|rejected|applied
    created_at REAL, decided_at REAL, applied_at REAL,
    applied_result TEXT
);
"""

ACTIVE_STATUSES = ("pending", "approved")


@dataclass
class KBProposal:
    id: int
    project: str
    kind: str
    target: str
    title: str
    reason: str
    ops: list[dict]
    findings: list[dict]
    status: str
    created_at: float
    decided_at: float | None = None
    applied_at: float | None = None
    applied_result: str | None = None

    def to_dict(self, include_ops: bool = True) -> dict:
        out = {
            "id": self.id, "project": self.project, "kind": self.kind,
            "target": self.target, "title": self.title, "reason": self.reason,
            "status": self.status,
            "created_at": self.created_at,
            "findings": self.findings,
        }
        if include_ops:
            out["ops"] = self.ops
        if self.decided_at:
            out["decided_at"] = self.decided_at
        if self.applied_at:
            out["applied_at"] = self.applied_at
        if self.applied_result:
            out["applied_result"] = self.applied_result
        return out


class KBProposalStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def add_plan(self, plan: Plan) -> int:
        cur = self.conn.execute(
            """INSERT INTO kb_proposals(project,kind,target,title,reason,ops,findings,
                                        status,created_at)
               VALUES(?,?,?,?,?,?,?,'pending',?)""",
            (plan.project, plan.kind, plan.target, plan.title, plan.reason,
             json.dumps([o.__dict__ for o in plan.ops], ensure_ascii=False),
             json.dumps([f.to_dict() for f in plan.findings], ensure_ascii=False),
             time.time()),
        )
        self.conn.commit()
        return cur.lastrowid

    def _row_to_proposal(self, row) -> KBProposal:
        return KBProposal(
            id=row["id"], project=row["project"], kind=row["kind"],
            target=row["target"], title=row["title"] or "", reason=row["reason"] or "",
            ops=json.loads(row["ops"]), findings=json.loads(row["findings"] or "[]"),
            status=row["status"], created_at=row["created_at"],
            decided_at=row["decided_at"], applied_at=row["applied_at"],
            applied_result=row["applied_result"],
        )

    def get(self, pid: int) -> KBProposal | None:
        row = self.conn.execute("SELECT * FROM kb_proposals WHERE id=?", (pid,)).fetchone()
        return self._row_to_proposal(row) if row else None

    def list(self, project: str | None = None, status: str | None = None,
             active_only: bool = False) -> list[KBProposal]:
        sql, params = "SELECT * FROM kb_proposals WHERE 1=1", []
        if project:
            sql += " AND project=?"
            params.append(project)
        if status:
            sql += " AND status=?"
            params.append(status)
        if active_only:
            sql += f" AND status IN {ACTIVE_STATUSES}"
        sql += " ORDER BY id DESC"
        return [self._row_to_proposal(r) for r in self.conn.execute(sql, params)]

    def decide(self, pid: int, status: str) -> bool:
        """决定提案状态。

        允许的迁移：
        - pending  → approved / rejected（正常审批）
        - approved → rejected（撤回批准：批准后、应用前发现提案过时）
        禁止：已 applied 的提案改状态；已 rejected 的复活。
        返回是否发生了状态变化。
        """
        if status not in ("approved", "rejected"):
            raise ValueError(f"非法审批状态: {status}")
        row = self.conn.execute(
            "SELECT status FROM kb_proposals WHERE id=?", (pid,)).fetchone()
        if row is None:
            return False
        current = row["status"]
        if current == "applied":
            return False        # 已落盘，不可改
        if current == status:
            return False        # 幂等：无变化
        if current == "rejected":
            return False        # 已驳回，不可复活
        self.conn.execute(
            "UPDATE kb_proposals SET status=?, decided_at=? WHERE id=?",
            (status, time.time(), pid))
        self.conn.commit()
        return True

    def mark_applied(self, pid: int, result: dict):
        self.conn.execute(
            "UPDATE kb_proposals SET status='applied', applied_at=?, applied_result=? WHERE id=?",
            (time.time(), json.dumps(result, ensure_ascii=False), pid))
        self.conn.commit()

    def pending_on_target(self, project: str, path: str) -> list[KBProposal]:
        rows = self.conn.execute(
            "SELECT * FROM kb_proposals WHERE project=? AND target=? AND status IN ('pending','approved')",
            (project, path)).fetchall()
        return [self._row_to_proposal(r) for r in rows]
