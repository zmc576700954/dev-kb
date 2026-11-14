"""read_source：按行号读取项目真实文件。

原则（对照规划）：回答中的代码段必须由程序读取原文件返回，
不引用索引里的旧副本；若文件自索引后已变化，明确提示。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..indexing.metadata_store import MetadataStore
from .projects import project_root


def read_source(store: MetadataStore, project: str, path: str,
                start_line: int = 1, end_line: int | None = None,
                config: str | None = None) -> dict:
    root = project_root(project, config)
    if not root:
        return {"error": f"未登记的项目: {project}"}
    root_path = Path(root).resolve()
    if Path(path).is_absolute():
        return {"error": "path 必须是项目内的相对路径"}
    full = (root_path / path).resolve()
    try:
        full.relative_to(root_path)
    except ValueError:
        return {"error": "path 不能越过项目根目录"}
    if not full.is_file():
        return {"error": f"文件不存在: {project}/{path}"}

    raw = full.read_bytes()
    text = raw.decode("utf-8", "replace")
    lines = text.splitlines()
    end_line = min(end_line or len(lines), len(lines))
    start_line = max(1, start_line)

    # 新鲜度校验：与索引记录的内容哈希对比
    digest = hashlib.sha1(raw).hexdigest()
    rec = store.get_file(project, path)
    stale = bool(rec and rec["content_hash"] != digest)

    numbered = [f"{i}\t{lines[i - 1]}" for i in range(start_line, end_line + 1)]
    return {
        "project": project,
        "path": path,
        "start_line": start_line,
        "end_line": end_line,
        "total_lines": len(lines),
        "git_commit": rec["git_commit"] if rec else "",
        "stale_since_index": stale,
        "content": "\n".join(numbered),
    }
