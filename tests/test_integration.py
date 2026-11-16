"""集成测试：增量同步与混合检索。

全部使用临时目录与独立 SQLite，不触碰真实项目数据。
"""

import os
import textwrap
from pathlib import Path

import pytest

from agent_app.contract.declaration import SourceDeclaration
from agent_app.indexing.metadata_store import MetadataStore
from agent_app.ingestion.sync import SyncEngine
from agent_app.tools.retrieval import HybridSearch


@pytest.fixture()
def store(tmp_path):
    s = MetadataStore(tmp_path / "test.db")
    yield s
    s.close()


@pytest.fixture()
def proj(tmp_path):
    """夹具项目：PHP 代码 + Markdown 文档。"""
    root = tmp_path / "proj"
    (root / "app").mkdir(parents=True)
    (root / "docs").mkdir(parents=True)
    (root / "app" / "Pay.php").write_text(
        "<?php\nclass Pay {\n    public function refund() { return 'old'; }\n}\n")
    (root / "README.md").write_text("# 夹具项目\n\n支付模块说明。\n")
    (root / "docs" / "pay.md").write_text(textwrap.dedent("""\
        # 支付流程

        ## 退款

        退款走 refund 方法。
        """))
    return str(root)


# ---- 增量同步 ----

def test_sync_new_change_delete(store, proj):
    engine = SyncEngine(store, None, embed=False)

    r1 = engine.sync_project("t", proj, decl=SourceDeclaration.from_entry("t", {
        "path": proj, "content_roots": ["."],
    }))
    assert r1["new"] == 3 and r1["errors"] == 0
    assert store.fts_search("refund", project="t")

    # 修改：旧内容失效，新内容可检索
    (open(os.path.join(proj, "app", "Pay.php"), "w")
     .write("<?php\nclass Pay {\n    public function refund() { return 'new-logic'; }\n}\n"))
    r2 = engine.sync_project("t", proj, decl=SourceDeclaration.from_entry("t", {
        "path": proj, "content_roots": ["."],
    }))
    assert r2["changed"] == 1 and r2["unchanged"] == 2
    hits = store.fts_search("new-logic", project="t")
    assert hits and any(h["start_line"] == 3 for h in hits)  # 方法块行号准确
    assert not store.fts_search("returnold", project="t")    # 旧块已整体失效

    # 删除：全清理
    os.remove(os.path.join(proj, "README.md"))
    r3 = engine.sync_project("t", proj, decl=SourceDeclaration.from_entry("t", {
        "path": proj, "content_roots": ["."],
    }))
    assert r3["deleted"] == 1
    assert not store.fts_search("夹具项目", project="t")


def test_sync_idempotent(store, proj):
    engine = SyncEngine(store, None, embed=False)
    engine.sync_project("t", proj, decl=SourceDeclaration.from_entry("t", {
        "path": proj, "content_roots": ["."],
    }))
    r = engine.sync_project("t", proj, decl=SourceDeclaration.from_entry("t", {
        "path": proj, "content_roots": ["."],
    }))
    assert r["new"] == 0 and r["changed"] == 0 and r["deleted"] == 0


def test_sync_records_category_and_heading_path(store, proj):
    """契约字段：category 取路径首段，heading_path 保留标题链。"""
    engine = SyncEngine(store, None, embed=False)
    engine.sync_project("t", proj, decl=SourceDeclaration.from_entry("t", {
        "path": proj, "content_roots": ["."],
    }))
    rows = store.conn.execute(
        "SELECT path, category, heading_path FROM chunks WHERE project='t' ORDER BY path"
    ).fetchall()
    by_path = {r["path"]: r for r in rows}
    assert by_path["README.md"]["category"] == ""
    assert by_path["docs/pay.md"]["category"] == "docs"
    # 标题链进入检索可见字段
    assert by_path["docs/pay.md"]["heading_path"]


def test_sync_preserves_dotfile_category(store, proj):
    plans = Path(proj, ".superpowers", "plans")
    plans.mkdir(parents=True)
    (plans / "design.md").write_text("# 设计\n\n保留隐藏目录分类。\n")
    SyncEngine(store, None, embed=False).sync_project("t", proj, decl=SourceDeclaration.from_entry("t", {
        "path": proj, "content_roots": ["."],
    }))
    row = store.conn.execute(
        "SELECT category FROM chunks WHERE project='t' AND path='.superpowers/plans/design.md'"
    ).fetchone()
    assert row["category"] == ".superpowers"


# ---- 混合检索 ----

def test_hybrid_symbol_and_chinese(store, proj):
    engine = SyncEngine(store, None, embed=False)
    engine.sync_project("t", proj, decl=SourceDeclaration.from_entry("t", {
        "path": proj, "content_roots": ["."],
    }))
    hs = HybridSearch(store, None)  # 无向量通道也应工作

    by_symbol = hs.search("Pay refund 在哪", project="t")
    assert by_symbol
    top_symbols = {r["symbol"] for r in by_symbol[:3]}
    assert "Pay@refund" in top_symbols  # 容器块 Pay 与先祖块同现为合理行为
    method = next(r for r in by_symbol if r["symbol"] == "Pay@refund")
    assert method["start_line"] == 3

    by_cn = hs.search("支付模块", project="t")
    assert by_cn and any("README.md" in r["path"] for r in by_cn[:3])


