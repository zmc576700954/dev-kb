"""知识源接入契约：索引层规则（R-02 / R-05 / R-06）与服务层。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
import yaml

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
        "SELECT aliases FROM chunks WHERE project=? AND path='01-概览/02-术语.md'",
        (doc_source.name,)).fetchone()
    assert row is not None
    assert json.loads(row["aliases"]) == ["ComfyUI"]


def test_aliases_column_records_only_surfaces_in_body(store, doc_source):
    source_root = Path(doc_source.root)
    (source_root / "doc-kb.terms.yaml").write_text(yaml.safe_dump({
        "aliases": {"LoRA": ["lora", "低秩适配"]},
    }, allow_unicode=True), encoding="utf-8")
    (source_root / "01-概览" / "02-术语.md").write_text(
        "# 术语表\n\n## 定义\n\n低秩适配是一种轻量微调方法。\n", encoding="utf-8"
    )
    SyncEngine(store, None, embed=False).sync_project(
        doc_source.name, doc_source.root, decl=doc_source
    )
    row = store.conn.execute(
        "SELECT aliases, content FROM chunks WHERE project=? AND path='01-概览/02-术语.md'",
        (doc_source.name,),
    ).fetchone()
    assert json.loads(row["aliases"]) == ["低秩适配"]


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


@pytest.mark.parametrize("with_index", [False, True])
def test_r03_rejects_targets_outside_indexable_content(tmp_path, with_index):
    root = tmp_path / "kb"
    (root / "docs").mkdir(parents=True)
    (root / "outside").mkdir()
    (root / "excluded").mkdir()
    (root / "docs" / "keep.md").write_text("# 有效目标\n")
    (root / "outside" / "hidden.md").write_text("# 边界外\n")
    (root / "excluded" / "hidden.md").write_text("# 已排除\n")
    config = tmp_path / "projects.yaml"
    config.write_text(yaml.safe_dump({"projects": [{
        "name": "kb", "path": str(root), "provides": ["category"],
        "content_roots": ["docs"], "entry_docs": ["docs/keep.md"],
        "extra_exclude_dirs": ["excluded"],
        "intent_map": {"有效": ["keep"], "大小写": ["KEEP"], "越界": ["outside"], "排除": ["excluded"]},
    }]}, allow_unicode=True), encoding="utf-8")
    db = tmp_path / "meta.db"
    if with_index:
        store = MetadataStore(db)
        for rel in ("docs/keep.md", "outside/hidden.md", "excluded/hidden.md"):
            store.replace_chunks("kb", rel, [{
                "source_type": "document", "heading_path": ["目标"], "content": rel,
            }])
        store.close()

    svc = ContractService(db_path=db, config=config)
    result = svc.validate("kb")
    svc.close()
    r03 = [f for f in result["findings"] if f["rule_id"] == "R-03"]
    assert {f["evidence"]["pattern"] for f in r03} == {"outside", "excluded"}


def test_r04_does_not_require_category_links(tmp_path, registry, doc_source):
    (tmp_path / "doc-kb" / "README.md").write_text("# 只有入口，没有分类链接\n")
    svc = ContractService(db_path=tmp_path / "meta.db", config=registry)
    result = svc.validate(doc_source.name)
    svc.close()
    assert result["findings"] == []


def test_validate_does_not_migrate_existing_index(tmp_path, registry, doc_source):
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE chunks ("
        "id INTEGER PRIMARY KEY, project TEXT, path TEXT, source_type TEXT, "
        "heading_path TEXT, content TEXT)"
    )
    conn.commit()
    conn.close()

    svc = ContractService(db_path=db, config=registry)
    svc.validate(doc_source.name)
    svc.close()

    conn = sqlite3.connect(db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    columns = {r[1] for r in conn.execute("PRAGMA table_info(chunks)")}
    conn.close()
    assert "files" not in tables
    assert "category" not in columns


def test_indexed_source_checks_are_inapplicable_without_index(tmp_path, registry, doc_source):
    """无索引时 R-02/R-05 不适用；声明级 R-01/R-03/R-04/R-07 仍可校验。"""
    db = tmp_path / "no-index.db"
    svc = ContractService(db_path=db, config=registry)
    result = svc.validate(doc_source.name)
    assert result["verdict"] in ("pass", "warn")
    assert all(f["rule_id"] not in ("R-02", "R-05", "R-06") for f in result["findings"])
    assert not db.exists()
    svc.close()
