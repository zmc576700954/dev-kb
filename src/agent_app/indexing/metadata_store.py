"""SQLite 元数据存储 + FTS5 关键词索引。

表：
- files：文件清单、内容哈希、同步状态、git 提交
- chunks：知识块原文与元数据
- chunk_fts：FTS5 全文索引（symbol / content / path / project）
- sync_errors：解析失败记录（不静默跳过）
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    project TEXT NOT NULL,
    path TEXT NOT NULL,
    mtime REAL,
    size INTEGER,
    content_hash TEXT,
    git_commit TEXT,
    status TEXT DEFAULT 'indexed',      -- indexed | error
    indexed_at REAL,
    PRIMARY KEY (project, path)
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL,
    path TEXT NOT NULL,
    source_type TEXT NOT NULL,
    language TEXT DEFAULT '',
    symbol TEXT DEFAULT '',
    heading_path TEXT DEFAULT '[]',
    start_line INTEGER,
    end_line INTEGER,
    content TEXT NOT NULL,
    embedded INTEGER DEFAULT 0          -- 0/1：是否已生成向量
);
CREATE INDEX IF NOT EXISTS idx_chunks_file ON chunks(project, path);
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
    symbol, content, path, project,
    tokenize='unicode61'
);
CREATE TABLE IF NOT EXISTS sync_errors (
    project TEXT, path TEXT, stage TEXT, message TEXT, at REAL
);
-- M4：Spec 结构化存储
CREATE TABLE IF NOT EXISTS spec_docs (
    project TEXT NOT NULL,
    path TEXT NOT NULL,              -- 相对路径
    kind TEXT NOT NULL,              -- design | plan | task
    spec_key TEXT NOT NULL,          -- canonical key（三类文档配对用）
    title TEXT, status TEXT, status_raw TEXT,
    created TEXT, branch TEXT, status_text TEXT,
    PRIMARY KEY (project, path)
);
CREATE INDEX IF NOT EXISTS idx_spec_key ON spec_docs(project, spec_key);
CREATE TABLE IF NOT EXISTS spec_tasks (
    project TEXT NOT NULL,
    spec_key TEXT NOT NULL,
    task_id TEXT NOT NULL,
    title TEXT, done INTEGER, ordinal INTEGER,
    source_path TEXT, start_line INTEGER, end_line INTEGER,
    PRIMARY KEY (project, spec_key, task_id, source_path)
);
CREATE TABLE IF NOT EXISTS spec_deps (
    project TEXT NOT NULL,
    spec_key TEXT NOT NULL,
    depends_on TEXT NOT NULL,        -- canonical key
    source_path TEXT,
    PRIMARY KEY (project, spec_key, depends_on, source_path)
);
CREATE TABLE IF NOT EXISTS spec_code_refs (
    project TEXT NOT NULL,
    spec_key TEXT NOT NULL,
    code_path TEXT, code_line INTEGER,
    source_path TEXT,
    PRIMARY KEY (project, spec_key, code_path, code_line, source_path)
);
-- M6：文档更新提案（审批闭环）
CREATE TABLE IF NOT EXISTS doc_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL,
    doc_path TEXT NOT NULL,
    title TEXT,
    reason TEXT,                   -- 为什么认为该文档受影响
    evidence TEXT,                 -- JSON：变更文件、引用位置等
    draft TEXT,                    -- 建议写入的内容草案
    status TEXT DEFAULT 'pending', -- pending | approved | rejected | applied
    created_at REAL, decided_at REAL, applied_at REAL,
    diff TEXT                      -- 应用时的 unified diff
);
"""


