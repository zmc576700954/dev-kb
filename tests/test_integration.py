"""集成测试：增量同步、混合检索、Spec 解析与依赖图、提案审批闭环。

全部使用临时目录与独立 SQLite，不触碰真实项目数据。
"""

import os
import subprocess
import textwrap

import pytest

from agent_app.indexing.metadata_store import MetadataStore
from agent_app.ingestion.sync import SyncEngine
from agent_app.proposals.apply_update import apply_proposal
from agent_app.proposals.impact_analysis import analyze_impact, draft_proposal
from agent_app.specs.parser import parse_spec, canonical_key
from agent_app.specs.task_graph import TaskGraph
from agent_app.tools.retrieval import HybridSearch


@pytest.fixture()
def store(tmp_path):
    s = MetadataStore(tmp_path / "test.db")
    yield s
    s.close()


@pytest.fixture()
def proj(tmp_path):
    """夹具项目：PHP 代码 + Markdown 文档 + Spec 任务文件。"""
    root = tmp_path / "proj"
    (root / "app").mkdir(parents=True)
    (root / "docs" / "tasks").mkdir(parents=True)
    (root / "app" / "Pay.php").write_text(
        "<?php\nclass Pay {\n    public function refund() { return 'old'; }\n}\n")
    (root / "README.md").write_text("# 夹具项目\n\n支付模块说明。\n")
    (root / "docs" / "tasks" / "2026-11-01-pay-v2.md").write_text(textwrap.dedent("""\
        # 支付 v2 · 任务跟踪

        > 创建：2026-11-01
        > 前置任务：[2026-10-01-pay-v1.md](2026-10-01-pay-v1.md)

        ## 任务清单

        - [x] **P1 退款接口**：完成
        - [ ] **P2 余额支付**：进行中
        - [ ] **P3 对账**：未开始
        """))
    (root / "docs" / "tasks" / "2026-10-01-pay-v1.md").write_text(textwrap.dedent("""\
        # 支付 v1 · 任务跟踪

        ## 任务清单

        - [x] **P1 下单**：完成
        """))
    return str(root)


# ---- 增量同步 ----

def test_sync_new_change_delete(store, proj):
    engine = SyncEngine(store, None, embed=False)

    r1 = engine.sync_project("t", proj)
    assert r1["new"] == 4 and r1["errors"] == 0
    assert store.fts_search("refund", project="t")

    # 修改：旧内容失效，新内容可检索
    (open(os.path.join(proj, "app", "Pay.php"), "w")
     .write("<?php\nclass Pay {\n    public function refund() { return 'new-logic'; }\n}\n"))
    r2 = engine.sync_project("t", proj)
    assert r2["changed"] == 1 and r2["unchanged"] == 3
    hits = store.fts_search("new-logic", project="t")
    assert hits and any(h["start_line"] == 3 for h in hits)  # 方法块行号准确
    assert not store.fts_search("returnold", project="t")    # 旧块已整体失效

    # 删除：全清理
    os.remove(os.path.join(proj, "README.md"))
    r3 = engine.sync_project("t", proj)
    assert r3["deleted"] == 1
    assert not store.fts_search("夹具项目", project="t")


def test_sync_idempotent(store, proj):
    engine = SyncEngine(store, None, embed=False)
    engine.sync_project("t", proj)
    r = engine.sync_project("t", proj)
    assert r["new"] == 0 and r["changed"] == 0 and r["deleted"] == 0


# ---- 混合检索 ----

def test_hybrid_symbol_and_chinese(store, proj):
    engine = SyncEngine(store, None, embed=False)
    engine.sync_project("t", proj)
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
    SyncEngine(store, None, embed=False).sync_project("t", proj)
    assert HybridSearch(store, None).search("zzqqxy 不存在的东西", project="t") == []


def test_hybrid_diagnostics_expose_channel_and_fusion_ranks(store, proj):
    SyncEngine(store, None, embed=False).sync_project("t", proj)
    diagnostics = {}
    results = HybridSearch(store, None).search(
        "Pay refund 在哪", project="t", diagnostics=diagnostics
    )
    assert results and diagnostics["code_seeking"] is True
    assert diagnostics["candidate_ids"] and diagnostics["fusion_ids"]
    assert "symbol" in diagnostics["channel_ranks"]
    gold = store.find_candidate_chunks("t", ["Pay@refund"])
    assert gold and gold[0]["chunk_id"] in diagnostics["candidate_sources"]


def test_expansion_full_substring_only_for_compound_terms(store, proj, monkeypatch):
    from agent_app.llm import query_expansion
    from pathlib import Path
    Path(proj, "report.py").write_text(
        "class MapMigrationStore:\n    pass\n\n"
        "def print_report_json():\n    return {}\n",
        encoding="utf-8",
    )
    SyncEngine(store, None, embed=False).sync_project("t", proj)
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


# ---- Spec 解析与依赖图 ----

def test_spec_parser_tasks_and_deps(store, proj):
    SyncEngine(store, None, embed=False).sync_project("t", proj)
    tasks = store.spec_tasks("t", "pay-v2")
    assert len(tasks) == 3
    assert tasks[0]["task_id"] == "P1" and tasks[0]["done"] == 1
    assert tasks[1]["done"] == 0
    deps = {(r["spec_key"], r["depends_on"]) for r in store.spec_deps("t")}
    assert ("pay-v2", "pay-v1") in deps


