"""dev-kb 只读 MCP stdio 服务：index_status / search / read_source。

仅依赖标准库与项目现有依赖，索引同步不在此服务中暴露。
启动：PYTHONPATH=src .venv/bin/python -m agent_app.mcp_server
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent_app.indexing.metadata_store import MetadataStore
from agent_app.config import default_projects_config
from agent_app.tools.projects import load_registry
from agent_app.tools.routing import route_project
from agent_app.tools.source import read_source as read_project_source

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_VERSIONS = {"2024-11-05", "2025-03-26", "2025-06-18"}
DEFAULT_PROTOCOL_VERSION = "2025-06-18"

TOOL_DEFINITIONS = [
    {
        "name": "index_status",
        "description": "读取各项目索引文件数、块数、待向量化数量与同步错误数。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "search",
        "description": "在唯一确定的项目中检索代码和文档；项目不明确时返回候选，不隐式全局搜索。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "project": {"type": "string"},
                "all_projects": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "read_source",
        "description": "按行号回读项目原文件并校验索引后是否变化，返回可复核证据。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "path": {"type": "string"},
                "start_line": {"type": "integer", "minimum": 1, "default": 1},
                "end_line": {"type": "integer", "minimum": 1},
            },
            "required": ["project", "path"],
            "additionalProperties": False,
        },
    },
]


class KnowledgeBaseService:
    def __init__(self, config: str | Path | None = None, db_path: str | Path | None = None,
                 engine=None):
        self.config = str(config or default_projects_config())
        self.store = MetadataStore(db_path or ROOT / "data/meta.db")
        self._engine = engine

    def _search_engine(self):
        if self._engine is None:
            from agent_app.indexing.vector_store import VectorStore
            from agent_app.tools.retrieval import HybridSearch
            vector_store = VectorStore(ROOT / "data/qdrant")
            self._engine = HybridSearch(
                self.store, vector_store, rerank=True, blend=0.3,
                expand=True, expand_use_llm=False,
            )
        return self._engine

    def index_status(self) -> dict:
        return {
            "status": "ok",
            "projects": self.store.stats(),
            "registered_projects": sorted(load_registry(self.config)),
            "read_only": True,
        }

    def search(self, query: str, project: str | None = None,
               all_projects: bool = False, limit: int = 5) -> dict:
        if not query.strip():
            return {"status": "invalid_query", "message": "query 不能为空"}
        limit = max(1, min(int(limit), 10))
        registry = load_registry(self.config)
        scope = route_project(query, registry, project, all_projects)
        if scope["status"] != "ok":
            return scope
        diagnostics: dict = {}
        results = self._search_engine().search(
            query, project=scope["project"], limit=limit, diagnostics=diagnostics
        )
        return {
            "status": "ok",
            "project": scope["project"],
            "all_projects": scope["project"] is None,
            "expanded_terms": diagnostics.get("expanded_terms", []),
            "results": [
                {
                    "chunk_id": item["chunk_id"],
                    "project": item["project"],
                    "path": item["path"],
                    "symbol": item["symbol"],
                    "start_line": item["start_line"],
                    "end_line": item["end_line"],
                    "matched_by": item["matched_by"],
                    "score": item["score"],
                    "rerank_score": item.get("rerank_score"),
                    "index_snippet": item["content"][:400],
                    "evidence_requires_read_source": True,
                }
                for item in results
            ],
        }

    def read_source(self, project: str, path: str, start_line: int = 1,
                    end_line: int | None = None) -> dict:
        return read_project_source(
            self.store, project, path, start_line, end_line, config=self.config
        )


class McpServer:
    def __init__(self, service: KnowledgeBaseService):
        self.service = service

    def _tool_call(self, arguments: dict) -> dict:
        name = arguments.get("name")
        params = arguments.get("arguments") or {}
        try:
            if name == "index_status":
                result = self.service.index_status()
            elif name == "search":
                result = self.service.search(**params)
            elif name == "read_source":
                result = self.service.read_source(**params)
            else:
                return {
                    "content": [{"type": "text", "text": f"未知工具: {name}"}],
                    "isError": True,
                }
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps(result, ensure_ascii=False, indent=2),
                }],
                "structuredContent": result,
                "isError": bool(result.get("error")),
            }
        except (TypeError, ValueError) as exc:
            return {
                "content": [{"type": "text", "text": f"参数错误: {exc}"}],
                "isError": True,
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
                "serverInfo": {"name": "dev-kb", "version": "0.1.0"},
                "instructions": "只读研发知识库。search 项目不明确时先选择候选，read_source 用于最终证据复核。",
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
    parser = argparse.ArgumentParser(description="dev-kb 只读 MCP stdio 服务")
    parser.add_argument("--config", default=str(default_projects_config()))
    parser.add_argument("--db", default=str(ROOT / "data/meta.db"))
    args = parser.parse_args()
    server = McpServer(KnowledgeBaseService(args.config, args.db))
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
            error = {
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32700, "message": f"Parse error: {exc}"},
            }
            sys.stdout.write(json.dumps(error, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
