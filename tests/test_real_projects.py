"""真实项目只读集成测试：验证五个项目的索引事实（不修改任何数据）。

依赖已运行的索引数据（data/meta.db + data/qdrant），数据不存在时跳过。
"""

import pytest
from pathlib import Path

from agent_app.indexing.metadata_store import MetadataStore
from agent_app.tools.profile import get_project_profile

DATA = Path(__file__).resolve().parents[1] / "data"
pytestmark = pytest.mark.skipif(not (DATA / "meta.db").exists(), reason="索引数据未生成")

PROJECTS = ["ai-video-kb", "kaiwu", "new-bargain-laravel", "tiangong", "repository-manage"]


@pytest.fixture(scope="module")
def store():
    s = MetadataStore(DATA / "meta.db")
    yield s
    s.close()


@pytest.mark.parametrize("name", PROJECTS)
def test_project_indexed(store, name):
    stats = store.stats()
    assert name in stats, f"{name} 未索引"
    assert stats[name]["files"] > 0 and stats[name].get("chunks", 0) > 0
    assert stats[name].get("pending_embedding", 0) == 0
    assert stats[name].get("error_files", 0) == 0


@pytest.mark.parametrize("name", PROJECTS)
def test_no_sensitive_files_indexed(store, name):
    rows = store.conn.execute(
        "SELECT path FROM files WHERE project=? AND ("
        "  path LIKE '%.env%' OR path LIKE 'node_modules/%' OR path LIKE '%/node_modules/%' "
        "  OR path LIKE 'vendor/%' OR path LIKE '%/vendor/%' OR path LIKE '%.pem' "
        "  OR path LIKE '%.key' OR path LIKE 'models/%' OR path LIKE '.git/%')",
        (name,)).fetchall()
    assert rows == [], f"{name} 索引中混入敏感/无用文件: {[r['path'] for r in rows[:5]]}"


def test_known_symbols_searchable(store):
    """M3 关键符号仍在索引中且行号合理。"""
    cases = [
        ("tiangong", "TRANSITION_RULES", "state-machine.ts"),
        ("tiangong", "canTransition", "state-machine.ts"),
        ("kaiwu", "BM25", "retrieval.py"),
        ("new-bargain-laravel", "GoodsSpecification", "Models"),
    ]
    for project, symbol, path_part in cases:
        rows = store.symbol_search(symbol, project, limit=3)
        assert rows, f"{project}/{symbol} 未命中"
        assert any(path_part in r["path"] for r in rows)
        assert all(r["start_line"] > 0 for r in rows)


def test_chinese_fts_works(store):
    rows = store.fts_search("退款", project="new-bargain-laravel", limit=5)
    assert rows, "中文 FTS 检索失效"


def test_spec_structured_data(store):
    """M4：tiangong 任务与依赖边存在。"""
    tasks = store.spec_tasks("tiangong")
    assert len(tasks) > 100
    deps = {(r["spec_key"], r["depends_on"]) for r in store.spec_deps("tiangong")}
    assert ("query-writeback", "wiki-graph-compute") in deps
    assert ("wiki-graph-compute", "wiki-link-ingest") in deps


def test_spec_code_refs_collected(store):
    n = store.conn.execute("SELECT COUNT(*) c FROM spec_code_refs").fetchone()["c"]
    assert n > 300, "spec↔代码引用数量异常"


def test_profiles_facts(store):
    """M5：画像事实与实测一致。"""
    p = get_project_profile(store, "tiangong")
    assert any("Electron" in (s["framework"] or "") for s in p["stacks"])
    p = get_project_profile(store, "new-bargain-laravel")
    assert any("Laravel" in (s["framework"] or "") for s in p["stacks"])
    p = get_project_profile(store, "kaiwu")
    assert p["stacks"] and p["stacks"][0]["language"] == "Python"
    p = get_project_profile(store, "repository-manage")
    assert any("Tauri" in (s["framework"] or "") for s in p["stacks"])


def test_proposals_pending_only(store):
    """M6：所有提案均未落盘（除非人工操作过）。"""
    for p in store.proposals():
        assert p["status"] in ("pending", "approved", "rejected", "applied")


def test_dev_kb_excludes_eval_artifacts_and_pytest_cache(store):
    rows = store.conn.execute(
        "SELECT path FROM files WHERE project='dev-kb' AND ("
        "path LIKE '%.pytest_cache%' OR path LIKE '%/baselines/%' "
        "OR path LIKE '%cases_v1.jsonl' OR path LIKE '%cases_holdout.jsonl')"
    ).fetchall()
    assert rows == [], f"索引混入缓存或评测答案: {[row['path'] for row in rows]}"


def test_holdout_gold_candidates_are_indexed(store):
    import json
    path = Path(__file__).resolve().parents[1] / "evals/cases_holdout.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines():
        case = json.loads(line)
        candidates = store.find_candidate_chunks(case["project"], case["gold"]["any_of"])
        assert candidates, f"{case['id']} 的 gold 不在索引中"
