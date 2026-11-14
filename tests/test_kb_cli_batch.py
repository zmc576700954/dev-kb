"""kb_cli 批量操作测试：批量批准/驳回/应用，含按文档分组的依赖处理。"""

from __future__ import annotations

import json

import pytest

from agent_app.kb_spec import cli
from agent_app.kb_spec.service import KBSpecService
from kb_fixture import write_project

PROJECT = "fixture-kb"
TARGET_DOC = "01-基础概念/01-安装与环境配置.md"


@pytest.fixture()
def kb_env(tmp_path, capsys):
    root = tmp_path / "kb"
    write_project(root)
    config = tmp_path / "kb_projects.yaml"
    config.write_text(json.dumps(
        {"projects": [{"name": PROJECT, "path": str(root)}]}, ensure_ascii=False),
        encoding="utf-8")
    db = tmp_path / "meta.db"
    argv_base = ["--config", str(config), "--db", str(db)]

    def run(*args):
        code = cli.main([*argv_base, *args])
        return code, capsys.readouterr().out

    svc = KBSpecService(config, db)
    return run, svc, root


ALL_DOCS = [
    "01-基础概念/01-安装与环境配置.md",
    "01-基础概念/02-节点连接方式.md",
    "01-基础概念/03-插件管理机制.md",
]


def _make_proposals(service, count: int, same_doc: bool = False):
    """生成 count 个提案。

    same_doc=False：分散到不同文档——彼此无 before_sha 依赖，可依次成功应用。
    same_doc=True：同一文档的多个提案——都基于同一份 before，
    apply 第一个后其余必然 stale（乐观并发的预期行为）。
    """
    _, snap = service.build_snapshot(PROJECT)
    ids = []
    for i in range(count):
        path = TARGET_DOC if same_doc else ALL_DOCS[i % len(ALL_DOCS)]
        content = snap.files[path]
        # 造一个真实变更：改摘要（触发 README 同步，但不影响文档间依赖）
        lines = content.split("\n")
        for j, ln in enumerate(lines):
            if ln.startswith("> "):
                lines[j] = ln + "补充说明。"
                break
        new = "\n".join(lines)
        r = service.propose_update(PROJECT, path, new, snap.sha_of(path))
        assert r["status"] == "proposal_created", r.get("reject_findings")
        ids.append(r["proposal_id"])
    return ids


def test_batch_approve_multiple_ids(kb_env):
    run, service, _ = kb_env
    ids = _make_proposals(service, 3)
    code, out = run("proposals", "approve", *[str(i) for i in ids])
    assert code == 0
    assert "批准成功 3 个" in out
    for pid in ids:
        assert service.proposals.get(pid).status == "approved"


def test_batch_approve_by_project(kb_env):
    run, service, _ = kb_env
    ids = _make_proposals(service, 2)
    code, out = run("proposals", "approve", "--project", PROJECT)
    assert code == 0
    assert "批准成功 2 个" in out
    for pid in ids:
        assert service.proposals.get(pid).status == "approved"


def test_batch_approve_dry_run(kb_env):
    run, service, _ = kb_env
    ids = _make_proposals(service, 2)
    code, out = run("proposals", "approve", "--project", PROJECT, "--dry-run")
    assert code == 0
    assert "dry-run" in out
    for pid in ids:
        assert service.proposals.get(pid).status == "pending"   # 未实际执行


def test_batch_reject(kb_env):
    run, service, _ = kb_env
    ids = _make_proposals(service, 2)
    code, out = run("proposals", "reject", "--project", PROJECT)
    assert code == 0
    assert "驳回成功 2 个" in out
    for pid in ids:
        assert service.proposals.get(pid).status == "rejected"


def test_batch_apply_by_project(kb_env):
    run, service, root = kb_env
    ids = _make_proposals(service, 2)
    run("proposals", "approve", "--project", PROJECT)
    code, out = run("apply", "--project", PROJECT)
    assert code == 0
    assert "应用成功 2 个" in out
    for pid in ids:
        assert service.proposals.get(pid).status == "applied"


def test_batch_apply_dry_run(kb_env):
    run, service, _ = kb_env
    ids = _make_proposals(service, 2)
    run("proposals", "approve", "--project", PROJECT)
    code, out = run("apply", "--project", PROJECT, "--dry-run")
    assert code == 0
    assert "dry-run" in out
    for pid in ids:
        assert service.proposals.get(pid).status == "approved"   # 未实际应用


