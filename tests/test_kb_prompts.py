"""Prompts 与新工具测试：六个 prompt 模板 + kb_search_guided + kb_draft_document + kb_suggest_fixes。"""

from __future__ import annotations

import json

import pytest

from agent_app.kb_spec.mcp_server import McpServer, TOOL_DEFINITIONS
from agent_app.kb_spec.service import KBSpecService
from kb_fixture import write_project

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


def test_tool_surface_includes_new_tools():
    names = [t["name"] for t in TOOL_DEFINITIONS]
    assert len(names) == 18
    for expected in ("kb_search_guided", "kb_draft_document", "kb_suggest_fixes"):
        assert expected in names


def test_prompts_list_and_get(env):
    service, _, _ = env
    server = McpServer(service)
    # prompts/list
    listing = server.handle({"jsonrpc": "2.0", "id": 1, "method": "prompts/list"})
    assert len(listing["result"]["prompts"]) == 6
    names = {p["name"] for p in listing["result"]["prompts"]}
    assert names == {"kb_write_guide", "kb_review_guide", "kb_fix_guide",
                     "kb_migrate_guide", "kb_style_guide", "kb_onboard_guide"}
    # prompts/get: kb_write_guide
    result = server.handle({"jsonrpc": "2.0", "id": 2, "method": "prompts/get",
                            "params": {"name": "kb_write_guide",
                                       "arguments": {"topic": "测试主题", "project": PROJECT}}})
    assert "messages" in result["result"]
    text = result["result"]["messages"][0]["content"]["text"]
    assert "写作引导" in text and "规范检查清单" in text
    # prompts/get: kb_style_guide（无参数）
    result2 = server.handle({"jsonrpc": "2.0", "id": 3, "method": "prompts/get",
                             "params": {"name": "kb_style_guide", "arguments": {}}})
    assert "风格检查清单" in result2["result"]["messages"][0]["content"]["text"]


def test_search_guided_intent_detection(env):
    service, _, _ = env
    # 写作者图
    r = service.kb_search_guided("我想写一篇关于工作流的文档", project=PROJECT)
    assert r["intent"] == "写作"
    assert "kb_draft_document" in r["suggested_action"]
    # 查询意图
    r2 = service.kb_search_guided("工作流在哪里配置", project=PROJECT)
    assert r2["intent"] == "查询"
    # 修复意图
    r3 = service.kb_search_guided("修复文档的格式问题", project=PROJECT)
    assert r3["intent"] == "修复"
    # 校验意图
    r4 = service.kb_search_guided("检查文档是否合规", project=PROJECT)
    assert r4["intent"] == "校验"


def test_draft_document_skeleton(env):
    service, _, _ = env
    r = service.kb_draft_document(PROJECT, "ComfyUI 视频生成技术详解")
    assert r["status"] == "ok"
    assert r["doc_type"] == "技术文档"
    assert "状态: 草案" in r["content"]
    assert "## 核心概念" in r["content"]
    assert "## 实现原理" in r["content"]
    assert len(r["next_steps"]) == 5


def test_draft_document_operation_guide(env):
    service, _, _ = env
    r = service.kb_draft_document(PROJECT, "工作流部署指南", doc_type="操作指南")
    assert r["doc_type"] == "操作指南"
    assert "## 前置条件" in r["content"]
    assert "## 操作步骤" in r["content"]


def test_suggest_fixes_structure(env):
    service, _, _ = env
    findings = [
        {"rule_id": "KB-3.1-doc-missing-fm", "severity": "error",
         "message": "缺 frontmatter", "location": "test.md:1"},
        {"rule_id": "KB-10-language-tag", "severity": "error",
         "message": "代码块未标注语言", "location": "test.md:10"},
    ]
    r = service.kb_suggest_fixes(findings)
    assert r["status"] == "ok"
    assert len(r["suggestions"]) == 2
    assert r["suggestions"][0]["steps"]
    assert r["suggestions"][1]["example_after"]


def test_mcp_stdio_with_new_tools(tmp_path):
    import subprocess, sys
    from pathlib import Path
    ROOT = Path(__file__).resolve().parents[1]
    root = tmp_path / "kb"
    write_project(root)
    config = tmp_path / "kb_projects.yaml"
    config.write_text(json.dumps(
        {"projects": [{"name": PROJECT, "path": str(root)}]}, ensure_ascii=False),
        encoding="utf-8")
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "prompts/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "kb_search_guided",
                    "arguments": {"query": "我想写文档", "project": PROJECT}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "kb_draft_document",
                    "arguments": {"project": PROJECT, "title": "测试文档"}}},
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
    assert len(responses) == 4
    assert responses[1]["result"]["prompts"]
    assert responses[2]["result"]["structuredContent"]["intent"] == "写作"
    assert responses[3]["result"]["structuredContent"]["status"] == "ok"
