"""评测框架测试：不加载模型，验证题集、元数据、对比门禁与失败分类。"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_app.contract.declaration import SourceDeclaration

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evals"))

from compare import (  # noqa: E402
    compatibility_warnings,
    config_differences,
    mcnemar,
    validated_case_ids,
)
from eval_metadata import cases_metadata, implementation_metadata, retrieval_metadata  # noqa: E402
from run_eval import failure_reason, hit_rank  # noqa: E402

CASES = ROOT / "evals/cases_v1.jsonl"


def assert_cases_wellformed(path: Path, minimum: int = 1):
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(cases) >= minimum
    ids = set()
    for case in cases:
        assert case["id"] not in ids, f"重复 id: {case['id']}"
        ids.add(case["id"])
        assert case["difficulty"] in ("literal", "semantic", "doc", "cross")
        assert case["question"] and case["gold"]["any_of"]
        assert "project" in case
    return cases


def test_cases_file_wellformed():
    assert_cases_wellformed(CASES, minimum=40)


def test_holdout_cases_wellformed():
    assert_cases_wellformed(ROOT / "evals/cases_holdout.jsonl", minimum=10)


def test_hit_rank_logic():
    results = [
        {"path": "a/b/Foo.php", "symbol": "Foo"},
        {"path": "c/Bar.ts", "symbol": None},
    ]
    assert hit_rank(results, ["Foo"]) == 1
    assert hit_rank(results, ["Bar"]) == 2
    assert hit_rank(results, ["不存在"]) is None
    assert hit_rank([], ["x"]) is None


def test_mcnemar_exact():
    assert mcnemar([True], [True])["p_exact"] == 1.0
    result = mcnemar([False, True], [True, True])
    assert result["improved"] == 1 and result["regressed"] == 0
    result = mcnemar([False] * 9, [True] * 9)
    assert result["p_exact"] < 0.05


def test_case_comparison_requires_identical_ids():
    base = {"rows": [{"id": "a"}, {"id": "b"}]}
    new = {"rows": [{"id": "a"}]}
    with pytest.raises(ValueError, match="题集 ID 不一致"):
        validated_case_ids(base, new)


def test_case_comparison_rejects_changed_content():
    base = {"rows": [{"id": "a"}], "cases": {"sha256": "old"}}
    new = {"rows": [{"id": "a"}], "cases": {"sha256": "new"}}
    with pytest.raises(ValueError, match="SHA-256"):
        validated_case_ids(base, new)


def test_comparison_metadata_warnings_and_config_diff():
    warnings = compatibility_warnings({"rows": []}, {"rows": []})
    assert len(warnings) == 2
    differences = config_differences(
        {"config": {"expand": False, "blend": 0.3}},
        {"config": {"expand": True, "blend": 0.3}},
    )
    assert differences == ["expand: False → True"]


def test_cases_and_implementation_fingerprints(tmp_path):
    path = tmp_path / "cases.jsonl"
    path.write_text('{"id":"x"}\n', encoding="utf-8")
    metadata = cases_metadata(path, [{"id": "x"}])
    assert metadata["count"] == 1 and len(metadata["sha256"]) == 64
    assert len(metadata["ids_sha256"]) == 64
    assert len(implementation_metadata()["sources_sha256"]) == 64


def test_implementation_fingerprint_includes_declarations_and_tuning(tmp_path):
    def declaration(fragment):
        return SourceDeclaration.from_entry("x", {
            "path": str(tmp_path), "content_roots": ["."],
            "intent_map": {"部署": [fragment]},
        })

    first = implementation_metadata({"x": declaration("docs")})
    assert first["sources_sha256"] == implementation_metadata({"x": declaration("docs")})["sources_sha256"]
    assert first["sources_sha256"] != implementation_metadata({"x": declaration("ops")})["sources_sha256"]

    tuning = tmp_path / "tuning"
    tuning.mkdir()
    (tuning / "x.yaml").write_text("aliases:\n  LoRA: [低秩适配]\n", encoding="utf-8")
    with_aliases = implementation_metadata({"x": declaration("docs")})
    assert first["sources_sha256"] != with_aliases["sources_sha256"]


def test_retrieval_metadata_records_every_contract_channel():
    args = SimpleNamespace(expand=False, rerank=False, blend=0.3)
    engine = SimpleNamespace(expand_use_llm=False, code_boost=1.5, doc_damp=0.7)
    config, _ = retrieval_metadata(args, engine)
    assert config["heading_weight"] == 0.7
    assert config["heading_fallback"] is False
    assert config["alias_weight"] == 1.0
    assert config["intent_hint_weight"] == 0.7
    assert config["structure_trust"] == 0.7


def test_failure_reason_distinguishes_retrieval_and_rerank():
    gold = [{"chunk_id": 7, "embedded": True}]
    assert failure_reason(gold, {}, 1) is None
    assert failure_reason([], {}, None) == "gold_not_indexed"
    assert failure_reason(gold, {"candidate_ids": []}, None) == "not_retrieved"
    assert failure_reason(
        gold, {"candidate_ids": [7], "fusion_ids": []}, None
    ) == "filtered_before_rerank"
    assert failure_reason(
        gold, {"candidate_ids": [7], "fusion_ids": [7]}, None
    ) == "reranked_out"


def test_baseline_metrics_and_metadata_present():
    base = json.loads((ROOT / "evals/baselines/baseline-v8.json").read_text(encoding="utf-8"))
    assert base["metrics"]["n"] >= 100
    assert base["metrics"]["recall@5"] >= 0.96
    assert base["metrics"]["recall@1"] >= 0.78
    assert base["config"] == {
        "rerank": True,
        "blend": 0.3,
        "expand": True,
        "expand_use_llm": True,
        "code_boost": 1.5,
        "doc_damp": 0.7,
        "rrf_k": 60,
        "heading_weight": 0.7,
        "heading_fallback": False,
        "alias_weight": 1.0,
        "intent_hint_weight": 0.7,
        "structure_trust": 0.7,
        "result_limit": 5,
        "query_expansion_cache_sha256": base["config"]["query_expansion_cache_sha256"],
    }
    assert len(base["implementation"]["sources_sha256"]) == 64
    assert len(base["index"]["sha256"]) == 64
    assert base["cases"]["count"] == base["metrics"]["n"]