class MetadataStore:
    def __init__(self, db_path: str | Path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ---- files ----

    def get_file(self, project: str, path: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM files WHERE project=? AND path=?", (project, path)
        ).fetchone()

    def known_paths(self, project: str) -> set[str]:
        rows = self.conn.execute("SELECT path FROM files WHERE project=?", (project,))
        return {r["path"] for r in rows}

    def upsert_file(self, project: str, path: str, mtime: float, size: int,
                    content_hash: str, git_commit: str, status: str = "indexed"):
        self.conn.execute(
            """INSERT INTO files(project,path,mtime,size,content_hash,git_commit,status,indexed_at)
               VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(project,path) DO UPDATE SET
                 mtime=excluded.mtime, size=excluded.size, content_hash=excluded.content_hash,
                 git_commit=excluded.git_commit, status=excluded.status, indexed_at=excluded.indexed_at""",
            (project, path, mtime, size, content_hash, git_commit, status, time.time()),
        )

    def delete_file(self, project: str, path: str) -> list[int]:
        """删除文件记录及其全部块，返回被删 chunk id（供向量库清理）。"""
        ids = self.delete_file_chunks_only(project, path)
        self.conn.execute("DELETE FROM files WHERE project=? AND path=?", (project, path))
        self.delete_spec(project, path)
        return ids

    # ---- chunks ----

    def replace_chunks(self, project: str, path: str, chunks: list[dict]) -> list[int]:
        """替换某文件的全部块，返回新块 id 列表。"""
        self.delete_file_chunks_only(project, path)
        ids = []
        for c in chunks:
            cur = self.conn.execute(
                """INSERT INTO chunks(project,path,source_type,language,symbol,heading_path,
                                      start_line,end_line,content,embedded)
                   VALUES(?,?,?,?,?,?,?,?,?,0)""",
                (project, path, c["source_type"], c.get("language", ""), c.get("symbol", ""),
                 json.dumps(c.get("heading_path", []), ensure_ascii=False),
                 c.get("start_line", 0), c.get("end_line", 0), c["content"]),
            )
            cid = cur.lastrowid
            from .fts_tokenizer import fts_tokenize
            searchable = fts_tokenize(f'{c.get("symbol", "")} {path} {c["content"]}')
            self.conn.execute(
                "INSERT INTO chunk_fts(rowid, symbol, content, path, project) VALUES(?,?,?,?,?)",
                (cid, fts_tokenize(c.get("symbol", "")), searchable, fts_tokenize(path), project),
            )
            ids.append(cid)
        return ids

    def delete_file_chunks_only(self, project: str, path: str) -> list[int]:
        ids = [r["id"] for r in self.conn.execute(
            "SELECT id FROM chunks WHERE project=? AND path=?", (project, path))]
        if ids:
            q = ",".join("?" * len(ids))
            self.conn.execute(f"DELETE FROM chunk_fts WHERE rowid IN ({q})", ids)
            self.conn.execute(f"DELETE FROM chunks WHERE id IN ({q})", ids)
        return ids

    def mark_embedded(self, chunk_ids: list[int]):
        if not chunk_ids:
            return
        q = ",".join("?" * len(chunk_ids))
        self.conn.execute(f"UPDATE chunks SET embedded=1 WHERE id IN ({q})", chunk_ids)

    def unembedded_chunks(self, project: str | None = None, limit: int = 500) -> list[sqlite3.Row]:
        # 按内容长度排序取出：同批长度接近，显著减少 BGE-M3 的 padding 浪费
        sql = "SELECT id, content FROM chunks WHERE embedded=0"
        params: list = []
        if project:
            sql += " AND project=?"
            params.append(project)
        sql += " ORDER BY LENGTH(content) LIMIT ?"
        params.append(limit)
        return self.conn.execute(sql, params).fetchall()

    # ---- 检索 ----

    def fts_search(self, query: str, project: str | None = None, limit: int = 10,
                   or_fallback: bool = True) -> list[sqlite3.Row]:
        from .fts_tokenizer import fts_query, fts_tokenize
        rows = self._fts_run(fts_query(query), project, limit)
        if rows or not or_fallback:
            return rows
        # AND 落空 → 覆盖率降级：逐 token 检索，按"命中 token 数 → bm25 总分"排序
        tokens = [t for t in dict.fromkeys(fts_tokenize(query).split()) if len(t) >= 2][:8]
        if not tokens:
            return []
        scores: dict[int, float] = {}
        counts: dict[int, int] = {}
        for t in tokens:
            for r in self._fts_run(f'"{t}"', project, 200):
                scores[r["id"]] = scores.get(r["id"], 0.0) + r["rank"]
                counts[r["id"]] = counts.get(r["id"], 0) + 1
        top = sorted(counts, key=lambda i: (counts[i], -scores.get(i, 0)), reverse=True)[:limit]
        return [self.get_chunk(i) for i in top]

    def _fts_run(self, match: str, project: str | None, limit: int) -> list[sqlite3.Row]:
        sql = """SELECT c.*, bm25(chunk_fts) AS rank FROM chunk_fts
                 JOIN chunks c ON c.id = chunk_fts.rowid
                 WHERE chunk_fts MATCH ?"""
        params: list = [match]
        if project:
            sql += " AND chunk_fts.project = ?"
            params.append(project)
        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)
        return self.conn.execute(sql, params).fetchall()

    def symbol_search(self, term: str, project: str | None = None, limit: int = 10) -> list[sqlite3.Row]:
        """符号精确/前缀搜索：类名、函数名、方法名直接命中，不走 FTS。"""
        sql = "SELECT * FROM chunks WHERE (symbol = ? OR symbol LIKE ? OR symbol LIKE ?)"
        params: list = [term, f"%@{term}", f"%::{term}%"]
        if project:
            sql += " AND project = ?"
            params.append(project)
        sql += " ORDER BY LENGTH(symbol) LIMIT ?"
        params.append(limit)
        return self.conn.execute(sql, params).fetchall()

    def symbol_substring_search(self, term: str, project: str | None = None, limit: int = 10) -> list[sqlite3.Row]:
        """符号子串搜索：扩展词子词（如 list → list_kbs）挂接真实符号。"""
        sql = "SELECT * FROM chunks WHERE symbol LIKE ?"
        params: list = [f"%{term}%"]
        if project:
            sql += " AND project = ?"
            params.append(project)
        sql += " ORDER BY LENGTH(symbol) LIMIT ?"
        params.append(limit)
        return self.conn.execute(sql, params).fetchall()

    def path_search(self, term: str, project: str | None = None, limit: int = 10) -> list[sqlite3.Row]:
        """路径子串搜索：查询中的符号 token 直接匹配文件路径。"""
        sql = "SELECT * FROM chunks WHERE path LIKE ?"
        params: list = [f"%{term}%"]
        if project:
            sql += " AND project = ?"
            params.append(project)
        sql += " ORDER BY LENGTH(path) LIMIT ?"
        params.append(limit)
        return self.conn.execute(sql, params).fetchall()

    def rebuild_fts(self):
        """清空并用当前分词器重建全文索引（分词器升级后调用）。"""
        from .fts_tokenizer import fts_tokenize
        self.conn.execute("DELETE FROM chunk_fts")
        rows = self.conn.execute("SELECT id, symbol, content, path, project FROM chunks").fetchall()
        for r in rows:
            searchable = fts_tokenize(f'{r["symbol"]} {r["path"]} {r["content"]}')
            self.conn.execute(
                "INSERT INTO chunk_fts(rowid, symbol, content, path, project) VALUES(?,?,?,?,?)",
                (r["id"], fts_tokenize(r["symbol"]), searchable, fts_tokenize(r["path"]), r["project"]),
            )
        self.conn.commit()
        return len(rows)

    def get_chunk(self, chunk_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM chunks WHERE id=?", (chunk_id,)).fetchone()

    def find_candidate_chunks(self, project: str | None, any_of: list[str],
                              limit: int = 50) -> list[dict]:
        """查找评测目标候选块，用于区分未收录、未召回与排序失败。"""
        if not any_of:
            return []
        clauses = ["(path LIKE ? OR symbol LIKE ?)" for _ in any_of]
        params: list = []
        for keyword in any_of:
            params.extend([f"%{keyword}%", f"%{keyword}%"])
        sql = (
            "SELECT id, project, path, symbol, start_line, end_line, embedded "
            "FROM chunks WHERE (" + " OR ".join(clauses) + ")"
        )
        if project:
            sql += " AND project=?"
            params.append(project)
        sql += " ORDER BY project, path, start_line LIMIT ?"
        params.append(limit)
        return [
            {
                "chunk_id": row["id"],
                "project": row["project"],
                "path": row["path"],
                "symbol": row["symbol"] or "",
                "start_line": row["start_line"],
                "end_line": row["end_line"],
                "embedded": bool(row["embedded"]),
            }
            for row in self.conn.execute(sql, params)
        ]

    # ---- Spec 结构化记录 ----

    def replace_spec(self, project: str, path: str, spec) -> None:
        """替换某文件对应的 Spec 结构化记录（spec 为 specs.parser.ParsedSpec）。"""
        old = self.conn.execute(
            "SELECT spec_key FROM spec_docs WHERE project=? AND path=?", (project, path)
        ).fetchone()
        self.conn.execute("DELETE FROM spec_docs WHERE project=? AND path=?", (project, path))
        if old:
            key = old["spec_key"]
            self.conn.execute("DELETE FROM spec_tasks WHERE project=? AND spec_key=? AND source_path=?",
                              (project, key, path))
            self.conn.execute("DELETE FROM spec_deps WHERE project=? AND spec_key=? AND source_path=?",
                              (project, key, path))
            self.conn.execute("DELETE FROM spec_code_refs WHERE project=? AND spec_key=? AND source_path=?",
                              (project, key, path))
        self.conn.execute(
            """INSERT INTO spec_docs(project,path,kind,spec_key,title,status,status_raw,created,branch,status_text)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (project, path, spec.kind, spec.key, spec.title, spec.status, spec.status_raw,
             spec.created, spec.branch, spec.status_text),
        )
        for t in spec.tasks:
            self.conn.execute(
                """INSERT OR REPLACE INTO spec_tasks(project,spec_key,task_id,title,done,ordinal,source_path,start_line,end_line)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (project, spec.key, t.task_id, t.title, int(t.done), t.ordinal, path, t.start_line, t.end_line),
            )
        for dep in spec.deps:
            self.conn.execute(
                "INSERT OR REPLACE INTO spec_deps(project,spec_key,depends_on,source_path) VALUES(?,?,?,?)",
                (project, spec.key, dep, path),
            )
        for cp, ln in spec.code_refs:
            self.conn.execute(
                "INSERT OR REPLACE INTO spec_code_refs(project,spec_key,code_path,code_line,source_path) VALUES(?,?,?,?,?)",
                (project, spec.key, cp, ln, path),
            )

    def delete_spec(self, project: str, path: str):
        self.conn.execute("DELETE FROM spec_docs WHERE project=? AND path=?", (project, path))
        for tbl in ("spec_tasks", "spec_deps", "spec_code_refs"):
            self.conn.execute(f"DELETE FROM {tbl} WHERE project=? AND source_path=?", (project, path))

    def spec_docs(self, project: str) -> list:
        return self.conn.execute(
            "SELECT * FROM spec_docs WHERE project=? ORDER BY created, path", (project,)
        ).fetchall()

    def spec_tasks(self, project: str, spec_key: str | None = None) -> list:
        if spec_key:
            return self.conn.execute(
                "SELECT * FROM spec_tasks WHERE project=? AND spec_key=? ORDER BY ordinal",
                (project, spec_key)).fetchall()
        return self.conn.execute(
            "SELECT * FROM spec_tasks WHERE project=? ORDER BY spec_key, ordinal", (project,)
        ).fetchall()

    def spec_deps(self, project: str) -> list:
        return self.conn.execute(
            "SELECT * FROM spec_deps WHERE project=?", (project,)
        ).fetchall()

    # ---- 文档更新提案 ----

    def add_proposal(self, project: str, doc_path: str, title: str, reason: str,
                     evidence: dict, draft: str) -> int:
        cur = self.conn.execute(
            """INSERT INTO doc_proposals(project,doc_path,title,reason,evidence,draft,status,created_at)
               VALUES(?,?,?,?,?,?,'pending',?)""",
            (project, doc_path, title, reason, json.dumps(evidence, ensure_ascii=False), draft, time.time()),
        )
        self.conn.commit()
        return cur.lastrowid

    def proposals(self, project: str | None = None, status: str | None = None) -> list:
        sql, params = "SELECT * FROM doc_proposals WHERE 1=1", []
        if project:
            sql += " AND project=?"; params.append(project)
        if status:
            sql += " AND status=?"; params.append(status)
        return self.conn.execute(sql + " ORDER BY id DESC", params).fetchall()

    def get_proposal(self, pid: int):
        return self.conn.execute("SELECT * FROM doc_proposals WHERE id=?", (pid,)).fetchone()

    def decide_proposal(self, pid: int, status: str):
        self.conn.execute("UPDATE doc_proposals SET status=?, decided_at=? WHERE id=?",
                          (status, time.time(), pid))
        self.conn.commit()

    def update_draft(self, pid: int, draft: str):
        """重生成 pending 提案的草案内容（如升级为 LLM 草案）。"""
        self.conn.execute("UPDATE doc_proposals SET draft=? WHERE id=? AND status='pending'",
                          (draft, pid))
        self.conn.commit()

    def mark_applied(self, pid: int, diff: str):
        self.conn.execute("UPDATE doc_proposals SET status='applied', applied_at=?, diff=? WHERE id=?",
                          (time.time(), diff, pid))
        self.conn.commit()

    # ---- 错误与统计 ----

    def log_error(self, project: str, path: str, stage: str, message: str):
        self.conn.execute(
            "INSERT INTO sync_errors(project,path,stage,message,at) VALUES(?,?,?,?,?)",
            (project, path, stage, message, time.time()),
        )

    def stats(self) -> dict:
        out = {}
        for r in self.conn.execute(
            "SELECT project, COUNT(*) AS n, SUM(status='error') AS errs FROM files GROUP BY project"):
            out[r["project"]] = {"files": r["n"], "error_files": r["errs"] or 0}
        for r in self.conn.execute(
            "SELECT project, COUNT(*) AS n, SUM(embedded=0) AS pending FROM chunks GROUP BY project"):
            out.setdefault(r["project"], {}).update(
                {"chunks": r["n"], "pending_embedding": r["pending"] or 0})
        return out

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.commit()
        self.conn.close()