def test_hybrid_no_evidence_returns_empty(store, proj):
    SyncEngine(store, None, embed=False).sync_project("t", proj, decl=SourceDeclaration.from_entry("t", {
        "path": proj, "content_roots": ["."],
    }))
    assert HybridSearch(store, None).search("zzqqxy 不存在的东西", project="t") == []


def test_hybrid_diagnostics_expose_channel_and_fusion_ranks(store, proj):
    SyncEngine(store, None, embed=False).sync_project("t", proj, decl=SourceDeclaration.from_entry("t", {
        "path": proj, "content_roots": ["."],
    }))
    diagnostics = {}
    results = HybridSearch(store, None).search(
        "Pay refund 在哪", project="t", diagnostics=diagnostics
    )
    assert results and diagnostics["code_seeking"] is True
    assert diagnostics["candidate_ids"] and diagnostics["fusion_ids"]
    assert "symbol" in diagnostics["channel_ranks"]
    gold = store.find_candidate_chunks("t", ["Pay@refund"])
    assert gold and gold[0]["chunk_id"] in diagnostics["candidate_sources"]


def test_cross_project_intent_hints_stay_in_their_source(store, tmp_path):
    for project in ("alpha", "beta"):
        store.replace_chunks(project, "docs/deploy.md", [{
            "source_type": "document", "heading_path": ["部署"],
            "content": "部署说明",
        }])
    declarations = {
        "alpha": SourceDeclaration.from_entry("alpha", {
            "path": str(tmp_path), "content_roots": ["."],
            "intent_map": {"部署": ["deploy"]},
        }),
        "beta": SourceDeclaration.from_entry("beta", {
            "path": str(tmp_path), "content_roots": ["."],
        }),
    }
    results = HybridSearch(store, None, declarations=declarations).search("部署")
    by_project = {r["project"]: r for r in results}
    assert "intent-hint" in by_project["alpha"]["matched_by"]
    assert "intent-hint" not in by_project["beta"]["matched_by"]


def test_cross_project_heading_fallback_merges_rows_without_bm25(store, tmp_path, monkeypatch):
    for project, heading in (("alpha", "Alpha"), ("beta", "Beta")):
        store.replace_chunks(project, "docs/topic.md", [{
            "source_type": "document", "heading_path": [heading], "content": heading,
        }])
    declarations = {
        project: SourceDeclaration.from_entry(project, {
            "path": str(tmp_path), "provides": ["heading_path"], "content_roots": ["."],
        })
        for project in ("alpha", "beta")
    }
    monkeypatch.setattr("agent_app.tools.retrieval.HEADING_FALLBACK", True)
    results = HybridSearch(store, None, declarations=declarations).search("Alpha Beta")
    assert {r["project"] for r in results} == {"alpha", "beta"}
    assert all("heading" in r["matched_by"] for r in results)


def test_heading_channel_matches_title(store, proj):
    """声明且覆盖率达标的标题链进入独立召回通道。"""
    decl = SourceDeclaration.from_entry("t", {
        "path": proj, "provides": ["heading_path"], "content_roots": ["."],
    })
    SyncEngine(store, None, embed=False).sync_project("t", proj, decl=decl)
    hs = HybridSearch(store, None, declarations={"t": decl})
    results = hs.search("退款", project="t")
    assert results
    assert any("docs/pay.md" in r["path"] for r in results)
    assert any("heading" in (r.get("matched_by") or "") for r in results)


def test_heading_channel_degrades_below_coverage_threshold(store, proj):
    Path(proj, "docs", "plain.md").write_text("没有标题的正文。\n")
    decl = SourceDeclaration.from_entry("t", {
        "path": proj, "provides": ["heading_path"], "content_roots": ["."],
    })
    SyncEngine(store, None, embed=False).sync_project("t", proj, decl=decl)
    results = HybridSearch(store, None, declarations={"t": decl}).search("退款", project="t")
    assert results
    assert all("heading" not in r["matched_by"] for r in results)


def test_expansion_full_substring_only_for_compound_terms(store, proj, monkeypatch):
    from agent_app.llm import query_expansion
    from pathlib import Path
    Path(proj, "report.py").write_text(
        "class MapMigrationStore:\n    pass\n\n"
        "def print_report_json():\n    return {}\n",
        encoding="utf-8",
    )
    SyncEngine(store, None, embed=False).sync_project("t", proj, decl=SourceDeclaration.from_entry("t", {
        "path": proj, "content_roots": ["."],
    }))
    expansion_calls = []
    def fake_expand(query, use_llm=True):
        expansion_calls.append(use_llm)
        return ["report_json"] if "report" in query else ["migrations"]
    monkeypatch.setattr(query_expansion, "expand", fake_expand)
    engine = HybridSearch(store, None, expand=True)

    compound = engine.search("report_json 支持哪些输出格式", project="t")
    report = next(r for r in compound if r["symbol"] == "print_report_json")
    assert "expand-full-sub" in report["matched_by"]

    single = engine.search("migrations 在哪定义", project="t")
    migration = next(r for r in single if r["symbol"] == "MapMigrationStore")
    assert "expand-full-sub" not in migration["matched_by"]

    cached_only = HybridSearch(store, None, expand=True, expand_use_llm=False)
    cached_only.search("migrations 在哪定义", project="t")
    assert expansion_calls == [True, True, False]
