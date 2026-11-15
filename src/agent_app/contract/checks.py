"""契约规则的检查实现。

每个检查函数只回答一个问题：这条规则是否被满足。检查器**只读**——
不修改知识源、不修改索引、不生成提案。

需要索引的检查（R-02 / R-05 / R-06）在索引缺失或列尚未迁移时，
返回"不适用"而不是误报。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .declaration import OPTIONAL_FIELDS, SourceDeclaration
from .rules import RULES

#: R-02 的标题链覆盖率阈值。
HEADING_COVERAGE_MIN = 0.95

#: R-04 覆盖性检查里，每个入口最多检查多少个链接目标。
MAX_ENTRY_LINKS = 200

_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


@dataclass
class Finding:
    """一条规则发现。"""

    rule_id: str
    severity: str
    message: str
    location: str = ""
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "title": RULES[self.rule_id].title if self.rule_id in RULES else "",
            "message": self.message,
            "location": self.location,
            "evidence": self.evidence,
        }


# ---- 声明层检查（不需要索引） --------------------------------------------

def check_r01(decl: SourceDeclaration, store=None) -> list[Finding]:
    """R-01：内容边界必须显式声明。"""
    if decl.content_roots:
        return []
    return [Finding(
        rule_id="R-01",
        severity="error",
        message="未声明 content_roots：接入前必须显式声明内容边界"
                '（content_roots: ["."] 表示全库）',
        location=f"{decl.name}",
        evidence={"provides": list(decl.provides)},
    )]


def check_r03(decl: SourceDeclaration, store=None) -> list[Finding]:
    """R-03：意图映射的目标必须存在。"""
    findings: list[Finding] = []
    root = Path(decl.root)
    for intent, patterns in decl.intent_map.items():
        for pattern in patterns:
            if _target_exists(root, pattern):
                continue
            findings.append(Finding(
                rule_id="R-03",
                severity="error",
                message=f"意图「{intent}」映射到的路径模式在本库中不存在：{pattern}",
                location=f"{decl.name}",
                evidence={"intent": intent, "pattern": pattern},
            ))
    return findings


def check_r04(decl: SourceDeclaration, store=None) -> list[Finding]:
    """R-04：入口文档必须存在；并报告未被入口覆盖的分类。"""
    findings: list[Finding] = []
    root = Path(decl.root)
    if not decl.entry_docs:
        findings.append(Finding(
            rule_id="R-04",
            severity="error",
            message="声明了分类结构但未声明 entry_docs："
                    "「这个库整体是什么」类问题无法命中入口",
            location=f"{decl.name}",
        ))
        return findings

    for doc in decl.entry_docs:
        if not (root / doc).exists():
            findings.append(Finding(
                rule_id="R-04",
                severity="error",
                message=f"入口文档不存在：{doc}",
                location=f"{decl.name}",
                evidence={"entry_doc": doc},
            ))

    # 覆盖性只对文档型知识源有意义：代码库的 src/、tests/ 不是"分类"，
    # 用入口文档去覆盖它们没有意义，会变成噪声。
    if not decl.provides_field("heading_path"):
        return findings
    categories = _document_categories_of(root, decl)
    if not categories:
        return findings
    linked = _entry_link_targets(root, decl)
    for category in sorted(categories):
        if any(c == category or c.startswith(category + "/") for c in linked):
            continue
        if any(category in c for c in linked):
            continue
        findings.append(Finding(
            rule_id="R-04",
            severity="warning",
            message=f"分类「{category}」未被任何入口文档覆盖",
            location=f"{decl.name}",
            evidence={"category": category},
        ))
    return findings


def check_r07(decl: SourceDeclaration, store=None) -> list[Finding]:
    """R-07：声明不得超出契约字段集。"""
    unknown = decl.unknown_provides()
    if not unknown:
        return []
    return [Finding(
        rule_id="R-07",
        severity="error",
        message=f"provides 声明了契约字段集之外的字段：{', '.join(unknown)}"
                f"（可用字段：{', '.join(OPTIONAL_FIELDS)}）",
        location=f"{decl.name}",
        evidence={"unknown": unknown},
    )]


# ---- 索引层检查（需要 chunks 表） ----------------------------------------

def check_r02(decl: SourceDeclaration, store=None) -> list[Finding]:
    """R-02：声明的结构能力必须真实存在（标题链覆盖率）。"""
    if not decl.provides_field("heading_path"):
        return []
    if store is None:
        return []
    stats = _heading_stats(store, decl.name)
    if stats is None:
        return []
    total, missing = stats
    if total == 0:
        # 还没建索引 ≠ 没有标题链。索引级规则在没有索引时不适用，
        # 否则 CI（无本地索引）会把每个源都判为不合规。
        return []
    coverage = (total - missing) / total
    if coverage >= HEADING_COVERAGE_MIN:
        return []
    return [Finding(
        rule_id="R-02",
        severity="error",
        message=f"声明 provides 含 heading_path，但 document 块标题链覆盖率仅 "
                f"{coverage:.1%}（{missing}/{total} 缺失），该能力自动降级",
        location=f"{decl.name}",
        evidence={"coverage": round(coverage, 4), "missing": missing, "total": total},
    )]


def check_r05(decl: SourceDeclaration, store=None) -> list[Finding]:
    """R-05：document 块应有标题链（warning）。"""
    if not decl.provides_field("heading_path") or store is None:
        return []
    stats = _heading_stats(store, decl.name)
    if stats is None:
        return []
    total, missing = stats
    if not missing:
        return []
    samples = _missing_heading_samples(store, decl.name)
    return [Finding(
        rule_id="R-05",
        severity="warning",
        message=f"{missing}/{total} 个 document 块没有标题链，检索可信度下降",
        location=f"{decl.name}",
        evidence={"missing": missing, "total": total, "samples": samples},
    )]


def check_r06(decl: SourceDeclaration, store=None) -> list[Finding]:
    """R-06：术语别名必须有出处（warning）。"""
    if not decl.provides_field("aliases") or store is None:
        return []
    terms = decl.terms()
    if not terms:
        return []
    missing: list[str] = []
    for term in terms:
        if not _term_occurs(store, decl.name, term):
            missing.append(term)
    if not missing:
        return []
    return [Finding(
        rule_id="R-06",
        severity="warning",
        message=f"{len(missing)} 个术语在本库正文中查无出处：{', '.join(missing[:10])}"
                + ("…" if len(missing) > 10 else ""),
        location=f"{decl.name}",
        evidence={"missing": missing},
    )]


# ---- 辅助 ----

def _target_exists(root: Path, pattern: str) -> bool:
    """路径模式在知识源根下是否有匹配目标。"""
    literal = root / pattern
    if literal.exists():
        return True
    try:
        return next(root.glob(pattern), None) is not None
    except (ValueError, OSError):
        return False


def _document_categories_of(root: Path, decl: SourceDeclaration) -> set[str]:
    """含文档的一级分类目录。

    只统计"装文档的目录"——代码库的 src/、tests/ 里没有 .md，
    拿入口文档去覆盖它们没有意义，只会产生噪声。
    """
    out: set[str] = set()
    if not root.is_dir():
        return out
    try:
        children = [c for c in root.iterdir() if c.is_dir() and not c.name.startswith(".")]
    except OSError:
        return out
    for child in children:
        try:
            has_doc = any(1 for _ in child.rglob("*.md"))
        except OSError:
            has_doc = False
        if has_doc:
            out.add(child.name)
    return out


def _entry_link_targets(root: Path, decl: SourceDeclaration) -> set[str]:
    """入口文档链接到的相对路径集合（用于分类覆盖性判断）。"""
    out: set[str] = set()
    for doc in decl.entry_docs:
        path = root / doc
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        base = path.parent
        for raw in _LINK_RE.findall(text)[:MAX_ENTRY_LINKS]:
            target = raw.split("#", 1)[0].strip()
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            try:
                resolved = (base / target).resolve()
                rel = resolved.relative_to(root.resolve())
            except (OSError, ValueError):
                continue
            out.add(str(rel).replace("\\", "/"))
    return out


def _heading_stats(store, project: str) -> tuple[int, int] | None:
    """(document 块总数, 缺标题链的块数)；表尚未迁移时返回 None。"""
    try:
        row = store.conn.execute(
            "SELECT COUNT(*) AS total,"
            " SUM(CASE WHEN heading_path IS NULL OR heading_path='' OR heading_path='[]'"
            "          THEN 1 ELSE 0 END) AS missing"
            " FROM chunks WHERE project=? AND source_type='document'",
            (project,),
        ).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    return int(row["total"] or 0), int(row["missing"] or 0)


def _missing_heading_samples(store, project: str, limit: int = 5) -> list[str]:
    try:
        rows = store.conn.execute(
            "SELECT DISTINCT path FROM chunks WHERE project=? AND source_type='document'"
            " AND (heading_path IS NULL OR heading_path='' OR heading_path='[]') LIMIT ?",
            (project, limit),
        ).fetchall()
    except Exception:
        return []
    return [r["path"] for r in rows]


def _term_occurs(store, project: str, term: str) -> bool:
    try:
        row = store.conn.execute(
            "SELECT 1 FROM chunks WHERE project=? AND content LIKE ? LIMIT 1",
            (project, f"%{term}%"),
        ).fetchone()
    except Exception:
        return True  # 查不了就当通过，不制造噪声
    return row is not None


#: 规则 ID → 检查函数。声明层的检查不需要索引。
CHECKS = {
    "R-01": check_r01,
    "R-02": check_r02,
    "R-03": check_r03,
    "R-04": check_r04,
    "R-05": check_r05,
    "R-06": check_r06,
    "R-07": check_r07,
}


def run_checks(decl: SourceDeclaration, store=None) -> list[Finding]:
    """跑全部适用规则，返回 findings。"""
    findings: list[Finding] = []
    for rule_id, rule in RULES.items():
        if rule.applies_to and not any(decl.provides_field(f) for f in rule.applies_to):
            continue  # 不适用
        findings.extend(CHECKS[rule_id](decl, store))
    return findings
