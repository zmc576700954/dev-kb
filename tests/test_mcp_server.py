"""只读 MCP、项目路由与原文件边界测试，不加载向量模型。"""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import yaml

from agent_app.indexing.metadata_store import MetadataStore
from agent_app.mcp_server import KnowledgeBaseService, McpServer
from agent_app.tools.routing import route_project

ROOT = Path(__file__).resolve().parents[1]


class FakeEngine:
    def __init__(self):
        self.calls = []

    def search(self, query, project=None, limit=5, diagnostics=None):
        self.calls.append({"query": query, "project": project, "limit": limit})
        if diagnostics is not None:
            diagnostics["expanded_terms"] = ["cached_symbol"]
        return [{
            "chunk_id": 1, "project": project or "demo", "path": "app.py",
            "symbol": "f", "start_line": 1, "end_line": 1, "matched_by": ["symbol"],
            "score": 1.0, "content": "def f(): pass",
        }]


def make_service(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    content = b"def f():\n    return 1\n"
    (root / "app.py").write_bytes(content)
    (root / "secret.txt").write_text("outside knowledge", encoding="utf-8")
    config = tmp_path / "projects.yaml"
    config.write_text(yaml.safe_dump({
        "projects": [{"name": "demo", "path": str(root), "type": "auto"}]
    }), encoding="utf-8")
    db_path = tmp_path / "test.db"
    store = MetadataStore(db_path)
    store.upsert_file("demo", "app.py", 0, len(content), hashlib.sha1(content).hexdigest(), "")
    store.close()
    engine = FakeEngine()
    service = KnowledgeBaseService(config, db_path, engine=engine)
    return service, engine, config


def test_route_requires_unique_project_or_explicit_global():
    registry = {"alpha": {}, "beta": {}}
    assert route_project("alpha 的入口", registry)["project"] == "alpha"
    ambiguous = route_project("通用入口在哪", registry)
    assert ambiguous["status"] == "project_required"
    assert ambiguous["candidates"] == ["alpha", "beta"]
    explicit = route_project("通用入口在哪", registry, all_projects=True)
    assert explicit["status"] == "ok" and explicit["project"] is None


def test_mcp_search_does_not_silently_search_all_projects(tmp_path):
    service, engine, _ = make_service(tmp_path)
    result = service.search("入口在哪")
    assert result["status"] == "project_required" and not engine.calls
    result = service.search("入口在哪", project="demo")
    assert result["status"] == "ok" and result["results"][0]["symbol"] == "f"
    assert result["expanded_terms"] == ["cached_symbol"]
    assert engine.calls[-1]["project"] == "demo"


def test_mcp_read_source_checks_boundaries_and_freshness(tmp_path):
    service, _, _ = make_service(tmp_path)
    result = service.read_source("demo", "app.py")
    assert result["stale_since_index"] is False
    assert "return 1" in result["content"]
    assert "error" in service.read_source("demo", "../secret.txt")
    assert "error" in service.read_source("demo", "/etc/passwd")


def test_mcp_protocol_initialize_list_and_call(tmp_path):
    service, _, _ = make_service(tmp_path)
    server = McpServer(service)
    initialized = server.handle({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26"},
    })
    assert initialized["result"]["protocolVersion"] == "2025-03-26"
    tools = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert {item["name"] for item in tools["result"]["tools"]} == {
        "index_status", "search", "read_source"
    }
    response = server.handle({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "index_status", "arguments": {}},
    })
    assert response["result"]["structuredContent"]["read_only"] is True


def test_mcp_stdio_end_to_end(tmp_path):
    _, _, config = make_service(tmp_path)
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "index_status", "arguments": {}}},
        {"jsonrpc": "2.0", "method": "exit"},
    ]
    env = {"PYTHONPATH": str(ROOT / "src")}
    process = subprocess.run(
        [sys.executable, "-m", "agent_app.mcp_server", "--config", str(config),
         "--db", str(tmp_path / "stdio.db")],
        input="".join(json.dumps(request) + "\n" for request in requests),
        text=True, capture_output=True, timeout=15, cwd=ROOT, env=env,
    )
    assert process.returncode == 0, process.stderr
    responses = [json.loads(line) for line in process.stdout.splitlines()]
    assert [response["id"] for response in responses] == [1, 2, 3]
    assert responses[2]["result"]["structuredContent"]["status"] == "ok"
