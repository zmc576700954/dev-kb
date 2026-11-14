"""全库链接图：入链/出链/断链/孤儿，供 checks_project 使用。"""

from __future__ import annotations

from dataclasses import dataclass, field

from .checks_doc import _resolve, _dir_of
from .docmodel import ParsedDoc


@dataclass
class LinkGraph:
    out_links: dict[str, list[str]] = field(default_factory=dict)   # 文档 -> 归一目标列表
    in_links: dict[str, set[str]] = field(default_factory=dict)     # 目标 -> 来源文档集合
    unresolved: list[tuple[str, int, str]] = field(default_factory=list)  # (来源, 行号, 原始目标)


def build_graph(docs: dict[str, ParsedDoc], path_exists) -> LinkGraph:
    g = LinkGraph()
    for rel, doc in docs.items():
        base = _dir_of(doc)
        targets: list[str] = []
        for lk in doc.links:
            if lk.kind != "doc":
                continue
            if not lk.target.startswith(("./", "../")):
                g.unresolved.append((rel, lk.line, lk.target))
                continue
            resolved = _resolve(base, lk.target)
            if resolved is None:
                g.unresolved.append((rel, lk.line, lk.target))
                continue
            path_only = resolved.split("#", 1)[0]
            if not path_exists(path_only):
                g.unresolved.append((rel, lk.line, lk.target))
                continue
            targets.append(path_only)
            g.in_links.setdefault(path_only, set()).add(rel)
        g.out_links[rel] = targets
    return g
