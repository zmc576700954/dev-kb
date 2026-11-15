"""契约服务：只读地回答"这个知识源符合契约吗、缺什么"。

服务不做任何写入：不改知识源、不改索引、不生成提案、不落盘。
"""

from __future__ import annotations

from pathlib import Path

from ..indexing.metadata_store import MetadataStore
from .checks import run_checks
from .declaration import OPTIONAL_FIELDS, SourceDeclaration, load_declarations
from .rules import RULES

ROOT = Path(__file__).resolve().parents[3]


class ContractService:
    """知识源接入契约的只读校验服务。"""

    def __init__(self, store: MetadataStore | None = None, db_path: str | Path | None = None,
                 config: str | Path | None = None):
        self._owns_store = store is None
        self.store = store if store is not None else MetadataStore(db_path or ROOT / "data/meta.db")
        self.config = config

    # ---- 声明 ----

    def sources(self) -> list[dict]:
        """列出已登记的知识源及其声明摘要。"""
        out = []
        for name, decl in load_declarations(self.config).items():
            out.append({
                "name": name,
                "path": decl.root,
                "provides": list(decl.provides),
                "content_roots": list(decl.content_roots),
                "entry_docs": list(decl.entry_docs),
                "intent_map": {k: list(v) for k, v in decl.intent_map.items()},
                "terms": len(decl.terms()),
            })
        return out

    def declaration(self, name: str) -> SourceDeclaration | None:
        return load_declarations(self.config).get(name)

    # ---- 校验 ----

    def validate(self, name: str) -> dict:
        """校验单个知识源，返回发现与判定。"""
        decl = self.declaration(name)
        if decl is None:
            return {
                "source": name,
                "verdict": "unknown",
                "summary": {"errors": 0, "warnings": 0, "reviews": 0},
                "findings": [],
                "message": f"未登记的知识源：{name}",
            }
        findings = run_checks(decl, self.store)
        errors = sum(1 for f in findings if f.severity == "error")
        warnings = sum(1 for f in findings if f.severity == "warning")
        verdict = "fail" if errors else ("warn" if warnings else "pass")
        return {
            "source": name,
            "path": decl.root,
            "verdict": verdict,
            "provides": list(decl.provides),
            "summary": {"errors": errors, "warnings": warnings, "reviews": 0},
            "findings": [f.to_dict() for f in findings],
        }

    def validate_all(self) -> list[dict]:
        return [self.validate(name) for name in load_declarations(self.config)]

    # ---- 规则与辅助 ----

    def rules(self) -> list[dict]:
        return [
            {
                "id": r.id,
                "severity": r.severity,
                "title": r.title,
                "statement": r.statement,
                "applies_to": list(r.applies_to),
            }
            for r in RULES.values()
        ]

    def suggest_hints(self, name: str) -> dict:
        """列出本库的一级目录，作为写 intent_map 时的候选。"""
        decl = self.declaration(name)
        if decl is None:
            return {"source": name, "directories": [], "message": f"未登记的知识源：{name}"}
        root = Path(decl.root)
        dirs: list[str] = []
        if root.is_dir():
            for child in sorted(root.iterdir()):
                if child.is_dir() and not child.name.startswith("."):
                    dirs.append(child.name)
        return {
            "source": name,
            "directories": dirs,
            "current_intent_map": {k: list(v) for k, v in decl.intent_map.items()},
            "hint": "把中文意图映射到上面的目录名，例如 "
                    "intent_map: {部署: [\"09-部署与生态\"]}",
        }

    def close(self):
        if self._owns_store:
            self.store.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def contract_fields() -> list[str]:
    """契约定义的可选结构字段（与声明模型同源）。"""
    return list(OPTIONAL_FIELDS)
