"""知识源契约的只读 MCP stdio 服务。

暴露四个只读工具：contract_sources / contract_validate / contract_rules /
contract_suggest_hints。不提供任何写入、提案或落盘能力。

启动：PYTHONPATH=src .venv/bin/python -m agent_app.contract.mcp_server
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

from .service import ContractService

ROOT = Path(__file__).resolve().parents[3]
PROTOCOL_VERSIONS = {"2024-11-05", "2025-03-26", "2025-06-18"}
DEFAULT_PROTOCOL_VERSION = "2025-06-18"

TOOL_DEFINITIONS = [
    {
        "name": "contract_sources",
        "description": "列出已登记的知识源及其接入契约声明（provides / 内容边界 / 意图映射）。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "contract_validate",
        "description": "校验知识源是否满足接入契约，返回 error/warning 发现与判定。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "知识源名；省略则校验全部"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "contract_rules",
        "description": "列出契约的核心规则（7 条）及其适用条件与判定口径。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "contract_suggest_hints",
        "description": "列出知识源的一级目录，作为编写 intent_map（中文意图→路径）时的候选。",
        "inputSchema": {
            "type": "object",
            "properties": {"source": {"type": "string"}},
            "required": ["source"],
            "additionalProperties": False,
        },
    },
]

#: 工具名 → 服务方法名（显式映射，不靠前缀猜测；ADR-0021）。
TOOL_METHODS = {
    "contract_sources": "sources",
    "contract_validate": "validate",
    "contract_rules": "rules",
    "contract_suggest_hints": "suggest_hints",
}


class ContractMcpServer:
    def __init__(self, service: ContractService):
        self.service = service

    def _tool_call(self, arguments: dict) -> dict:
        name = arguments.get("name")
        params = arguments.get("arguments") or {}
        method_name = TOOL_METHODS.get(name)
        if method_name is None:
            return {
                "content": [{"type": "text", "text": f"未知工具: {name}"}],
                "isError": True,
            }
        try:
            result = getattr(self.service, method_name)(**params)
        except Exception as exc:  # server 进程绝不因单次调用失败而退出（ADR-0021）
            return {
                "content": [{
                    "type": "text",
                    "text": f"工具执行失败: {exc}\n{traceback.format_exc(limit=3)}",
                }],
                "isError": True,
            }
        if isinstance(result, list):
            result = {"results": result}
        return {
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}],
            "structuredContent": result,
            "isError": False,
        }

    def handle(self, request: dict) -> dict | None:
        method = request.get("method")
        request_id = request.get("id")
        if request_id is None:
            return None
        if method == "initialize":
            params = request.get("params") or {}
            requested = params.get("protocolVersion")
            version = requested if requested in PROTOCOL_VERSIONS else DEFAULT_PROTOCOL_VERSION
            result = {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "dev-kb-contract", "version": "0.1.0"},
                "instructions": "只读的知识源接入契约校验服务：查声明、查规则、查发现。",
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOL_DEFINITIONS}
        elif method == "tools/call":
            result = self._tool_call(request.get("params") or {})
        elif method == "shutdown":
            result = None
        else:
            return {
                "jsonrpc": "2.0", "id": request_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }
        return {"jsonrpc": "2.0", "id": request_id, "result": result}


def main() -> int:
    parser = argparse.ArgumentParser(description="知识源契约只读 MCP stdio 服务")
    parser.add_argument("--config", default=None, help="projects.yaml 路径")
    parser.add_argument("--db", default=str(ROOT / "data/meta.db"))
    args = parser.parse_args()
    server = ContractMcpServer(ContractService(db_path=args.db, config=args.config))
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            if request.get("method") == "exit":
                break
            response = server.handle(request)
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                sys.stdout.flush()
        except json.JSONDecodeError as exc:
            error = {"jsonrpc": "2.0", "id": None,
                     "error": {"code": -32700, "message": f"Parse error: {exc}"}}
            sys.stdout.write(json.dumps(error, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
