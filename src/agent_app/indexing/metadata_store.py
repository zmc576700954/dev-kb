"""SQLite 元数据存储 + FTS5 关键词索引。

表：
- files：文件清单、内容哈希、同步状态、git 提交
- chunks：知识块原文与契约元数据（category / doc_role / aliases）
- chunk_fts：FTS5 全文索引（symbol / content / path / project）
- chunk_headings：FTS5 标题链索引（独立召回通道）
- sync_errors：解析失败记录（不静默跳过）

契约字段（category / doc_role / aliases）是"知识源接入契约"在索引侧的落点：
声明里 provides 的字段在这里有对应列，检索器才能真正消费它们。
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
    category TEXT DEFAULT '',           -- 契约字段：所属分类（路径首段）
    doc_role TEXT DEFAULT '',           -- 契约字段：文档角色（来自声明）
    aliases TEXT DEFAULT '[]',          -- 契约字段：命中正文的术语别名
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
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_headings USING fts5(
    heading, path, project,
    tokenize='unicode61'
);
CREATE TABLE IF NOT EXISTS sync_errors (
    project TEXT, path TEXT, stage TEXT, message TEXT, at REAL
);
"""

#: chunks 表的契约字段列（迁移与回填用）。
CONTRACT_COLUMNS = ("category", "doc_role", "aliases")


