"""get_project_profile：技术栈 + 目录结构 + 项目入口 + 关键文档。

事实三层（对照规划第三节）：
1. 确定事实：清单、锁文件、目录结构（本工具输出，全部带来源）。
2. 文档说明：README / 架构文档（列出路径，供检索层引用）。
3. 模型归纳：留给 LLM 编排层，本工具不输出，防止建议伪装成事实。
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from ..indexing.metadata_store import MetadataStore
from ..ingestion.manifest_parser import parse_manifest, python_fallback
from .projects import project_root

_ENTRY_CANDIDATES = [
    "artisan", "src/main.rs", "src-tauri/src/main.rs", "src/main.tsx", "src/main.ts",
    "src/index.ts", "electron/main.ts", "main.py", "app.py", "index.html",
]
_DOC_CANDIDATES = ("readme", "claude.md", "architecture", "架构", "技术参考", "设计")


def get_project_profile(store: MetadataStore, project: str,
                        config: str | None = None) -> dict:
    root = project_root(project, config)
    if not root:
        return {"error": f"未登记的项目: {project}"}

    # 1. 清单事实（实时读取原文件）
    manifests = [r["path"] for r in store.conn.execute(
        "SELECT path FROM chunks WHERE project=? AND source_type='config' AND "
        "(path LIKE '%package.json' OR path LIKE '%composer.json' OR path LIKE '%Cargo.toml') "
        "GROUP BY path", (project,))]
    stacks = []
    for rel in sorted(manifests, key=lambda p: (p.count("/"), p)):  # 根目录清单优先
        facts = parse_manifest(root, rel)
        if facts and (facts.framework or facts.key_dependencies):
            stacks.append(facts)
    if not stacks:
        fb = python_fallback(root)
        if fb:
            stacks.append(fb)

    # 2. 目录结构（来自索引文件表，已应用排除清单）
    dirs: Counter = Counter()
    langs: Counter = Counter()
    for r in store.conn.execute(
        "SELECT path, language, source_type FROM chunks WHERE project=? GROUP BY path, language",
        (project,)):
        top = r["path"].split("/")[0] if "/" in r["path"] else "(根目录)"
        dirs[top] += 1
        if r["language"]:
            langs[r["language"]] += 1

    # 3. 入口与文档
    files = {r["path"] for r in store.conn.execute(
        "SELECT DISTINCT path FROM chunks WHERE project=?", (project,))}
    entries = [e for e in _ENTRY_CANDIDATES if e in files]
    docs = sorted(p for p in files
                  if p.endswith(".md") and any(k in p.lower() for k in _DOC_CANDIDATES))[:15]

    return {
        "project": project,
        "root": root,
        "stacks": [{
            "language": s.language, "framework": s.framework, "runtime": s.runtime,
            "key_dependencies": s.key_dependencies, "dev_tools": s.dev_tools,
            "scripts": s.scripts, "source": s.source,
        } for s in stacks],
        "structure": [{"dir": d, "files": n} for d, n in dirs.most_common(20)],
        "languages": dict(langs.most_common()),
        "entry_points": entries,
        "key_documents": docs,
        "file_count": len(files),
    }
