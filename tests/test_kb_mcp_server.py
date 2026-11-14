"""kb_spec MCP 协议与 stdio 端到端测试。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent_app.kb_spec.mcp_server import TOOL_DEFINITIONS, McpServer
from agent_app.kb_spec.service import KBSpecService
from kb_fixture import write_project

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "fixture-kb"


@pytest.fixture()
def env(tmp_path):
    root = tmp_path / "kb"
    write_project(root)
    config = tmp_path / "kb_projects.yaml"
    config.write_text(json.dumps(
        {"projects": [{"name": PROJECT, "path": str(root)}]}, ensure_ascii=False),
        encoding="utf-8")
    return KBSpecService(config, tmp_path / "meta.db"), config, tmp_path


def test_tool_surface_is_complete():
    names = [t["name"] for t in TOOL_DEFINITIONS]
    assert len(names) == 18
    for expected in ("kb_projects", "kb_get_document", "kb_search",
                     "kb_validate_document", "kb_validate_project",
                     "kb_list_proposals", "kb_show_proposal", "kb_propose_create",
                     "kb_propose_update", "kb_propose_move", "kb_propose_deprecate",
                     "kb_propose_archive", "kb_propose_delete", "kb_apply_proposal"):
        assert expected in names
    # reject 不暴露给 agent（Q8b：驳回仅 kb_cli）
    assert "kb_reject_proposal" not in names
    for t in TOOL_DEFINITIONS:
        assert t["inputSchema"]["type"] == "object"


def test_initialize_and_tools_list(env):
    service, _, _ = env
    server = McpServer(service)
    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                              "params": {"protocolVersion": "2025-06-18"}})
    assert response["result"]["serverInfo"]["name"] == "dev-kb-kb-spec"
    listing = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert len(listing["result"]["tools"]) == 18


def test_tool_call_validate_and_propose(env):
    service, _, _ = env
    server = McpServer(service)
    call = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
        "name": "kb_validate_document",
        "arguments": {"project": PROJECT, "content": "# 坏标题。\n\n正文没有摘要。"}}})
    structured = call["result"]["structuredContent"]
    assert {f["rule_id"] for f in structured["findings"]} >= {
        "KB-3.1-doc-missing-fm", "KB-5.2-summary-required"}

    snap = service.build_snapshot(PROJECT)[1]
    content = snap.files["01-基础概念/01-安装与环境配置.md"]
    call2 = server.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
        "name": "kb_propose_create",
        "arguments": {"project": PROJECT, "path": "01-基础概念/99-新文档.md",
                      "content": content, "dry_run": True}}})
    assert call2["result"]["structuredContent"]["status"] == "dry_run"
    assert call2["result"]["isError"] is False


def test_unknown_tool_and_bad_params(env):
    service, _, _ = env
    server = McpServer(service)
    unknown = server.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                             "params": {"name": "kb_hack", "arguments": {}}})
    assert unknown["result"]["isError"] is True
    bad = server.handle({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                         "params": {"name": "kb_propose_create",
                                    "arguments": {"project": PROJECT}}})
    assert bad["result"]["isError"] is True   # TypeError → 参数错误

    err = server.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {
        "name": "kb_validate_project", "arguments": {"project": "ghost"}}})
    assert err["result"]["isError"] is True   # KBSpecError → error 结果
    assert err["result"]["structuredContent"]["status"] == "error"


def test_tool_method_mapping_is_complete():
    """每个工具名都必须映射到真实存在的 service 方法（否则 AttributeError 会崩掉 server）。"""
    from agent_app.kb_spec.mcp_server import TOOL_DEFINITIONS, TOOL_METHODS
    from agent_app.kb_spec.service import KBSpecService
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert names == set(TOOL_METHODS), names.symmetric_difference(TOOL_METHODS)
    svc = KBSpecService()
    for tool, method in TOOL_METHODS.items():
        assert hasattr(svc, method), f"{tool} → {method} 不存在"


def test_server_survives_unexpected_exception(env, monkeypatch):
    """未预期异常必须变成 error 结果，不得让 server 进程退出。"""
    service, _, _ = env
    server = McpServer(service)

    def boom(**kwargs):
        raise RuntimeError("内部故障")

    monkeypatch.setattr(service, "kb_projects", boom)
    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": "kb_projects", "arguments": {}}})
    assert response["result"]["isError"] is True
    assert "RuntimeError" in response["result"]["structuredContent"]["message"]
    # server 仍能继续响应
    again = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert len(again["result"]["tools"]) == 18


def test_list_and_show_proposals_tools_work(env):
    """kb_list_proposals / kb_show_proposal 必须能调用（曾因方法名不匹配崩溃）。"""
    service, _, _ = env
    server = McpServer(service)
    listing = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                             "params": {"name": "kb_list_proposals", "arguments": {}}})
    assert listing["result"]["isError"] is False
    assert listing["result"]["structuredContent"]["status"] == "ok"
    showing = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                             "params": {"name": "kb_show_proposal",
                                        "arguments": {"proposal_id": 999999}}})
    assert showing["result"]["isError"] is False
    assert showing["result"]["structuredContent"]["status"] == "not_found"


def test_mcp_stdio_end_to_end(tmp_path):
    root = tmp_path / "kb"
    write_project(root)
    config = tmp_path / "kb_projects.yaml"
    config.write_text(json.dumps(
        {"projects": [{"name": PROJECT, "path": str(root)}]}, ensure_ascii=False),
        encoding="utf-8")
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "kb_projects", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "kb_validate_project", "arguments": {"project": PROJECT}}},
        {"jsonrpc": "2.0", "method": "exit"},
    ]
    process = subprocess.run(
        [sys.executable, "-m", "agent_app.kb_spec.mcp_server",
         "--config", str(config), "--db", str(tmp_path / "stdio.db")],
        input="".join(json.dumps(r) + "\n" for r in requests),
        text=True, capture_output=True, timeout=30, cwd=ROOT,
        env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"},
    )
    assert process.returncode == 0, process.stderr
    responses = [json.loads(line) for line in process.stdout.splitlines()]
    assert [r.get("id") for r in responses] == [1, 2, 3, 4]
    assert responses[2]["result"]["structuredContent"]["status"] == "ok"
    projects = responses[2]["result"]["structuredContent"]["projects"]
    assert projects[0]["name"] == PROJECT
    assert responses[3]["result"]["structuredContent"]["summary"]["errors"] == 0
