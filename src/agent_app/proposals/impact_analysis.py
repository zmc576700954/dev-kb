"""文档影响分析：Git 变更 → 受影响文档 → 带来源的更新提案。

证据两通道：
1. spec_code_refs：Spec/计划文档中显式引用了变更文件（`path:line` 引用，M4 提取）。
2. 正文提及：任何文档块内容中包含变更文件路径。

提案只生成草案与证据，绝不直接写文件。
"""

from __future__ import annotations

import subprocess
import time
from collections import defaultdict
from datetime import datetime

from ..indexing.metadata_store import MetadataStore

CODE_EXT = (".ts", ".tsx", ".js", ".php", ".py", ".rs", ".vue", ".css", ".sql")


def git_changed_files(root: str, since: str | None = None, ref: str | None = None,
                      include_uncommitted: bool = True) -> list[dict]:
    """获取变更文件：committed（git log/diff）+ uncommitted（git status）。"""
    changed: dict[str, str] = {}  # path -> 变更来源

    def run(*args) -> str:
        out = subprocess.run(["git", "-C", root, *args],
                             capture_output=True, text=True, timeout=30)
        return out.stdout if out.returncode == 0 else ""

    if ref:
        for line in run("diff", "--name-only", ref).splitlines():
            if line.strip():
                changed[line.strip()] = f"diff {ref}"
    elif since:
        for line in run("log", f"--since={since}", "--name-only", "--pretty=format:").splitlines():
            if line.strip():
                changed[line.strip()] = f"commit since {since}"
    else:
        for line in run("log", "-3", "--name-only", "--pretty=format:").splitlines():
            if line.strip():
                changed[line.strip()] = "recent commits"

    if include_uncommitted:
        for line in run("status", "--porcelain").splitlines():
            if len(line) > 3:
                p = line[3:].split(" -> ")[-1].strip()
                if p:
                    changed.setdefault(p, "uncommitted")

    head = run("rev-parse", "--short", "HEAD").strip()
    return [{"path": p, "how": h, "commit": head} for p, h in sorted(changed.items())]


def analyze_impact(store: MetadataStore, project: str, root: str,
                   since: str | None = None, ref: str | None = None) -> list[dict]:
    """返回受影响文档清单（含证据），不生成提案记录。"""
    changed = git_changed_files(root, since, ref)
    code_changes = [c for c in changed if c["path"].endswith(CODE_EXT)]
    if not code_changes:
        return []

    impacts: dict[str, dict] = defaultdict(lambda: {"evidence": []})
    for c in code_changes:
        path = c["path"]
        # 通道 1：Spec 显式引用
        for r in store.conn.execute(
            "SELECT DISTINCT source_path, code_line FROM spec_code_refs WHERE project=? AND code_path=?",
            (project, path)):
            impacts[r["source_path"]]["evidence"].append({
                "type": "spec_ref", "changed_file": path,
                "ref_line": r["code_line"], "how": c["how"], "commit": c["commit"],
            })
        # 通道 2：文档正文提及该路径
        for r in store.conn.execute(
            "SELECT DISTINCT path FROM chunks WHERE project=? AND source_type='document' "
            "AND content LIKE ? LIMIT 20", (project, f"%{path}%")):
            if r["path"] == path:
                continue
            impacts[r["path"]]["evidence"].append({
                "type": "content_mention", "changed_file": path,
                "how": c["how"], "commit": c["commit"],
            })
    return [{"doc_path": doc, "evidence": ev["evidence"]} for doc, ev in sorted(impacts.items())]


def draft_proposal(store: MetadataStore, project: str, doc_path: str,
                   evidence: list[dict]) -> int:
    """生成提案记录（pending），草案为结构化影响说明，需人工审批后才写入。"""
    files = sorted({e["changed_file"] for e in evidence})
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [
        "",
        "---",
        "",
        f"## 变更影响记录（{today} · 由研发知识库 Agent 提案，人工审批后写入）",
        "",
        "以下代码文件发生变更，本文档可能受影响：",
        "",
    ]
    for e in evidence:
        loc = f"（本文档第 {e['ref_line']} 行引用）" if e.get("ref_line") else "（正文提及）"
        lines.append(f"- `{e['changed_file']}` — {e['how']} @ {e.get('commit', '')} {loc}")
    lines += ["", "**建议动作**：核对上述引用位置的描述与行号是否仍成立；若不成立请更新后移除本记录。", ""]
    reason = f"{len(files)} 个代码文件变更，经由 {len(evidence)} 处引用关联到本文档"
    return store.add_proposal(
        project, doc_path, f"变更影响：{', '.join(f.split('/')[-1] for f in files[:3])}",
        reason, {"changed_files": files, "evidence": evidence}, "\n".join(lines))
