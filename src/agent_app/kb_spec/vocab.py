"""kb_spec 共享词表：Finding 与三档严重级。

三档输出（设计决策 Q17）：
- error   ：规范「必须/禁止」级违规；写路径上直接拒绝提案
- warning ：规范「建议」级偏离；不拒绝，仅提示
- review  ：规范条目含主观判断，无法纯机械判定；附证据指针，由运行时 agent/人工裁决
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_REVIEW = "review"
SEVERITIES = (SEVERITY_ERROR, SEVERITY_WARNING, SEVERITY_REVIEW)


@dataclass
class Finding:
    rule_id: str          # KB-<章节>-<slug>，唯一权威定义见 rules.py
    severity: str         # error | warning | review
    message: str
    location: str = ""    # 如 "行 12" 或 "02-技术详解/01-ComfyUI.md:12"
    fix_hint: str = ""
    evidence: dict = field(default_factory=dict)   # review 档证据（查重候选等）

    def to_dict(self) -> dict:
        return asdict(self)


def summarize(findings: list["Finding"]) -> dict:
    return {
        "errors": sum(1 for f in findings if f.severity == SEVERITY_ERROR),
        "warnings": sum(1 for f in findings if f.severity == SEVERITY_WARNING),
        "reviews": sum(1 for f in findings if f.severity == SEVERITY_REVIEW),
    }


_SEVERITY_ORDER = {SEVERITY_ERROR: 0, SEVERITY_WARNING: 1, SEVERITY_REVIEW: 2}


def sort_findings(findings: list["Finding"]) -> list["Finding"]:
    return sorted(
        findings,
        key=lambda f: (_SEVERITY_ORDER.get(f.severity, 3), f.rule_id, f.location),
    )


def findings_to_dicts(findings: list["Finding"]) -> list[dict]:
    return [f.to_dict() for f in sort_findings(findings)]