def test_canonical_key():
    assert canonical_key("2026-10-01-d10b-yjs-awareness") == "d10b-yjs-awareness"
    assert canonical_key("2026-07-23-wiki-link-ingest-design") == "wiki-link-ingest"
    assert canonical_key("../../tasks/2026-07-23-wiki-link-ingest") == "wiki-link-ingest"
    assert canonical_key("2026-11-05-luck-admin-integration-spec") == "luck-admin-integration"


def test_task_graph_parallel_and_blocking(store, proj):
    SyncEngine(store, None, embed=False).sync_project("t", proj)
    g = TaskGraph(store, "t")
    p1, p2 = g.progress("pay-v1"), g.progress("pay-v2")
    assert p1["state"] == "done"
    assert p2["state"] == "in_progress" and p2["blocked_by"] == []  # 前置已完成
    parallel = g.parallel_groups()
    assert any(x["key"] == "pay-v2" for x in parallel)
    # 可复算：两次计算一致
    assert [x["key"] for x in g.parallel_groups()] == [x["key"] for x in TaskGraph(store, "t").parallel_groups()]


def test_task_graph_blocked_when_dep_unfinished(store, proj):
    # 把 pay-v1 的任务改成未完成，pay-v2 应被阻塞
    (open(os.path.join(proj, "docs", "tasks", "2026-10-01-pay-v1.md"), "w")
     .write("# 支付 v1 · 任务跟踪\n\n## 任务清单\n\n- [ ] **P1 下单**：未完成\n"))
    SyncEngine(store, None, embed=False).sync_project("t", proj)
    g = TaskGraph(store, "t")
    p2 = g.progress("pay-v2")
    assert p2["blocked_by"] == ["pay-v1"] and not p2["executable"]
    assert all(x["key"] != "pay-v2" for x in g.parallel_groups())


def test_spec_status_line_normalized():
    spec = parse_spec("# 某设计\n\n> 状态：待评审\n", "docs/superpowers/specs/2026-01-01-x-design.md")
    assert spec.status == "pending_review" and spec.kind == "design"


# ---- 提案审批闭环 ----

def test_proposal_gate_and_apply(store, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.ts").write_text("export const x = 1\n")
    doc = root / "note.md"
    doc.write_text("# 笔记\n\n引用 `a.ts:1`。\n")
    git = lambda *a: subprocess.run(["git", "-C", str(root), *a], capture_output=True)
    git("init", "-q"); git("config", "user.email", "t@t"); git("config", "user.name", "t")
    git("add", "."); git("commit", "-qm", "init")

    SyncEngine(store, None, embed=False).sync_project("t", str(root))
    (root / "a.ts").write_text("export const x = 2\n")
    git("add", "."); git("commit", "-qm", "change")

    impacts = analyze_impact(store, "t", str(root), ref="HEAD~1")
    assert impacts and impacts[0]["doc_path"] == "note.md"

    pid = draft_proposal(store, "t", "note.md", impacts[0]["evidence"])
    # 未审批不能写
    assert "error" in apply_proposal(store, str(root), pid)
    assert "变更影响记录" not in doc.read_text()
    # 驳回后也不能写
    store.decide_proposal(pid, "rejected")
    assert "error" in apply_proposal(store, str(root), pid)
    # 审批后可写，留 diff，不重复应用
    store.decide_proposal(pid, "approved")
    r = apply_proposal(store, str(root), pid)
    assert "applied" in r
    assert "变更影响记录" in doc.read_text()
    assert "a.ts" in store.get_proposal(pid)["diff"]
    assert "error" in apply_proposal(store, str(root), pid)


# ---- LLM 文档草案（M6 增强）----

def test_llm_draft_helpers(store, tmp_path):
    import subprocess as sp
    from agent_app.proposals.llm_draft import collect_diffs, doc_context, llm_draft

    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.ts").write_text("export const x = 1\n")
    doc = root / "note.md"
    doc.write_text("\n".join(f"第{i}行" for i in range(1, 60)) + "\n")
    git = lambda *a: sp.run(["git", "-C", str(root), *a], capture_output=True)
    git("init", "-q"); git("config", "user.email", "t@t"); git("config", "user.name", "t")
    git("add", "."); git("commit", "-qm", "init")
    (root / "a.ts").write_text("export const x = 2  # changed\n")
    git("add", "."); git("commit", "-qm", "change")

    # diff 收集：能拿到变更内容且受预算截断
    diffs = collect_diffs(str(root), ["a.ts"])
    assert "a.ts" in diffs and "x = 2" in diffs["a.ts"]
    assert len(diffs["a.ts"]) <= 2500

    # 文档上下文：有行号取窗口，无行号取开头
    ctx = doc_context(str(root), "note.md", [30])
    assert "第30行" in ctx and "第1行" not in ctx
    ctx2 = doc_context(str(root), "note.md", [])
    assert "第1行" in ctx2

    # LLM 不可用时回退空串（调用方回退模板草案）
    import agent_app.llm.backend as backend
    orig = backend.available
    backend.available = lambda: False
    try:
        assert llm_draft(str(root), "note.md", [{"changed_file": "a.ts", "how": "x"}]) == ""
    finally:
        backend.available = orig


def test_update_draft_only_pending(store, tmp_path):
    from agent_app.proposals.impact_analysis import draft_proposal
    evidence = [{"type": "content_mention", "changed_file": "a.ts", "how": "commit", "commit": "abc"}]
    pid = draft_proposal(store, "t", "note.md", evidence)
    store.update_draft(pid, "\n\n## 变更同步（测试）\n")
    assert "变更同步" in store.get_proposal(pid)["draft"]
    # 已批准的提案不可再改草案
    store.decide_proposal(pid, "approved")
    store.update_draft(pid, "不应生效")
    assert "不应生效" not in store.get_proposal(pid)["draft"]
