"""kb_cli 人工入口测试：校验退出码、提案审批闭环（审批发生在 agent 循环之外）。"""

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

    def service():
        return KBSpecService(config, db)

    return run, service, root


def test_validate_clean_exit_zero(kb_env):
    run, _, _ = kb_env
    code, out = run("validate", PROJECT)
    assert code == 0
    assert "error=0" in out


def test_validate_dirty_exit_one(kb_env):
    run, _, root = kb_env
    (root / TARGET_DOC).write_text(
        "# 坏标题。\n\n没有摘要与元数据，正文也短。\n", encoding="utf-8")
    code, out = run("validate", PROJECT)
    assert code == 1
    assert "KB-3.1-doc-missing-fm" in out


def test_validate_json_mode(kb_env):
    run, _, _ = kb_env
    code, out = run("validate", PROJECT, "--document", TARGET_DOC, "--json")
    assert code in (0, 1)
    assert json.loads(out)["target"] == TARGET_DOC


def test_proposal_approve_apply_flow(kb_env):
    run, service, root = kb_env
    # agent 侧：通过共享 meta.db 生成提案（标题变更 → README 同步）
    snap = service().build_snapshot(PROJECT)[1]
    content = snap.files[TARGET_DOC].replace("# 安装与环境配置", "# 安装与运行环境")
    pid = service().propose_update(PROJECT, TARGET_DOC, content)["proposal_id"]

    code, out = run("proposals", "list")
    assert code == 0 and f"#{pid} [pending]" in out
    code, out = run("proposals", "show", str(pid))
    assert code == 0 and "安装与运行环境" in out

    # apply 前置：未批准 → 拒绝
    code, out = run("apply", str(pid))
    assert code == 1 and "not_approved" in out

    # 人工批准 → 应用
    code, out = run("proposals", "approve", str(pid))
    assert code == 0
    code, out = run("apply", str(pid))
    assert code == 0 and "已应用" in out and "应用成功 1 个" in out
    assert "# 安装与运行环境" in (root / TARGET_DOC).read_text(encoding="utf-8")

    code, out = run("proposals", "list", "--all")
    assert f"#{pid} [applied]" in out


def test_proposal_reject_flow(kb_env):
    run, service, root = kb_env
    snap = service().build_snapshot(PROJECT)[1]
    pid = service().propose_update(PROJECT, TARGET_DOC,
                                   snap.files[TARGET_DOC])["proposal_id"]
    code, out = run("proposals", "reject", str(pid))
    assert code == 0
    code, out = run("apply", str(pid))
    assert code == 1 and "not_approved" in out
    # 文件未被改动
    assert "# 安装与环境配置" in (root / TARGET_DOC).read_text(encoding="utf-8")


def test_projects_command(kb_env):
    run, _, _ = kb_env
    code, out = run("projects")
    assert code == 0
    payload = json.loads(out)
    assert payload["projects"][0]["name"] == PROJECT
    assert payload["projects"][0]["documents"] == 9
