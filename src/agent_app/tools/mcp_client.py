"""轻量 MCP stdio client：让 agent_app 能作为 MCP client 调用外部 MCP server。

手写而非引入官方 SDK——项目保持零新依赖（与 kb_spec.mcp_server 同样的理由）。

协议：JSON-RPC 2.0，每行一个 JSON；请求/响应以 id 匹配。
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path


class MCPError(RuntimeError):
    """MCP 调用失败（连接、超时、协议错误）。"""


class MCPServer:
    """一个 stdio MCP server 的连接（上下文管理器用法最自然）。"""

    def __init__(self, command: list[str], env: dict[str, str] | None = None,
                 cwd: str | Path | None = None, timeout: float = 60.0,
                 server_name: str = "mcp"):
        self.command = command
        self.env = env
        self.cwd = cwd
        self.timeout = timeout
        self.server_name = server_name
        self.proc: subprocess.Popen | None = None
        self._id = 0
        self._lock = threading.Lock()
        self._stderr_lines: list[str] = []

    # ---- 生命周期 ----

    def __enter__(self) -> "MCPServer":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def start(self) -> None:
        import os
        env = dict(os.environ)
        if self.env:
            env.update(self.env)
        try:
            self.proc = subprocess.Popen(
                self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, bufsize=1, env=env,
                cwd=str(self.cwd) if self.cwd else None)
        except OSError as exc:
            raise MCPError(f"无法启动 MCP server {self.command}: {exc}") from exc
        self._drain_stderr()

    def close(self) -> None:
        if self.proc is None:
            return
        try:
            if self.proc.stdin:
                self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "exit"}) + "\n")
                self.proc.stdin.flush()
        except (BrokenPipeError, ValueError):
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.proc = None

    def _drain_stderr(self) -> None:
        """后台读 stderr，避免管道写满导致 server 阻塞。"""
        def run():
            try:
                for line in self.proc.stderr:
                    self._stderr_lines.append(line.rstrip())
                    if len(self._stderr_lines) > 100:
                        self._stderr_lines.pop(0)
            except (ValueError, OSError):
                pass
        if self.proc and self.proc.stderr:
            threading.Thread(target=run, daemon=True).start()

    @property
    def stderr_tail(self) -> str:
        return "\n".join(self._stderr_lines[-10:])

    # ---- JSON-RPC ----

    def _send(self, payload: dict) -> None:
        if self.proc is None or self.proc.stdin is None:
            raise MCPError("MCP server 未启动")
        try:
            self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise MCPError(f"MCP 连接已断开: {exc}\n{self.stderr_tail}") from exc

    def _read_message(self, timeout: float) -> dict:
        """读一条 JSON-RPC 消息（跳过通知等无 id 的消息）。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc is None or self.proc.stdout is None:
                raise MCPError("MCP server 已退出")
            line = self.proc.stdout.readline()
            if not line:
                raise MCPError(f"MCP server 无输出（已退出？）\n{self.stderr_tail}")
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue      # 非 JSON 行（server 的日志），忽略
        raise MCPError(f"MCP 请求超时（{timeout}s）")

    def request(self, method: str, params: dict | None = None,
                timeout: float | None = None) -> dict:
        with self._lock:
            self._id += 1
            rid = self._id
            payload = {"jsonrpc": "2.0", "id": rid, "method": method}
            if params is not None:
                payload["params"] = params
            self._send(payload)
            while True:
                msg = self._read_message(timeout or self.timeout)
                if msg.get("id") != rid:
                    continue      # 别人的响应或通知
                if "error" in msg:
                    err = msg["error"]
                    raise MCPError(f"{method} 失败: {err.get('message', err)}")
                return msg.get("result", {})

    def notify(self, method: str, params: dict | None = None) -> None:
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._send(payload)

    # ---- MCP 语义 ----

    def initialize(self) -> dict:
        result = self.request("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "dev-kb-agent", "version": "0.1.0"},
        })
        self.notify("notifications/initialized")
        return result

    def list_tools(self) -> list[dict]:
        return self.request("tools/list").get("tools", [])

    def list_prompts(self) -> list[dict]:
        return self.request("prompts/list").get("prompts", [])

    def get_prompt(self, name: str, arguments: dict | None = None) -> dict:
        params = {"name": name}
        if arguments:
            params["arguments"] = arguments
        return self.request("prompts/get", params)

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        """调用工具，返回 structuredContent（缺失时回退到 text 内容）。"""
        result = self.request("tools/call", {"name": name, "arguments": arguments or {}})
        if "structuredContent" in result:
            return result["structuredContent"]
        for item in result.get("content", []):
            if item.get("type") == "text":
                try:
                    return json.loads(item["text"])
                except (json.JSONDecodeError, KeyError):
                    return item["text"]
        return result
