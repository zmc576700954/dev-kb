"""agent_app 作为 MCP client 调用 kb_spec 知识库规范服务。

让内部 agent 也能用同一套规范校验与查重能力——避免同一套逻辑写两遍。
"""

from __future__ import annotations

import sys
from pathlib import Path

from .mcp_client import MCPServer, MCPError

ROOT = Path(__file__).resolve().parents[3]


class KbSpecClient:
    """kb_spec MCP server 的客户端封装（按需启动/关闭进程）。"""

    def __init__(self, config: str | Path | None = None,
                 db: str | Path | None = None, python: str | None = None):
        self.config = str(config or ROOT / "kb_projects.yaml")
        self.db = str(db or ROOT / "data/meta.db")
        self.python = python or sys.executable
        self._server: MCPServer | None = None

    # ---- 生命周期 ----

    def __enter__(self) -> "KbSpecClient":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def start(self) -> None:
        if self._server is not None:
            return
        self._server = MCPServer(
            [self.python, "-m", "agent_app.kb_spec.mcp_server",
             "--config", self.config, "--db", self.db],
            env={"PYTHONPATH": str(ROOT / "src")},
            cwd=str(ROOT),
            server_name="kb-spec")
        self._server.start()
        self._server.initialize()

    def close(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None

    @property
    def available(self) -> bool:
        """kb_spec server 是否可用（不可用时调用方应降级，不报错）。"""
        try:
            self.start()
            return True
        except (MCPError, OSError):
            self.close()
            return False

    # ---- 工具 ----

    def _call(self, tool: str, **args) -> dict:
        self.start()
        return self._server.call_tool(tool, args)

    def projects(self) -> list[dict]:
        return self._call("kb_projects").get("projects", [])

    def validate_project(self, project: str) -> dict:
        return self._call("kb_validate_project", project=project)

    def validate_document(self, project: str, path: str | None = None,
                          content: str | None = None) -> dict:
        return self._call("kb_validate_document", project=project,
                           path=path, content=content)

    def search(self, project: str, query: str, limit: int = 5) -> dict:
        return self._call("kb_search", project=project, query=query, limit=limit)

    def search_guided(self, query: str, project: str | None = None,
                      limit: int = 5) -> dict:
        """自然语言查询：意图识别 + 关键词提取 + 检索建议。"""
        return self._call("kb_search_guided", query=query, project=project, limit=limit)

    def draft_document(self, project: str, title: str, topic_keywords: str = "",
                       doc_type: str | None = None, category: str | None = None) -> dict:
        """生成合规文档骨架。"""
        return self._call("kb_draft_document", project=project, title=title,
                           topic_keywords=topic_keywords, doc_type=doc_type,
                           category=category)

    def suggest_fixes(self, findings: list[dict]) -> dict:
        return self._call("kb_suggest_fixes", findings=findings)

    def get_prompt(self, name: str, **arguments) -> str:
        """取 prompt 模板文本（写作引导、校验解读等）。"""
        self.start()
        result = self._server.get_prompt(name, arguments)
        messages = result.get("messages", [])
        if messages:
            return messages[0].get("content", {}).get("text", "")
        return ""

    # ---- 降级路径（server 不可用时的本地等价物） ----

    def local_search(self, project: str, query: str, limit: int = 5) -> dict:
        """不依赖 MCP 进程的本地查重（直接用 kb_spec 服务层）。"""
        from ..kb_spec.service import KBSpecService
        svc = KBSpecService()
        return svc.kb_search(query, project=project, limit=limit)
