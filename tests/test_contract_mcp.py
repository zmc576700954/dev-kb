"""知识源接入契约：CLI 与 MCP stdio 端到端。"""

from __future__ import annotations

import json
import subprocess
import sys

from agent_app.contract.cli import main as cli_main
from agent_app.contract.mcp_server import TOOL_DEFINITIONS, TOOL_METHODS, ContractMcpServer
from agent_app.contract.service import ContractService


# ---- CLI ----

def test_cli_sources(tmp_path, registry, capsys):
    assert cli_main(["--config", str(registry), "--db", str(tmp_path / "m.db"), "sources"]) == 0
    out = capsys.readouterr().out
    assert "doc-kb" in out and "code-lib" in out


def test_cli_rules_lists_seven(tmp_path, registry, capsys):
    assert cli_main(["--config", str(registry), "--db", str(tmp_path / "m.db"), "rules"]) == 0
    out = capsys.readouterr().out
    for rid in ("R-01", "R-02", "R-03", "R-04", "R-05", "R-06", "R-07"):
        assert rid in out


def test_cli_validate_json(tmp_path, registry, capsys):
    code = cli_main(["--config", str(registry), "--db", str(tmp_path / "m.db"),
                     "validate", "doc-kb", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == "doc-kb"
    assert code in (0, 1)


def test_cli_suggest_hints(tmp_path, registry, capsys):
    assert cli_main(["--config", str(registry), "--db", str(tmp_path / "m.db"),
                     "suggest-hints", "doc-kb"]) == 0
    assert "03-运维" in capsys.readouterr().out


# ---- MCP ----

def test_mcp_tool_definitions_match_methods(tmp_path, registry):
    """显式映射表必须覆盖全部工具，且方法真实存在（ADR-0021）。"""
    svc = ContractService(db_path=tmp_path / "m.db", config=registry)
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert names == set(TOOL_METHODS)
    for method in TOOL_METHODS.values():
        assert callable(getattr(svc, method))
    svc.close()


def test_mcp_call_returns_structured_content(tmp_path, registry):
    svc = ContractService(db_path=tmp_path / "m.db", config=registry)
    server = ContractMcpServer(svc)
    result = server._tool_call({"name": "contract_rules", "arguments": {}})
    assert result["isError"] is False
    # 列表结果按 MCP 约定包一层
    assert len(result["structuredContent"]["results"]) == 7
    svc.close()


def test_mcp_unknown_tool_is_error(tmp_path, registry):
    svc = ContractService(db_path=tmp_path / "m.db", config=registry)
    server = ContractMcpServer(svc)
    result = server._tool_call({"name": "contract_nope", "arguments": {}})
    assert result["isError"] is True
    svc.close()


def test_mcp_survives_tool_exception(tmp_path, registry, monkeypatch):
    """单次调用失败不能让 server 进程退出（ADR-0021）。"""
    svc = ContractService(db_path=tmp_path / "m.db", config=registry)

    def boom(*_a, **_k):
        raise RuntimeError("炸了")

    monkeypatch.setattr(svc, "rules", boom)
    server = ContractMcpServer(svc)
    result = server._tool_call({"name": "contract_rules", "arguments": {}})
    assert result["isError"] is True
    assert "炸了" in result["content"][0]["text"]
    svc.close()


def test_mcp_stdio_end_to_end(tmp_path, registry):
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "contract_sources", "arguments": {}}},
    ]
    payload = "\n".join(json.dumps(r) for r in requests) + "\n"
    proc = subprocess.run(
        [sys.executable, "-m", "agent_app.contract.mcp_server",
         "--config", str(registry), "--db", str(tmp_path / "m.db")],
        input=payload, capture_output=True, text=True,
        env={"PYTHONPATH": "src", "PATH": "/usr/bin:/bin"},
    )
    lines = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    assert len(lines) == 3
    assert lines[0]["result"]["serverInfo"]["name"] == "dev-kb-contract"
    assert len(lines[1]["result"]["tools"]) == 4
    assert lines[2]["result"]["isError"] is False
