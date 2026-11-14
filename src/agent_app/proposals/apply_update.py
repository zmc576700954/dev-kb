"""提案应用：审批通过后写入文档，生成 diff，触发增量索引。

硬性规则：只有 status == 'approved' 的提案才能应用。
"""

from __future__ import annotations

import difflib
from pathlib import Path

from ..indexing.metadata_store import MetadataStore


def apply_proposal(store: MetadataStore, root: str, pid: int) -> dict:
    p = store.get_proposal(pid)
    if not p:
        return {"error": f"提案不存在: {pid}"}
    if p["status"] == "applied":
        return {"error": "提案已应用过，不能重复写入"}
    if p["status"] != "approved":
        return {"error": f"提案状态为 {p['status']}，必须先审批（approve）才能写入"}

    full = Path(root) / p["doc_path"]
    if not full.is_file():
        return {"error": f"文档不存在: {p['doc_path']}"}

    old = full.read_text(encoding="utf-8")
    new = old.rstrip("\n") + "\n" + p["draft"]
    diff = "\n".join(difflib.unified_diff(
        old.splitlines(), new.splitlines(),
        fromfile=f"a/{p['doc_path']}", tofile=f"b/{p['doc_path']}", lineterm="",
    ))
    full.write_text(new, encoding="utf-8")
    store.mark_applied(pid, diff)
    return {"applied": p["doc_path"], "diff_lines": diff.count("\n") + 1, "diff": diff}
