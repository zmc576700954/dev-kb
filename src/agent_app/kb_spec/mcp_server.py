"""知识库规范 MCP stdio 服务（决策 Q1：独立于只读 dev-kb MCP）。

写路径一律产出提案（决策 Q8b）：pending → 人工 kb_cli approve → apply。
启动：PYTHONPATH=src .venv/bin/python -m agent_app.kb_spec.mcp_server
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .service import KBSpecError, KBSpecService
from .prompts import PROMPTS, kb_write_guide, kb_review_guide, kb_fix_guide, kb_migrate_guide, kb_style_guide, kb_onboard_guide

ROOT = Path(__file__).resolve().parents[3]
PROTOCOL_VERSIONS = {"2024-11-05", "2025-03-26", "2025-06-18"}
DEFAULT_PROTOCOL_VERSION = "2025-06-18"

_STR = {"type": "string"}

TOOL_DEFINITIONS = [
    {"name": "kb_projects", "description": "列出登记的知识库规范项目与概况。",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "kb_get_document",
     "description": "读取一篇文档的解析视图：frontmatter、大纲、出链、入链、内容 sha。",
     "inputSchema": {"type": "object",
                     "properties": {"project": _STR, "path": _STR},
                     "required": ["project", "path"], "additionalProperties": False}},
    {"name": "kb_search",
     "description": "查重检索（作业流程第 2 步）：必须显式指定 project，不做隐式跨项目。",
     "inputSchema": {"type": "object",
                     "properties": {"query": _STR, "project": _STR,
                                    "limit": {"type": "integer", "minimum": 1, "maximum": 10,
                                              "default": 5}},
                     "required": ["query"], "additionalProperties": False}},
    {"name": "kb_validate_document",
     "description": "校验单篇文档（可对未落盘的 content 做预检），返回按规则 ID 分级的 findings。",
     "inputSchema": {"type": "object",
                     "properties": {"project": _STR, "path": _STR, "content": _STR},
                     "required": ["project"], "additionalProperties": False}},
    {"name": "kb_validate_project",
     "description": "全库校验：结构、README 覆盖、断链、孤儿文档、命名与每篇文档规则。",
     "inputSchema": {"type": "object", "properties": {"project": _STR},
                     "required": ["project"], "additionalProperties": False}},
    {"name": "kb_list_proposals", "description": "列出提案（默认 pending）。",
     "inputSchema": {"type": "object",
                     "properties": {"project": _STR,
                                    "status": {"type": "string",
                                               "enum": ["pending", "approved", "rejected",
                                                        "applied"]}},
                     "additionalProperties": False}},
    {"name": "kb_show_proposal", "description": "查看提案详情：文件操作列表与完整目标内容。",
     "inputSchema": {"type": "object", "properties": {"proposal_id": {"type": "integer"}},
                     "required": ["proposal_id"], "additionalProperties": False}},
    {"name": "kb_propose_create",
     "description": "提案新建文档：自动校验、补齐 frontmatter 缺省键并同步 README；"
                    "error 级违规直接拒绝。dry_run=true 只返回计划。",
     "inputSchema": {"type": "object",
                     "properties": {"project": _STR, "path": _STR, "content": _STR,
                                    "dry_run": {"type": "boolean", "default": False}},
                     "required": ["project", "path", "content"],
                     "additionalProperties": False}},
    {"name": "kb_propose_update",
     "description": "提案更新文档：乐观并发（expected_sha）；状态变更自动同步最后更新；"
                    "标题/摘要变化自动同步 README。",
     "inputSchema": {"type": "object",
                     "properties": {"project": _STR, "path": _STR, "content": _STR,
                                    "expected_sha": _STR,
                                    "dry_run": {"type": "boolean", "default": False}},
                     "required": ["project", "path", "content"],
                     "additionalProperties": False}},
    {"name": "kb_propose_move",
     "description": "提案移动/改名：自动改写 README 与全库入链，断链保持为 0。",
     "inputSchema": {"type": "object",
                     "properties": {"project": _STR, "old_path": _STR, "new_path": _STR,
                                    "dry_run": {"type": "boolean", "default": False}},
                     "required": ["project", "old_path", "new_path"],
                     "additionalProperties": False}},
    {"name": "kb_propose_deprecate",
     "description": "提案废弃：插入文首废弃声明、改状态、README 标注；replacement 为替代文档路径。",
     "inputSchema": {"type": "object",
                     "properties": {"project": _STR, "path": _STR, "reason": _STR,
                                    "replacement": _STR,
                                    "dry_run": {"type": "boolean", "default": False}},
                     "required": ["project", "path", "reason"], "additionalProperties": False}},
    {"name": "kb_propose_archive",
     "description": "提案归档：移入 archive/、状态改已归档、README 单列归档分组。",
     "inputSchema": {"type": "object",
                     "properties": {"project": _STR, "path": _STR,
                                    "dry_run": {"type": "boolean", "default": False}},
                     "required": ["project", "path"], "additionalProperties": False}},
    {"name": "kb_propose_delete",
     "description": "提案删除：reason 必填（禁止静默删除）；自动移除 README 条目与全部入链。",
     "inputSchema": {"type": "object",
                     "properties": {"project": _STR, "path": _STR, "reason": _STR,
                                    "dry_run": {"type": "boolean", "default": False}},
                     "required": ["project", "path", "reason"], "additionalProperties": False}},
    {"name": "kb_apply_proposal",
     "description": "应用已获人工批准（kb_cli proposals approve）的提案；apply 后自动 re-sync 索引。",
     "inputSchema": {"type": "object", "properties": {"proposal_id": {"type": "integer"}},
                     "required": ["proposal_id"], "additionalProperties": False}},
    {"name": "kb_search_guided",
     "description": "自然语言查询引导：意图识别、关键词提取、结构化检索建议（写作/查询/修复/校验/归档）。",
     "inputSchema": {"type": "object",
                     "properties": {"query": _STR, "project": _STR,
                                    "limit": {"type": "integer", "minimum": 1, "maximum": 10,
                                              "default": 5}},
                     "required": ["query"], "additionalProperties": False}},
    {"name": "kb_draft_document",
     "description": "生成合规文档骨架：自动 frontmatter、五类骨架模板（技术/指南/手册/排查/默认）、规范内嵌。",
     "inputSchema": {"type": "object",
                     "properties": {"project": _STR, "title": _STR,
                                    "topic_keywords": _STR, "doc_type": _STR, "category": _STR},
                     "required": ["project", "title"], "additionalProperties": False}},
    {"name": "kb_suggest_fixes",
     "description": "根据校验 findings 生成结构化修复建议（修复步骤 + before/after 示例）。",
     "inputSchema": {"type": "object",
                     "properties": {"findings": {"type": "array", "items": {"type": "object"}}},
                     "required": ["findings"], "additionalProperties": False}},
    {"name": "kb_batch_fix",
     "description": "批量修复指定规则的全库违规：add_frontmatter/add_language_tag/add_related_section。",
     "inputSchema": {"type": "object",
                     "properties": {"project": _STR, "rule_id": _STR, "fix_strategy": _STR},
                     "required": ["project", "rule_id"], "additionalProperties": False}},
]

# MCP 工具名 → service 方法名（显式映射，避免前缀猜测出错）
TOOL_METHODS = {
    "kb_projects": "kb_projects",
    "kb_get_document": "kb_get_document",
    "kb_search": "kb_search",
    "kb_search_guided": "kb_search_guided",
    "kb_validate_document": "kb_validate_document",
    "kb_validate_project": "kb_validate_project",
    "kb_draft_document": "kb_draft_document",
    "kb_suggest_fixes": "kb_suggest_fixes",
    "kb_batch_fix": "kb_batch_fix",
    "kb_list_proposals": "list_proposals",
    "kb_show_proposal": "show_proposal",
    "kb_propose_create": "propose_create",
    "kb_propose_update": "propose_update",
    "kb_propose_move": "propose_move",
    "kb_propose_deprecate": "propose_deprecate",
    "kb_propose_archive": "propose_archive",
    "kb_propose_delete": "propose_delete",
    "kb_apply_proposal": "apply_proposal",
}


class McpServer:
    def __init__(self, service: KBSpecService):
        self.service = service

    def _tool_call(self, arguments: dict) -> dict:
        name = arguments.get("name")
        params = dict(arguments.get("arguments") or {})
        method_name = TOOL_METHODS.get(name or "")
        if method_name is None:
            return {"content": [{"type": "text", "text": f"未知工具: {name}"}],
                    "isError": True}
        try:
            result = getattr(self.service, method_name)(**params)
        except KBSpecError as exc:
            result = {"status": "error", "message": str(exc)}
        except (TypeError, ValueError) as exc:
            return {"content": [{"type": "text", "text": f"参数错误: {exc}"}],
                    "isError": True}
        except Exception as exc:
            # 任何未预期异常都不得让 server 进程退出（否则客户端看到 Connection closed）
            import traceback
            result = {"status": "error", "message": f"{type(exc).__name__}: {exc}",
                      "traceback": traceback.format_exc()[-1200:]}
        return {
            "content": [{"type": "text",
                         "text": json.dumps(result, ensure_ascii=False, indent=2)}],
            "structuredContent": result,
            "isError": result.get("status") in ("error", "rejected", "stale",
                                                "not_approved", "invalid_query",
                                                "unknown_project"),
        }

    def _prompt_get(self, params: dict) -> dict:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name not in PROMPTS:
            return {"isError": True, "content": [{"type": "text", "text": f"未知 prompt: {name}"}]}
        try:
            if name == "kb_write_guide":
                text = kb_write_guide(**arguments)
            elif name == "kb_review_guide":
                text = kb_review_guide(**arguments)
            elif name == "kb_fix_guide":
                text = kb_fix_guide(**arguments)
            elif name == "kb_migrate_guide":
                text = kb_migrate_guide(**arguments)
            elif name == "kb_style_guide":
                text = kb_style_guide(**arguments)
            elif name == "kb_onboard_guide":
                text = kb_onboard_guide(**arguments)
            else:
                text = ""
            return {
                "description": PROMPTS[name]["description"],
                "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
            }
        except Exception as exc:
            return {"isError": True, "content": [{"type": "text", "text": str(exc)}]}

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
                "capabilities": {"tools": {"listChanged": False},
                                 "prompts": {"listChanged": False}},
                "serverInfo": {"name": "dev-kb-kb-spec", "version": "0.2.0"},
                "instructions": (
                    "知识库写作与管理规范的机械化执行。先 kb_validate_project / "
                    "kb_validate_document 体检，再 kb_search_guided 查重，"
                    "用 kb_draft_document 生成骨架，最后 kb_propose_* 提交变更；"
                    "写路径一律产出提案，人工 kb_cli approve 后 kb_apply_proposal 落盘。"
                    "prompts/list 提供写作、校验、修复、迁移、风格、接入六个场景的引导模板。"
                ),
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOL_DEFINITIONS}
        elif method == "tools/call":
            result = self._tool_call(request.get("params") or {})
        elif method == "prompts/list":
            result = {"prompts": list(PROMPTS.values())}
        elif method == "prompts/get":
            result = self._prompt_get(request.get("params") or {})
        elif method == "shutdown":
            result = None
        else:
            return {"jsonrpc": "2.0", "id": request_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"}}
        return {"jsonrpc": "2.0", "id": request_id, "result": result}


def main() -> int:
    parser = argparse.ArgumentParser(description="知识库规范 MCP stdio 服务")
    parser.add_argument("--config", default=str(ROOT / "kb_projects.yaml"))
    parser.add_argument("--db", default=str(ROOT / "data/meta.db"))
    args = parser.parse_args()
    server = McpServer(KBSpecService(args.config, args.db))
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