def test_batch_apply_skips_dependents_after_stale(kb_env):
    """同文档的第二个提案 stale 时，该文档剩余提案应跳过，其他文档继续。"""
    run, service, root = kb_env
    ids = _make_proposals(service, 2, same_doc=True)
    run("proposals", "approve", "--project", PROJECT)
    # 人为让文件漂移 → 第一个提案 stale
    (root / TARGET_DOC).write_text(
        (root / TARGET_DOC).read_text(encoding="utf-8") + "\n漂移\n", encoding="utf-8")
    code, out = run("apply", "--project", PROJECT)
    assert code == 1
    assert "stale" in out
    assert "跳过" in out
    # 两个都没落盘
    for pid in ids:
        assert service.proposals.get(pid).status == "approved"


def test_apply_requires_target(kb_env):
    run, _, _ = kb_env
    code, out = run("apply")
    assert code == 1
    assert "请提供提案 id" in out


def test_dry_run_flags_stale_proposals(kb_env):
    """dry-run 预检应标出会 stale 的提案（前置条件已不成立）。"""
    run, service, root = kb_env
    ids = _make_proposals(service, 1)
    run("proposals", "approve", "--project", PROJECT)
    # 让文件漂移 → 提案前置条件不再成立
    (root / TARGET_DOC).write_text(
        (root / TARGET_DOC).read_text(encoding="utf-8") + "\n漂移\n", encoding="utf-8")
    code, out = run("apply", "--project", PROJECT, "--dry-run")
    assert code == 0
    assert "将 stale" in out
    assert "内容已变" in out


def test_dry_run_shows_no_warning_for_valid_proposals(kb_env):
    """前置条件仍成立的提案不应被标为 stale。"""
    run, service, _ = kb_env
    _make_proposals(service, 2)
    run("proposals", "approve", "--project", PROJECT)
    code, out = run("apply", "--project", PROJECT, "--dry-run")
    assert code == 0
    assert "将 stale" not in out


def test_can_revoke_approved_proposal(kb_env):
    """批准后、应用前发现过时 → 应能撤回批准（approved → rejected）。"""
    run, service, _ = kb_env
    ids = _make_proposals(service, 1)
    run("proposals", "approve", "--project", PROJECT)
    assert service.proposals.get(ids[0]).status == "approved"
    code, out = run("proposals", "reject", str(ids[0]))
    assert code == 0
    assert "驳回成功 1 个" in out
    assert service.proposals.get(ids[0]).status == "rejected"


def test_applied_proposal_cannot_be_revoked(kb_env):
    """已落盘的提案不可撤回（内容已生效）。"""
    run, service, _ = kb_env
    ids = _make_proposals(service, 1)
    run("proposals", "approve", "--project", PROJECT)
    run("apply", "--project", PROJECT)
    assert service.proposals.get(ids[0]).status == "applied"
    code, out = run("proposals", "reject", str(ids[0]))
    assert "跳过" in out
    assert service.proposals.get(ids[0]).status == "applied"


def test_rejected_proposal_cannot_be_revived(kb_env):
    """已驳回的提案不可再批准。"""
    run, service, _ = kb_env
    ids = _make_proposals(service, 1)
    run("proposals", "reject", "--project", PROJECT)
    code, out = run("proposals", "approve", str(ids[0]))
    assert "跳过" in out
    assert service.proposals.get(ids[0]).status == "rejected"


def test_group_by_target_orders_by_id(kb_env):
    """分组必须按文档聚合并保持 id 升序（before_sha 依赖）。"""
    from agent_app.kb_spec.cli import _group_by_target
    proposals = [
        {"id": 5, "target": "a.md"}, {"id": 2, "target": "a.md"},
        {"id": 9, "target": "b.md"},
    ]
    groups = _group_by_target(proposals)
    assert [t for t, _ in groups] == ["a.md", "b.md"]
    assert [p["id"] for p in groups[0][1]] == [2, 5]


def test_cross_document_apply_merges_readme(kb_env):
    """跨文档提案各自基于同一份旧 README → 后者必须在当前 README 上重放条目增量。"""
    run, service, root = kb_env
    ids = _make_proposals(service, 2)          # 两个不同文档
    run("proposals", "approve", "--project", PROJECT)
    code, out = run("apply", "--project", PROJECT)
    assert code == 0, out
    assert "应用成功 2 个" in out
    readme = (root / "README.md").read_text(encoding="utf-8")
    # 两个文档的条目都要在最终 README 里
    for path in ALL_DOCS[:2]:
        assert f"](./{path})" in readme, f"{path} 的条目丢失"


def test_readme_patch_replay_keeps_other_entries(kb_env):
    """重放只改自己的条目，不影响 README 里其他文档的条目。"""
    run, service, root = kb_env
    ids = _make_proposals(service, 3)          # 三个不同文档
    run("proposals", "approve", "--project", PROJECT)
    code, out = run("apply", "--project", PROJECT)
    assert code == 0, out
    readme = (root / "README.md").read_text(encoding="utf-8")
    for path in ALL_DOCS[:3]:
        assert f"](./{path})" in readme, f"{path} 的条目丢失"
