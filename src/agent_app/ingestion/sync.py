"""全量与增量同步：扫描 → 哈希比对 → 切块 → 入库 → 生成向量。

规则：
- 新文件：解析、切块、入库、生成向量。
- 内容变化（content_hash 不同）：删旧块、重建新块。
- 文件删除：同步删除块、FTS、向量与文件记录。
- 解析失败：记录 sync_errors，不静默跳过。
- 回答层应读取原文件，本模块只维护索引。

契约字段（category / doc_role / aliases）在这里按声明填入块行，
索引侧的契约字段因此与声明始终一致。
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

from ..indexing.metadata_store import MetadataStore
from .code_parser import chunk_code
from .document_parser import chunk_markdown
from .scanner import scan_project

EMBED_BATCH = 64


def _git_commit(root: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", root, "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def _chunk_language(language: str, path: str) -> str:
    lower = path.lower()
    if lower.endswith((".tsx", ".jsx")):
        return "tsx"
    return language


class SyncEngine:
    def __init__(self, store: MetadataStore, vector_store=None, embed: bool = True):
        self.store = store
        self.vs = vector_store
        self.embed = embed and vector_store is not None
        self._pending_ids: list[int] = []
        self._pending_texts: list[str] = []
        self._pending_payloads: list[dict] = []

    # ---- 主流程 ----

    def sync_project(self, name: str, root: str, extra_exclude_dirs: list[str] | None = None,
                     decl=None) -> dict:
        """同步一个知识源。

        ``decl`` 是 :class:`~agent_app.contract.declaration.SourceDeclaration`；
        给了就按声明限定内容边界、填契约字段。
        """
        root = os.path.expandvars(os.path.expanduser(root))
        content_roots = list(decl.content_roots) if decl else None
        report = scan_project(name, root, extra_exclude_dirs, content_roots=content_roots)
        commit = _git_commit(root)
        seen: set[str] = set()
        result = {"project": name, "new": 0, "changed": 0, "unchanged": 0,
                  "deleted": 0, "errors": 0, "chunks": 0, "fallback_files": 0}

        for f in report.files:
            rel = f.path
            seen.add(rel)
            full = os.path.join(root, rel)
            try:
                raw = Path(full).read_bytes()
            except OSError as e:
                self.store.log_error(name, rel, "read", str(e))
                result["errors"] += 1
                continue
            digest = hashlib.sha1(raw).hexdigest()
            existing = self.store.get_file(name, rel)
            if existing and existing["content_hash"] == digest:
                result["unchanged"] += 1
                continue

            # 内容变化：先删旧块（含向量）
            if existing:
                old_ids = self.store.delete_file_chunks_only(name, rel)
                if self.vs:
                    self.vs.delete(old_ids)
                result["changed"] += 1
            else:
                result["new"] += 1

            try:
                chunks, used_fallback = self._chunk_file(name, rel, raw, f.category, f.language)
            except Exception as e:  # 解析失败必须记录
                self.store.log_error(name, rel, "parse", repr(e))
                self.store.upsert_file(name, rel, f.size and os.path.getmtime(full) or 0,
                                       f.size, digest, commit, status="error")
                result["errors"] += 1
                continue

            ids = self.store.replace_chunks(
                name, rel, [self._chunk_to_row(name, rel, c, decl) for c in chunks])
            self.store.upsert_file(name, rel, os.path.getmtime(full), f.size, digest, commit)
            result["chunks"] += len(ids)
            result["fallback_files"] += 1 if used_fallback else 0
            self._queue_embedding(name, rel, ids, chunks)
            self.store.commit()  # 逐文件提交：长同步被中断后可断点续跑，避免重复切块

        # 已删除文件
        for gone in self.store.known_paths(name) - seen:
            ids = self.store.delete_file(name, gone)
            if self.vs:
                self.vs.delete(ids)
            result["deleted"] += 1

        self._flush_embedding()
        self.store.commit()
        return result

    # ---- 切块 ----

    def _chunk_file(self, project: str, rel: str, raw: bytes, category: str, language: str):
        text = raw.decode("utf-8", "replace")
        if category == "document":
            return chunk_markdown(text), False
        if category == "code":
            return chunk_code(text, _chunk_language(language, rel))
        # config / data / other：整体成块（小文件事实）
        lines = text.splitlines()
        from .document_parser import Chunk
        return [Chunk(text[:8000], "config" if category == "config" else "data",
                      start_line=1, end_line=len(lines))], False

    def _chunk_to_row(self, project: str, rel: str, c, decl=None) -> dict:
        """块 → 索引行，附带契约字段。"""
        from ..contract.declaration import aliases_in_content, category_of
        terms = decl.terms() if decl else {}
        role = decl.role_of(rel) if decl else ""
        return {
            "source_type": c.source_type, "language": c.language, "symbol": c.symbol,
            "heading_path": c.heading_path, "start_line": c.start_line,
            "end_line": c.end_line, "content": c.content,
            "category": category_of(rel),
            "doc_role": role,
            "aliases": aliases_in_content(c.content, terms),
        }

    # ---- Embedding ----

    def _queue_embedding(self, project: str, rel: str, ids: list[int], chunks: list):
        if not self.embed:
            return
        for cid, c in zip(ids, chunks):
            self._pending_ids.append(cid)
            self._pending_texts.append(c.content)
            self._pending_payloads.append({
                "project": project, "path": rel, "symbol": c.symbol,
                "start_line": c.start_line, "end_line": c.end_line,
                "source_type": c.source_type,
            })
        if len(self._pending_ids) >= EMBED_BATCH:
            self._flush_embedding()

    def _flush_embedding(self):
        if not self.embed or not self._pending_ids:
            return
        from ..indexing import embedder
        vectors = embedder.embed_texts(self._pending_texts)
        self.vs.upsert(self._pending_ids, vectors, self._pending_payloads)
        self.store.mark_embedded(self._pending_ids)
        self._pending_ids, self._pending_texts, self._pending_payloads = [], [], []

    def embed_pending(self, project: str | None = None, batch: int = 256) -> int:
        """补挂中断时未生成向量的块（可重复执行直至返回 0）。"""
        if not self.embed:
            return 0
        from ..indexing import embedder
        rows = self.store.unembedded_chunks(project, batch)
        if not rows:
            return 0
        ids = [r["id"] for r in rows]
        vectors = embedder.embed_texts([r["content"] for r in rows])
        full = [self.store.get_chunk(i) for i in ids]
        payloads = [{
            "project": r["project"], "path": r["path"], "symbol": r["symbol"],
            "start_line": r["start_line"], "end_line": r["end_line"],
            "source_type": r["source_type"],
        } for r in full if r]
        self.vs.upsert(ids, vectors, payloads)
        self.store.mark_embedded(ids)
        self.store.commit()
        return len(ids)
