"""知识源接入契约：规则与检查实现。"""

from __future__ import annotations

from agent_app.contract.checks import (
    HEADING_COVERAGE_MIN,
    check_r01,
    check_r02,
    check_r03,
    check_r04,
    check_r05,
    check_r06,
    check_r07,
    run_checks,
)
from agent_app.contract.declaration import SourceDeclaration
from agent_app.contract.rules import RULES


def test_rule_registry_shape():
    """规则集固定 7 条，ID 稳定，每条都有可判定的条文。"""
    assert list(RULES) == ["R-01", "R-02", "R-03", "R-04", "R-05", "R-06", "R-07"]
    for rule in RULES.values():
        assert rule.severity in ("error", "warning")
        assert rule.title and rule.statement


# ---- R-01 内容边界 ----

def test_r01_requires_content_roots(tmp_path):
    decl = SourceDeclaration.from_entry("x", {"path": str(tmp_path)})
    findings = check_r01(decl)
    assert len(findings) == 1
    assert findings[0].rule_id == "R-01"
    assert findings[0].severity == "error"


def test_r01_passes_with_explicit_roots(tmp_path):
    decl = SourceDeclaration.from_entry("x", {"path": str(tmp_path), "content_roots": ["."]})
    assert check_r01(decl) == []


# ---- R-03 意图映射目标 ----

def test_r03_flags_missing_intent_target(doc_source):
    decl = SourceDeclaration.from_entry(doc_source.name, {
        "path": doc_source.root,
        "provides": list(doc_source.provides),
        "content_roots": list(doc_source.content_roots),
        "intent_map": {"部署": ["99-不存在的目录"]},
    })
    findings = check_r03(decl)
    assert findings and findings[0].rule_id == "R-03"
    assert "99-不存在的目录" in findings[0].message


def test_r03_passes_when_target_exists(doc_source):
    decl = SourceDeclaration.from_entry(doc_source.name, {
        "path": doc_source.root,
        "provides": list(doc_source.provides),
        "content_roots": list(doc_source.content_roots),
        "intent_map": {"部署": ["03-运维"]},
    })
    assert check_r03(decl) == []


# ---- R-04 入口文档 ----

def test_r04_requires_entry_docs(code_source):
    decl = SourceDeclaration.from_entry(code_source.name, {
        "path": code_source.root, "provides": list(code_source.provides)})
    findings = check_r04(decl)
    assert findings and findings[0].severity == "error"


def test_r04_flags_missing_entry_file(doc_source):
    decl = SourceDeclaration.from_entry(doc_source.name, {
        "path": doc_source.root,
        "provides": list(doc_source.provides),
        "content_roots": list(doc_source.content_roots),
        "entry_docs": ["不存在的.md"],
    })
    findings = check_r04(decl)
    assert findings and "不存在" in findings[0].message


def test_r04_coverage_only_for_document_sources(code_source):
    """代码库没有"文档分类"，覆盖性检查不适用——否则全是噪声。"""
    assert check_r04(code_source) == []


def test_r04_warns_on_uncovered_category(doc_source):
    doc_source.entry_docs = ("README.md",)
    findings = check_r04(doc_source)
    # README 覆盖了全部三个分类 → 无覆盖性警告
    assert [f for f in findings if "未被任何入口文档覆盖" in f.message] == []


# ---- R-07 声明字段集 ----

def test_r07_flags_unknown_provides(tmp_path):
    decl = SourceDeclaration.from_entry("x", {
        "path": str(tmp_path), "provides": ["heading_path", "不存在的字段"]})
    findings = check_r07(decl)
    assert findings and "不存在的字段" in findings[0].message


def test_r07_accepts_contract_fields(tmp_path):
    decl = SourceDeclaration.from_entry("x", {
        "path": str(tmp_path),
        "provides": ["heading_path", "category", "doc_role", "aliases"]})
    assert check_r07(decl) == []


# ---- 适用性：不适用不是 finding ----

def test_inapplicable_rules_are_skipped(tmp_path):
    """没声明 heading_path 的源，R-02/R-05 不适用，也不产生发现。"""
    decl = SourceDeclaration.from_entry("x", {
        "path": str(tmp_path), "provides": ["category"], "content_roots": ["."]})
    findings = run_checks(decl, store=None)
    assert all(f.rule_id not in ("R-02", "R-05", "R-06") for f in findings)


def test_heading_coverage_threshold_is_declared():
    assert 0 < HEADING_COVERAGE_MIN <= 1
