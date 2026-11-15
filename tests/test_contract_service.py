"""知识源接入契约：索引层规则（R-02 / R-05 / R-06）与服务层。"""

from __future__ import annotations

import pytest

from agent_app.contract.checks import check_r02, check_r05, check_r06
from agent_app.contract.service import ContractService
from agent_app.indexing.metadata_store import MetadataStore
from agent_app.ingestion.sync import SyncEngine


@pytest.fixture()
def store(tmp_path):
    s = MetadataStore(tmp_path / "test.db")
    yield s
    s.close()


@pytest.fixture()
def synced(store, doc_source):
    engine = SyncEngine(store, None, embed=False)
    engine.sync_project(doc_source.name, doc_source.root, decl=doc_source)
    return store


def test_preamble_chunk_inherits_first_heading(synced, doc_source):
    """frontmatter 前导块归属文档首个标题——否则它是检索里的死块。"""
    rows = synced.conn.execute(
        "SELECT path, heading_path FROM chunks WHERE project=? AND start_line=1 AND path LIKE '%背景%'",
        (doc_source.name,)).fetchall()
    assert rows
    assert "项目背景" in rows[0]["heading_path"]


def test_contract_columns_are_populated(synced, doc_source):
    row = synced.conn.execute(
        "SELECT category, doc_role, aliases FROM chunks WHERE project=? AND path LIKE '%02-技术%'",
        (doc_source.name,)).fetchone()
    assert row["category"] == "02-技术"
    assert row["doc_role"] == ""      # roles 只覆盖 README 与 01-概览
    row2 = synced.conn.execute(
        "SELECT doc_role FROM chunks WHERE project=? AND path LIKE '%01-概览%'",
        (doc_source.name,)).fetchone()
    assert row2["doc_role"] == "guide"


def test_aliases_column_records_terms_found_in_body(synced, doc_source):
    row = synced.conn.execute(
        "SELECT aliases FROM chunks WHERE project=? AND content LIKE '%ComfyUI%'",
        (doc_source.name,)).fetchone()
    assert row is not None
    assert "ComfyUI" in row["aliases"] and "comfy" in row["aliases"]


def test_r02_passes_when_headings_are_dense(synced, doc_source):
    assert check_r02(doc_source, synced) == []


def test_r05_reports_chunks_without_heading(synced, doc_source):
    findings = check_r05(doc_source, synced)
    assert findings == []  # 夹具全部有标题


def test_r06_flags_alias_without_occurrence(synced, doc_source):
    doc_source.terms()["不存在的术语"] = ["nope"]
    doc_source._cache["aliases"]["不存在的术语"] = ["nope"]
    findings = check_r06(doc_source, synced)
    assert findings and "不存在的术语" in findings[0].message


# ---- 服务层 ----

def test_service_validate_reports_findings(tmp_path, registry, doc_source, code_source):
    svc = ContractService(db_path=tmp_path / "meta.db", config=registry)
    result = svc.validate(doc_source.name)
    assert result["source"] == doc_source.name
    assert result["verdict"] in ("pass", "warn", "fail")
    assert set(result["summary"]) == {"errors", "warnings", "reviews"}
    svc.close()


def test_service_validate_unknown_source(tmp_path, registry):
    svc = ContractService(db_path=tmp_path / "meta.db", config=registry)
    result = svc.validate("不存在的源")
    assert result["verdict"] == "unknown"
    svc.close()


def test_service_lists_rules_and_sources(tmp_path, registry):
    svc = ContractService(db_path=tmp_path / "meta.db", config=registry)
    assert len(svc.rules()) == 7
    names = {s["name"] for s in svc.sources()}
    assert names == {"doc-kb", "code-lib"}
    svc.close()


def test_service_suggest_hints_lists_directories(tmp_path, registry):
    svc = ContractService(db_path=tmp_path / "meta.db", config=registry)
    info = svc.suggest_hints("doc-kb")
    assert "03-运维" in info["directories"]
    svc.close()


def test_readonly_queries_do_not_create_index(tmp_path, registry):
    """CI 没有 meta.db：sources/rules/suggest-hints 不应顺手建库。"""
    db = tmp_path / "no-index.db"
    svc = ContractService(db_path=db, config=registry)
    assert svc.sources()
    assert len(svc.rules()) == 7
    assert svc.suggest_hints("doc-kb")["directories"]
    assert not db.exists()
    svc.close()
    assert not db.exists()


def test_indexed_source_checks_are_inapplicable_without_index(tmp_path, registry, doc_source):
    """无索引时 R-02/R-05 不适用；声明级 R-01/R-03/R-04/R-07 仍可校验。"""
    db = tmp_path / "no-index.db"
    svc = ContractService(db_path=db, config=registry)
    result = svc.validate(doc_source.name)
    assert result["verdict"] in ("pass", "warn")
    assert all(f["rule_id"] not in ("R-02", "R-05", "R-06") for f in result["findings"])
    assert not db.exists()
    svc.close()