class MetadataStore:
    def __init__(self, db_path: str | Path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """为既有索引补上契约字段列（不重建索引、不重算向量）。

        列补齐后才能建索引——旧库里没有这些列。
        """
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(chunks)")}
        for name in CONTRACT_COLUMNS:
            if name in cols:
                continue
            default = "'[]'" if name == "aliases" else "''"
            self.conn.execute(f"ALTER TABLE chunks ADD COLUMN {name} TEXT DEFAULT {default}")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chunks_category ON chunks(project, category)"
        )

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
        return ids

    # ---- chunks ----

    def replace_chunks(self, project: str, path: str, chunks: list[dict]) -> list[int]:
        """替换某文件的全部块，返回新块 id 列表。"""
        self.delete_file_chunks_only(project, path)
        ids = []
        for c in chunks:
            cur = self.conn.execute(
                """INSERT INTO chunks(project,path,source_type,language,symbol,heading_path,
                                      category,doc_role,aliases,
                                      start_line,end_line,content,embedded)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0)""",
                (project, path, c["source_type"], c.get("language", ""), c.get("symbol", ""),
                 json.dumps(c.get("heading_path", []), ensure_ascii=False),
                 c.get("category", ""), c.get("doc_role", ""),
                 json.dumps(c.get("aliases", []), ensure_ascii=False),
                 c.get("start_line", 0), c.get("end_line", 0), c["content"]),
            )
            cid = cur.lastrowid
            self._index_chunk(cid, project, path, c)
            ids.append(cid)
        return ids

    def _index_chunk(self, cid: int, project: str, path: str, c: dict) -> None:
        """把一个块写进全文索引与标题链索引。

        标题链同时进两处：拼进 chunk_fts.content（与正文同等权重），
        以及独立的 chunk_headings 表（可单独调权、可单独归因）。
        """
        from .fts_tokenizer import fts_tokenize
        heading_text = " ".join(c.get("heading_path") or [])
        searchable = fts_tokenize(
            f'{c.get("symbol", "")} {path} {heading_text} {c["content"]}'
        )
        self.conn.execute(
            "INSERT INTO chunk_fts(rowid, symbol, content, path, project) VALUES(?,?,?,?,?)",
            (cid, fts_tokenize(c.get("symbol", "")), searchable, fts_tokenize(path), project),
        )
        if heading_text:
            self.conn.execute(
                "INSERT INTO chunk_headings(rowid, heading, path, project) VALUES(?,?,?,?)",
                (cid, fts_tokenize(heading_text), fts_tokenize(path), project),
            )

    def delete_file_chunks_only(self, project: str, path: str) -> list[int]:
        ids = [r["id"] for r in self.conn.execute(
            "SELECT id FROM chunks WHERE project=? AND path=?", (project, path))]
        if ids:
            q = ",".join("?" * len(ids))
            self.conn.execute(f"DELETE FROM chunk_fts WHERE rowid IN ({q})", ids)
            self.conn.execute(f"DELETE FROM chunk_headings WHERE rowid IN ({q})", ids)
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

    def heading_search(self, query: str, project: str | None = None,
                       limit: int = 10, or_fallback: bool = True) -> list[sqlite3.Row]:
        """标题链通道：只匹配文档标题里的词，与正文通道可分别调权与归因。

        AND 优先。``or_fallback=True`` 时落空后逐 token 降级为 OR（同 FTS 通道口径）
        —— 但宽松降级会捞进大量只命中单个 token 的弱候选，挤掉正确答案；
        标题通道要的是精度（问"核心架构"命中"### 模块"这种父标题命中子节），
        因此降级可关。
        """
        from .fts_tokenizer import fts_query, fts_tokenize
        rows = self._heading_run(fts_query(query), project, limit)
        if rows or not or_fallback:
            return rows
        tokens = [t for t in dict.fromkeys(fts_tokenize(query).split()) if len(t) >= 2][:8]
        if not tokens:
            return []
        scores: dict[int, float] = {}
        counts: dict[int, int] = {}
        for t in tokens:
            for r in self._heading_run(f'"{t}"', project, 200):
                scores[r["id"]] = scores.get(r["id"], 0.0) + r["rank"]
                counts[r["id"]] = counts.get(r["id"], 0) + 1
        top = sorted(counts, key=lambda i: (counts[i], -scores.get(i, 0)), reverse=True)[:limit]
        return [self.get_chunk(i) for i in top]

    def _heading_run(self, match: str, project: str | None, limit: int) -> list[sqlite3.Row]:
        sql = """SELECT c.*, bm25(chunk_headings) AS rank FROM chunk_headings
                 JOIN chunks c ON c.id = chunk_headings.rowid
                 WHERE chunk_headings MATCH ?"""
        params: list = [match]
        if project:
            sql += " AND chunk_headings.project = ?"
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

    def alias_search(self, term: str, project: str | None = None, limit: int = 10) -> list[sqlite3.Row]:
        """术语通道：查询词命中该块声明的术语别名（如「低秩适配」→ LoRA）。"""
        sql = "SELECT * FROM chunks WHERE aliases LIKE ?"
        params: list = [f'%"{term}"%']
        if project:
            sql += " AND project = ?"
            params.append(project)
        sql += " ORDER BY LENGTH(path) LIMIT ?"
        params.append(limit)
        return self.conn.execute(sql, params).fetchall()

    def rebuild_fts(self):
        """清空并用当前分词器重建全文索引与标题链索引。

        分词器升级、标题链进正文、或契约字段回填之后调用；只重写索引，
        不触碰向量。
        """
        from .fts_tokenizer import fts_tokenize
        self.conn.execute("DELETE FROM chunk_fts")
        self.conn.execute("DELETE FROM chunk_headings")
        rows = self.conn.execute(
            "SELECT id, project, path, source_type, symbol, heading_path, content FROM chunks"
        ).fetchall()
        for r in rows:
            try:
                heading = json.loads(r["heading_path"] or "[]")
            except (TypeError, ValueError):
                heading = []
            self._index_chunk(r["id"], r["project"], r["path"], {
                "symbol": r["symbol"],
                "heading_path": heading,
                "content": r["content"],
            })
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

    # ---- 契约字段 ----

    def heading_coverage(self, project: str) -> tuple[int, int]:
        """(document 块总数, 缺标题链的块数)。"""
        row = self.conn.execute(
            "SELECT COUNT(*) AS total,"
            " SUM(CASE WHEN heading_path IS NULL OR heading_path='' OR heading_path='[]'"
            "          THEN 1 ELSE 0 END) AS missing"
            " FROM chunks WHERE project=? AND source_type='document'",
            (project,),
        ).fetchone()
        return int(row["total"] or 0), int(row["missing"] or 0)

    def apply_sections(self, project: str, path: str,
                       sections: list[tuple[int, list[str]]]) -> int:
        """按章节起点回填该文件所有块的 heading_path。

        不动块边界、不动正文、不重算向量——只改 heading_path 一列。
        块归属的规则与切块器一致：取"章节起点 ≤ 块起始行"的最后一节。
        """
        if not sections:
            return 0
        rows = self.conn.execute(
            "SELECT id, start_line FROM chunks WHERE project=? AND path=?",
            (project, path)).fetchall()
        updated = 0
        for r in rows:
            start = r["start_line"]
            chosen: list[str] | None = None
            for sec_start, sec_path in sections:
                if sec_start <= start:
                    chosen = sec_path
                else:
                    break
            if chosen is None:
                continue
            self.conn.execute(
                "UPDATE chunks SET heading_path=? WHERE id=?",
                (json.dumps(chosen, ensure_ascii=False), r["id"]))
            updated += 1
        return updated

    def document_files(self, project: str) -> list[str]:
        """该源已入库的文档文件路径。"""
        rows = self.conn.execute(
            "SELECT DISTINCT path FROM chunks WHERE project=? AND source_type='document'"
            " ORDER BY path", (project,)).fetchall()
        return [r["path"] for r in rows]

    def set_preamble_heading(self, project: str, path: str, heading: str) -> int:
        """给文件里"无标题"的前导块补上文档首个标题。

        切块器把 frontmatter 切成独立块时，那块原本没有任何标题信号。
        补标题只改 heading_path 一列——不动块边界、不动正文、不重算向量。
        """
        if not heading:
            return 0
        cur = self.conn.execute(
            "UPDATE chunks SET heading_path=?"
            " WHERE project=? AND path=? AND start_line=1"
            " AND (heading_path IS NULL OR heading_path='' OR heading_path='[]')",
            (json.dumps([heading], ensure_ascii=False), project, path),
        )
        return cur.rowcount

    def files_with_preamble_chunk(self, project: str) -> list[str]:
        """列出"存在无标题前导块"的文档路径。"""
        rows = self.conn.execute(
            "SELECT DISTINCT path FROM chunks WHERE project=? AND source_type='document'"
            " AND start_line=1 AND (heading_path IS NULL OR heading_path='' OR heading_path='[]')",
            (project,),
        ).fetchall()
        return [r["path"] for r in rows]

    def backfill_structure(self, project: str, category_of, role_of, alias_of) -> int:
        """按声明回填契约字段（category / doc_role / aliases）。

        不需要重扫源文件、不需要重算向量——只改这三列的取值。
        """
        rows = self.conn.execute(
            "SELECT id, path, content FROM chunks WHERE project=?", (project,)
        ).fetchall()
        for r in rows:
            self.conn.execute(
                "UPDATE chunks SET category=?, doc_role=?, aliases=? WHERE id=?",
                (category_of(r["path"]), role_of(r["path"]),
                 json.dumps(alias_of(r["path"], r["content"]), ensure_ascii=False), r["id"]),
            )
        self.conn.commit()
        return len(rows)

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
