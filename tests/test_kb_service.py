"""服务层与提案门禁测试（决策 Q8b：pending 不可落盘，approved 一次生效，漂移拒绝）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_app.kb_spec.proposals import KBProposalStore
from agent_app.kb_spec.service import KBSpecError, KBSpecService
from kb_fixture import project_files, write_project

PROJECT = "fixture-kb"
TARGET_DOC = "01-基础概念/01-安装与环境配置.md"


@pytest.fixture()
def service(tmp_path):
    root = tmp_path / "kb"
    write_project(root)
    config = tmp_path / "kb_projects.yaml"
    config.write_text(json.dumps(
        {"projects": [{"name": PROJECT, "path": str(root)}]}, ensure_ascii=False),
        encoding="utf-8")
    svc = KBSpecService(config, tmp_path / "meta.db")
    svc.fixture_root = root
    return svc


def approve(service, pid):
    assert service.proposals.decide(pid, "approved")


# ---- 校验 ----

def test_validate_project_clean(service):
    result = service.kb_validate_project(PROJECT)
    assert result["status"] == "ok"
    assert result["summary"]["errors"] == 0, [
        f for f in result["findings"] if f["severity"] == "error"]


def test_validate_document_by_path(service):
    result = service.kb_validate_document(PROJECT, path=TARGET_DOC)
    assert result["verdict"] in ("pass", "warn")


def test_validate_document_adhoc_content(service):
    bad = "# 坏标题。\n\n没有摘要也没有 frontmatter。"
    result = service.kb_validate_document(PROJECT, path="x.md", content=bad)
    rules = {f["rule_id"] for f in result["findings"]}
    assert "KB-3.1-doc-missing-fm" in rules and "KB-5.2-summary-required" in rules


def test_unknown_project_raises(service):
    with pytest.raises(KBSpecError):
        service.require_root("no-such-kb")


def test_path_escape_rejected(service):
    with pytest.raises(KBSpecError):
        service.kb_validate_document(PROJECT, path="../outside.md")


def test_spec_info_checksum_matches(service):
    info = service.spec_info()
    assert info["vendored"] and info["matches"]


# ---- 提案流 ----

def test_full_gate_flow(service):
    content = service.build_snapshot(PROJECT)[1].files[TARGET_DOC].replace(
        "# 安装与环境配置", "# 工作流导入导出")
    result = service.propose_create(PROJECT, "01-基础概念/02-工作流导入导出.md", content)
    assert result["status"] == "proposal_created"
    pid = result["proposal_id"]

    # pending 不可落盘
    assert service.apply_proposal(pid)["status"] == "not_approved"
    # 客户端仍看不到文件
    assert not (service.fixture_root / "01-基础概念/02-工作流导入导出.md").exists()

    approve(service, pid)
    applied = service.apply_proposal(pid)
    assert applied["status"] == "applied"

    doc_path = service.fixture_root / "01-基础概念/02-工作流导入导出.md"
    assert doc_path.exists()
    readme = (service.fixture_root / "README.md").read_text(encoding="utf-8")
    assert "02-工作流导入导出.md" in readme
    # 应用后不可重复应用
    assert service.apply_proposal(pid)["status"] == "already_applied"


def test_dry_run_persists_nothing(service):
    content = service.build_snapshot(PROJECT)[1].files[TARGET_DOC]
    result = service.propose_create(PROJECT, "01-基础概念/99-预演.md", content,
                                    dry_run=True)
    assert result["status"] == "dry_run"
    assert service.list_proposals(PROJECT)["proposals"] == []
    assert not (service.fixture_root / "01-基础概念/99-预演.md").exists()


def test_rejected_proposal_not_stored(service):
    bad = "# 坏文档。\n\n" + "内容" * 200
    result = service.propose_create(PROJECT, "01-基础概念/99-坏.md", bad)
    assert result["status"] == "rejected"
    assert any(f["rule_id"] == "KB-3.1-doc-missing-fm"
               for f in result["reject_findings"])
    assert service.list_proposals(PROJECT)["proposals"] == []


def test_apply_stale_after_drift(service):
    snap = service.build_snapshot(PROJECT)[1]
    result = service.propose_update(PROJECT, TARGET_DOC,
                                    snap.files[TARGET_DOC], snap.sha_of(TARGET_DOC))
    pid = result["proposal_id"]
    approve(service, pid)
    # 审批后项目被人改动了
    (service.fixture_root / TARGET_DOC).write_text(
        snap.files[TARGET_DOC] + "\n人工追加\n", encoding="utf-8")
    assert service.apply_proposal(pid)["status"] == "stale"


def test_move_flow_end_to_end(service):
    old, new = "03-应用实践/02-质量抽检流程.md", "03-应用实践/02-质量抽查流程.md"
    pid = service.propose_move(PROJECT, old, new)["proposal_id"]
    approve(service, pid)
    assert service.apply_proposal(pid)["status"] == "applied"
    assert not (service.fixture_root / old).exists()
    assert (service.fixture_root / new).exists()
    sibling = (service.fixture_root / "03-应用实践/01-素材批量生产.md").read_text(
        encoding="utf-8")
    assert "](./02-质量抽查流程.md)" in sibling and "](./02-质量抽检流程.md)" not in sibling
    readme = (service.fixture_root / "README.md").read_text(encoding="utf-8")
    assert f"](./{new})" in readme
    # apply 后项目重新全量校验仍然零 error
    assert service.kb_validate_project(PROJECT)["summary"]["errors"] == 0


def test_delete_flow_removes_empty_category(service):
    """删除分类最后一篇文档 → 空分类目录被清理。

    三篇文档都要走提案流程删除（直接 unlink 会让 README 与实际状态不一致，
    后续提案会被 README 断链校验拒绝——这是预期行为）。
    """
    for path, reason in [
        ("03-应用实践/01-素材批量生产.md", "主题并入 02-核心技术"),
        ("03-应用实践/03-发布与归档方式.md", "内容已过时"),
        ("03-应用实践/02-质量抽检流程.md", "主题已并入 02-核心技术"),
    ]:
        r = service.propose_delete(PROJECT, path, reason)
        assert r["status"] == "proposal_created", r.get("reject_findings")
        approve(service, r["proposal_id"])
        assert service.apply_proposal(r["proposal_id"])["status"] == "applied"
    assert not (service.fixture_root / "03-应用实践").exists()
    readme = (service.fixture_root / "README.md").read_text(encoding="utf-8")
    # 删除分类最后一篇文档后，条目行被移除；空 `## 分类` 标题的清理是已知限制
    assert "02-质量抽检流程.md" not in readme
    assert "01-素材批量生产.md" not in readme
    assert "03-发布与归档方式.md" not in readme


def test_reject_keeps_pending_gate(service):
    content = service.build_snapshot(PROJECT)[1].files[TARGET_DOC]
    pid = service.propose_update(PROJECT, TARGET_DOC, content)["proposal_id"]
    assert service.proposals.decide(pid, "rejected")
    assert service.apply_proposal(pid)["status"] == "not_approved"
    assert service.proposals.decide(pid, "approved") is False   # 已驳回不可再批


def test_proposal_store_lifecycle(service):
    store = service.proposals
    content = service.build_snapshot(PROJECT)[1].files[TARGET_DOC]
    pid = service.propose_update(PROJECT, TARGET_DOC, content)["proposal_id"]
    p = store.get(pid)
    assert p.status == "pending" and p.ops
    store.decide(pid, "approved")
    store.mark_applied(pid, {"ops": 1})
    assert store.get(pid).status == "applied"
    with pytest.raises(ValueError):
        store.decide(pid, "bogus")


def test_pending_note_attached(service):
    content = service.build_snapshot(PROJECT)[1].files[TARGET_DOC]
    pid = service.propose_update(PROJECT, TARGET_DOC, content)["proposal_id"]
    second = service.propose_update(PROJECT, TARGET_DOC, content)
    reviews = [f for f in second["findings"] if f["rule_id"] == "KB-OP-conflict"]
    assert reviews and reviews[0]["evidence"]["proposal_id"] == pid


def test_kb_search_requires_project(service):
    result = service.kb_search("工作流")
    assert result["status"] == "project_required"
    assert PROJECT in result["candidates"]
    assert service.kb_search("工作流", project="nope")["status"] == "unknown_project"


def test_kb_get_document_view(service):
    result = service.kb_get_document(PROJECT, TARGET_DOC)
    assert result["status"] == "ok"
    assert result["title"] == "安装与环境配置"
    assert result["status_base"] == "稳定"
    assert len(result["outline"]) >= 4
    assert any(lk["from"] == "README.md" for lk in result["inlinks"])
    assert len(result["content_sha"]) == 64
