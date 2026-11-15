"""kb_spec 服务编排层：快照构建、校验、提案规划与落盘、查重检索。

CLI（kb_cli）与 MCP（kb_spec.mcp_server）共用本模块，保证两条入口行为一致。
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import date
from pathlib import Path

from . import rules
from .checks_doc import DocContext, check_document
from .checks_project import ProjectContext, check_project
from .docmodel import parse_document
from .operations import (ProjectSnapshot, plan_archive, plan_create, plan_delete,
                         plan_deprecate, plan_move, plan_update, _summary_of)
from .proposals import KBProposalStore
from .registry import kb_project_root, load_kb_registry
from ..config import default_kb_projects_config
from .vocab import Finding, SEVERITY_ERROR, SEVERITY_REVIEW, findings_to_dicts, summarize
from .docmodel import count_words
from .prompts import PROMPTS

ROOT = Path(__file__).resolve().parents[3]
SPEC_PATH = Path(__file__).resolve().parent / "spec" / "kb-agent-spec.md"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class KBSpecError(Exception):
    """可预期的服务级错误（未知项目、越界路径等），message 面向 agent。"""


class KBSpecService:
    # 非知识库内容目录：构建产物与缓存，不参与规范校验
    NON_CONTENT_DIRS = {"__pycache__", "node_modules", "vendor", "dist", "build",
                       ".venv", "venv", "target", "out"}

    def __init__(self, projects_config: str | Path | None = None,
                 db_path: str | Path | None = None, store=None):
        self.registry_path = projects_config or default_kb_projects_config()
        if store is not None:
            self.store = store
        else:
            from ..indexing.metadata_store import MetadataStore
            self.store = MetadataStore(db_path or ROOT / "data/meta.db")
        self.proposals = KBProposalStore(self.store.conn)
        self._engine = None

    # ---- 注册表与快照 ----

    def registry(self) -> dict[str, dict]:
        return load_kb_registry(self.registry_path)

    def require_root(self, project: str) -> Path:
        root = kb_project_root(project, self.registry_path)
        if root is None:
            raise KBSpecError(
                f"未登记的知识库项目：{project}；可用项目：{sorted(self.registry())}")
        path = Path(root)
        if not path.is_dir():
            raise KBSpecError(f"项目目录不存在：{root}")
        return path

    def build_snapshot(self, project: str) -> tuple[Path, ProjectSnapshot]:
        root = self.require_root(project)
        snap = ProjectSnapshot()
        scope = set(self.kb_paths(project))   # 空集 = 不限制范围
        snap.scope = scope
        for dirpath, dirnames, filenames in os.walk(root):
            rel_dir = Path(dirpath).relative_to(root).as_posix()
            rel_dir = "" if rel_dir == "." else rel_dir
            depth = 0 if rel_dir == "" else rel_dir.count("/") + 1
            # 隐藏目录、`_` 内部目录不参与正式内容（规范 §4.1）
            dirnames[:] = sorted(d for d in dirnames
                                 if not d.startswith((".", "~"))
                                 and not (depth == 0 and d.startswith("_"))
                                 and d not in self.NON_CONTENT_DIRS)
            # 范围限定：只把范围内的目录纳入「文档集合」，但全部文件仍进存在集合
            # ——断链检查必须基于真实文件系统，否则 README 指向范围外文档会被误判断链
            in_scope_dir = (not scope) or depth == 0 or rel_dir.split("/")[0] in scope
            if in_scope_dir and rel_dir:
                snap.dirs.add(rel_dir)
            for fn in filenames:
                if fn.startswith((".", "~")):
                    continue
                rel = f"{rel_dir}/{fn}" if rel_dir else fn
                snap.all_paths.add(rel)
                # 参与校验的文档：范围内的 .md + 根目录 README（唯一入口必须合规）
                # 根目录其他文件（如 CHANGELOG.md）只在存在集合里，可被链接但不校验
                # 未配置 scope 时，项目内所有 .md 都参与（规范会报出根目录多 md 的违规）
                if not scope:
                    participates = True
                elif depth == 0:
                    participates = fn == "README.md"
                else:
                    participates = rel_dir.split("/")[0] in scope
                if fn.endswith(".md") and participates:
                    try:
                        snap.files[rel] = (Path(dirpath) / fn).read_text(encoding="utf-8")
                    except UnicodeDecodeError:
                        # 无法按 UTF-8 解码 = 必须级缺陷（规范 §0.3），以占位内容进入校验
                        snap.files[rel] = "﻿<<<KB-0.3-UNDECODABLE>>>"
        return root, snap

    def kb_paths(self, project: str) -> list[str]:
        """知识库内容范围（kb_projects.yaml 的 kb_paths；空=不限制）。"""
        from .registry import kb_paths as _paths
        return _paths(project, self.registry_path)

    def _resolve_in_project(self, project: str, rel: str) -> tuple[Path, str]:
        root, _ = self.build_snapshot(project)
        p = (root / rel).resolve()
        root_r = root.resolve()
        if not (p == root_r or root_r in p.parents):
            raise KBSpecError(f"路径越出项目根：{rel}")
        return root, p.relative_to(root_r).as_posix()

    # ---- 读工具 ----

    def kb_projects(self) -> dict:
        registry = self.registry()
        out = []
        for name, meta in sorted(registry.items()):
            try:
                _, snap = self.build_snapshot(name)
                cats = [d for d in snap.dirs if "/" not in d and not d.startswith(("_", ".", "~"))
                        and d not in ("assets", "archive")]
                out.append({
                    "name": name, "path": meta["path"],
                    "documents": len([p for p in snap.files if p != "README.md"]),
                    "categories": len(cats),
                    "kb_paths": sorted(self.kb_paths(name)),
                })
            except KBSpecError as exc:
                out.append({"name": name, "path": meta["path"], "error": str(exc)})
        return {"status": "ok", "projects": out, "spec": self.spec_info()}

    def kb_get_document(self, project: str, path: str) -> dict:
        root, rel = self._resolve_in_project(project, path)
        full = root / rel
        if not full.is_file():
            return {"status": "not_found", "project": project, "path": rel}
        text = full.read_text(encoding="utf-8")
        doc = parse_document(rel, text)
        inlinks = []
        from .checks_doc import _resolve, _dir_of
        for other_rel, other_text in self._all_doc_texts(project).items():
            if other_rel == rel:
                continue
            other = parse_document(other_rel, other_text)
            for lk in other.links:
                if lk.kind == "doc" and lk.target.startswith(("./", "../")):
                    if _resolve(_dir_of(other), lk.target) == rel:
                        inlinks.append({"from": other_rel, "line": lk.line,
                                        "text": lk.text})
        heading_lines = {h.line for h in doc.headings}
        words = count_words("\n".join(ln for i, ln in doc.prose_lines()
                                      if i not in heading_lines))
        return {
            "status": "ok",
            "project": project,
            "path": rel,
            "content_sha": _sha256_text(text),
            "frontmatter": doc.fm,
            "status_base": doc.status_base(),
            "title": doc.title,
            "summary": _summary_of(doc),
            "words": words,
            "outline": [{"level": h.level, "text": h.text, "line": h.line}
                        for h in doc.headings],
            "links": [{"text": lk.text, "target": lk.target, "kind": lk.kind,
                       "line": lk.line, "in_related": lk.in_related} for lk in doc.links],
            "inlinks": inlinks,
            "is_readme": doc.is_readme,
        }

    def _all_doc_texts(self, project: str) -> dict[str, str]:
        _, snap = self.build_snapshot(project)
        return snap.files

    def kb_search(self, query: str, project: str | None = None,
                  limit: int = 5) -> dict:
        """查重检索（决策 Q16：复用现有索引，FTS 通道，不隐式跨项目）。"""
        if not query.strip():
            return {"status": "invalid_query", "message": "query 不能为空"}
        registry = self.registry()
        if project is None:
            return {"status": "project_required", "candidates": sorted(registry),
                    "message": "必须显式提供 project（规范 MCP 不做隐式跨项目检索）"}
        if project not in registry:
            return {"status": "unknown_project", "candidates": sorted(registry)}
        limit = max(1, min(int(limit), 10))
        engine = self._search_engine()
        results = engine.search(query, project=project, limit=limit)
        _, snap = self.build_snapshot(project)
        md_hits = []
        for item in results:
            path = item["path"]
            entry = {
                "path": path, "score": item["score"], "matched_by": item.get("matched_by"),
                "is_kb_document": path in snap.files,
                "snippet": item["content"][:200],
            }
            md_hits.append(entry)
        return {"status": "ok", "project": project, "query": query, "results": md_hits}

    def kb_search_guided(self, query: str, project: str | None = None,
                         limit: int = 5) -> dict:
        """自然语言查询引导：意图识别 + 关键词提取 + 结构化检索建议。"""
        if not query.strip():
            return {"status": "invalid_query", "message": "query 不能为空"}
        # 意图识别（关键词规则，Q6）：按优先级排序，避免「查」抢在「检查」前面
        intent_rules = [
            ("写作", ["写", "新建", "创建", "起草", "补充", "增加", "整理"]),
            ("校验", ["检查", "校验", "合规", "规范", "审查"]),
            ("修复", ["修复", "改正", "调整", "优化", "重构", "迁移"]),
            ("归档", ["归档", "废弃", "删除", "清理", "下线"]),
            ("查询", ["找", "查", "搜索", "在哪", "什么是", "如何", "怎么"]),
        ]
        intent = "查询"  # 默认
        for name, keywords in intent_rules:
            if any(kw in query for kw in keywords):
                intent = name
                break
        # 关键词提取：CJK bigram + 拉丁词元
        import re
        cjk = re.findall(r"[一-鿿]{2,}", query)
        latin = re.findall(r"[A-Za-z][A-Za-z0-9]+", query)
        keywords = list(dict.fromkeys(cjk + latin))[:5]
        # 检索
        search_result = self.kb_search(" ".join(keywords) if keywords else query,
                                       project=project, limit=limit)
        suggested_action = {
            "写作": "查重后决定：已有同主题文档则更新，否则用 kb_draft_document 生成骨架",
            "查询": "浏览相关文档，用 kb_get_document 查看详细结构",
            "修复": "先 kb_validate_document 确认违规，再用 kb_suggest_fixes 获取修复步骤",
            "校验": "用 kb_validate_project 或 kb_validate_document 执行校验",
            "归档": "用 kb_propose_archive 或 kb_propose_deprecate 提交变更提案",
        }.get(intent, "根据检索结果决定下一步")
        return {
            "status": "ok",
            "intent": intent,
            "keywords": keywords,
            "original_query": query,
            "suggested_action": suggested_action,
            "search_results": search_result.get("results", []),
        }

    def kb_draft_document(self, project: str, title: str,
                          topic_keywords: str = "",
                          doc_type: str | None = None,
                          category: str | None = None) -> dict:
        """生成合规文档骨架（决策 Q3/Q7：五类骨架，自动 frontmatter）。"""
        from datetime import date
        today = date.today().isoformat()
        # 类型识别
        if doc_type is None:
            if any(k in title for k in ("技术", "架构", "实现", "原理")):
                doc_type = "技术文档"
            elif any(k in title for k in ("指南", "教程", "步骤", "如何")):
                doc_type = "操作指南"
            elif any(k in title for k in ("手册", "参考", "规范", "标准")):
                doc_type = "参考手册"
            elif any(k in title for k in ("排查", "调试", "错误", "问题")):
                doc_type = "问题排查"
            else:
                doc_type = "默认"
        # 骨架模板
        skeletons = {
            "技术文档": ["## 核心概念", "## 实现原理", "## 代码示例", "## 边界与限制"],
            "操作指南": ["## 前置条件", "## 操作步骤", "## 验证结果", "## 常见问题"],
            "参考手册": ["## 概述", "## 详细说明", "## 示例", "## 相关文档"],
            "问题排查": ["## 现象描述", "## 原因分析", "## 解决方案", "## 预防措施"],
            "默认": ["## 核心概念", "## 详细说明", "## 示例", "## 相关文档"],
        }
        sections = skeletons.get(doc_type, skeletons["默认"])
        # 生成内容
        lines = [
            "---",
            "状态: 草案",
            f"最后更新: {today}",
            "下次复核: 按需",
            "优先级: 中",
            f"标签: {topic_keywords}" if topic_keywords else "标签: 待补充",
            "---",
            "",
            f"# {title}",
            "",
            f"> {title}的核心概念、操作步骤与注意事项，帮助读者快速理解并应用。",
            "",
        ]
        for section in sections:
            if section == "## 相关文档":
                lines.extend([section, "", "- [待补充：同分类相关文档](./)", ""])
            else:
                lines.extend([section, "", "（待补充内容）", ""])
        content = "\n".join(lines)
        # 确定路径
        if category is None:
            _, snap = self.build_snapshot(project)
            cats = [d for d in snap.dirs if "/" not in d and not d.startswith(("_", ".", "~"))
                    and d not in ("assets", "archive")]
            category = cats[0] if cats else "01-默认分类"
        path = f"{category}/{title.replace(' ', '-')}.md"
        return {
            "status": "ok",
            "project": project,
            "path": path,
            "doc_type": doc_type,
            "content": content,
            "next_steps": [
                "1. 替换「待补充」占位符为实际内容",
                "2. 调整摘要使其包含核心检索关键词",
                "3. 补充相关文档链接（同分类前 3 篇）",
                "4. 用 kb_validate_document 预检",
                "5. 用 kb_propose_create 提交提案",
            ],
        }

    def kb_suggest_fixes(self, findings: list[dict]) -> dict:
        """根据 findings 生成结构化修复建议（决策 Q8）。"""
        suggestions = []
        for f in findings:
            rule_id = f.get("rule_id", "")
            suggestion = {
                "rule_id": rule_id,
                "severity": f.get("severity"),
                "location": f.get("location", ""),
                "message": f.get("message", ""),
                "steps": [],
                "example_before": "",
                "example_after": "",
            }
            # 按规则类型提供修复步骤
            if "frontmatter" in rule_id.lower() or rule_id.startswith("KB-3"):
                suggestion["steps"] = [
                    "在文件第一行插入 `---`",
                    "填写必填键：状态、最后更新、下次复核",
                    "以 `---` 收束",
                    "空一行后开始正文",
                ]
                suggestion["example_before"] = "# 文档标题"
                suggestion["example_after"] = "---\n状态: 草案\n最后更新: 2026-11-13\n下次复核: 按需\n---\n\n# 文档标题"
            elif "empty-section" in rule_id:
                suggestion["steps"] = [
                    "在标题下添加过渡正文（1–2 句话）",
                    "或删除该标题，提升子标题层级",
                ]
                suggestion["example_before"] = "## 标题\n\n### 子标题"
                suggestion["example_after"] = "## 标题\n\n本节说明……\n\n### 子标题"
            elif "language-tag" in rule_id:
                suggestion["steps"] = ["在 ``` 后添加语言标识（bash/python/json 等）"]
                suggestion["example_before"] = "```\ncode\n```"
                suggestion["example_after"] = "```bash\ncode\n```"
            elif "broken" in rule_id or "orphan" in rule_id:
                suggestion["steps"] = [
                    "检查链接目标是否存在",
                    "如目标已移动，更新链接路径",
                    "如目标已删除，移除链接或链接到替代文档",
                ]
            elif "related" in rule_id or rule_id.startswith("KB-7"):
                suggestion["steps"] = [
                    "在文末添加 `## 相关文档` 小节",
                    "以无序列表列出 3–8 条链接",
                    "每条格式：`- [描述](./路径.md)`",
                ]
            else:
                suggestion["steps"] = [f.get("fix_hint", "参考规范对应章节进行修复")]
            suggestions.append(suggestion)
        return {"status": "ok", "suggestions": suggestions}

    def kb_batch_fix(self, project: str, rule_id: str,
                     fix_strategy: str = "auto") -> dict:
        """批量修复指定规则的全库违规（决策 Q2）。

        fix_strategy:
        - add_frontmatter: 为缺 frontmatter 的文档补元数据块
        - add_language_tag: 为无语言标注的代码块补语言标识
        - add_related_section: 为缺相关文档段的文档补空段
        - auto: 根据 rule_id 自动选择策略
        """
        _, snap = self.build_snapshot(project)
        today = date.today()
        fixed = []
        skipped = []
        # 按策略批量修复
        if rule_id == "KB-3.1-doc-missing-fm" or fix_strategy == "add_frontmatter":
            for path, content in sorted(snap.files.items()):
                if path == "README.md" or content.startswith("---"):
                    continue
                new_content = f"""---
状态: 草案
最后更新: {today.isoformat()}
下次复核: 按需
---

{content}"""
                result = self.propose_update(project, path, new_content,
                                             snap.sha_of(path))
                if result["status"] == "proposal_created":
                    fixed.append({"path": path, "proposal_id": result["proposal_id"]})
                else:
                    skipped.append({"path": path, "reason": result.get("message", "unknown")})
        elif rule_id == "KB-10-language-tag" or fix_strategy == "add_language_tag":
            import re
            for path, content in sorted(snap.files.items()):
                if path == "README.md":
                    continue
                # 简单策略：为无语言的 ``` 块补 text（或根据内容推断）
                new_content = re.sub(
                    r"```\n",
                    lambda m: "```bash\n" if "python -m" in content or "pip install" in content else "```text\n",
                    content)
                if new_content != content:
                    result = self.propose_update(project, path, new_content,
                                                 snap.sha_of(path))
                    if result["status"] == "proposal_created":
                        fixed.append({"path": path, "proposal_id": result["proposal_id"]})
                    else:
                        skipped.append({"path": path, "reason": result.get("message", "unknown")})
        elif rule_id == "KB-7-missing" or fix_strategy == "add_related_section":
            for path, content in sorted(snap.files.items()):
                if path == "README.md" or "## 相关文档" in content:
                    continue
                new_content = content.rstrip("\n") + "\n\n## 相关文档\n\n- [待补充](./)\n"
                result = self.propose_update(project, path, new_content,
                                             snap.sha_of(path))
                if result["status"] == "proposal_created":
                    fixed.append({"path": path, "proposal_id": result["proposal_id"]})
                else:
                    skipped.append({"path": path, "reason": result.get("message", "unknown")})
        else:
            return {"status": "unsupported_rule", "rule_id": rule_id,
                    "supported": ["KB-3.1-doc-missing-fm", "KB-10-language-tag", "KB-7-missing"]}
        return {
            "status": "ok",
            "project": project,
            "rule_id": rule_id,
            "fixed_count": len(fixed),
            "skipped_count": len(skipped),
            "fixed": fixed,
            "skipped": skipped,
            "next": "人工审批每个提案（kb_cli proposals approve <id>），然后 apply",
        }

    def _search_engine(self):
        if self._engine is None:
            from ..tools.retrieval import HybridSearch
            # FTS 通道即可支撑查重；不加载向量/嵌入模型，保持 server 轻启动
            self._engine = HybridSearch(self.store, None, rerank=False)
        return self._engine

    # ---- 校验 ----

    def kb_validate_document(self, project: str, path: str | None = None,
                             content: str | None = None) -> dict:
        _, snap = self.build_snapshot(project)
        if content is not None:
            rel = path or "<adhoc>"
            doc = parse_document(rel, content)
            findings = check_document(doc, snap.doc_ctx())
        else:
            if path is None:
                raise KBSpecError("path 与 content 至少提供一个")
            root, rel = self._resolve_in_project(project, path)
            text = snap.files.get(rel)
            if text is None:
                return {"status": "not_found", "project": project, "path": rel}
            doc = parse_document(rel, text)
            findings = check_document(doc, snap.doc_ctx())
        s = summarize(findings)
        return {
            "status": "ok", "project": project, "target": doc.rel_path,
            "verdict": "fail" if s["errors"] else ("warn" if s["warnings"] else "pass"),
            "summary": s,
            "findings": findings_to_dicts(findings),
            "rule_catalog": len(rules.RULES),
        }

    def kb_validate_project(self, project: str) -> dict:
        _, snap = self.build_snapshot(project)
        findings: list[Finding] = []
        per_doc: dict[str, dict] = {}
        for rel, text in sorted(snap.files.items()):
            doc = parse_document(rel, text)
            doc_findings = check_document(doc, snap.doc_ctx())
            if doc_findings:
                per_doc[rel] = findings_to_dicts(doc_findings)
                findings.extend(doc_findings)
        proj_ctx = ProjectContext(
            docs={p: parse_document(p, t) for p, t in snap.files.items()},
            dirs=set(snap.dirs), file_paths=set(snap.all_paths), today=snap.today)
        proj_findings = check_project(proj_ctx)
        findings.extend(proj_findings)
        s = summarize(findings)
        return {
            "status": "ok", "project": project,
            "verdict": "fail" if s["errors"] else ("warn" if s["warnings"] or s["reviews"]
                                                   else "pass"),
            "summary": s, "documents": len(snap.files),
            "findings_by_document": per_doc,
            "findings": findings_to_dicts(findings),
            "undecodable_files": [p for p, t in snap.files.items()
                                  if "KB-0.3-UNDECODABLE" in t],
        }

    def spec_info(self) -> dict:
        import json as _json
        version_file = SPEC_PATH.parent / "SPEC_VERSION.json"
        info: dict = {"vendored": SPEC_PATH.exists()}
        if version_file.exists():
            meta = _json.loads(version_file.read_text(encoding="utf-8"))
            info["declared_sha256"] = meta.get("sha256")
            info["vendored_at"] = meta.get("vendored_at")
            if SPEC_PATH.exists():
                actual = hashlib.sha256(SPEC_PATH.read_bytes()).hexdigest()
                info["actual_sha256"] = actual
                info["matches"] = actual == meta.get("sha256")
        return info

    # ---- 提案 ----

    def _check_pending_note(self, project: str, path: str) -> list[Finding]:
        notes = []
        for p in self.proposals.pending_on_target(project, path):
            notes.append(Finding("KB-OP-conflict", SEVERITY_REVIEW,
                                 f"同一目标已有 {p.status} 提案 #{p.id}（{p.kind}），注意顺序",
                                 path, evidence={"proposal_id": p.id, "kind": p.kind}))
        return notes

    def _propose(self, plan, dry_run: bool = False) -> dict:
        out = plan.to_dict()
        if plan.rejected:
            out.update({"status": "rejected",
                        "summary": summarize(plan.reject_findings + plan.findings),
                        "reject_findings": findings_to_dicts(plan.reject_findings),
                        "findings": findings_to_dicts(plan.findings),
                        "message": "写入被拒绝：先消除全部 error 级违规再提交"})
            return out
        if dry_run:
            out.update({"status": "dry_run",
                        "summary": summarize(plan.findings),
                        "findings": findings_to_dicts(plan.findings),
                        "message": "dry-run：仅返回变更计划，未创建提案、未写盘"})
            return out
        pid = self.proposals.add_plan(plan)
        return {"status": "proposal_created", "proposal_id": pid,
                "project": plan.project, "kind": plan.kind, "target": plan.target,
                "summary": summarize(plan.findings),
                "findings": findings_to_dicts(plan.findings),
                "ops": [{"op": o.op, "path": o.path, "after": o.after,
                         "before_sha": o.before_sha} for o in plan.ops],
                "next": f"人工审批：python -m agent_app.kb_spec.cli proposals approve {pid}，"
                        f"然后 kb_apply_proposal 或 kb_cli apply {pid}"}

    def propose_create(self, project: str, path: str, content: str, dry_run: bool = False) -> dict:
        _, snap = self.build_snapshot(project)
        plan = plan_create(snap, project, path, content)
        plan.findings.extend(self._dedup_findings(project, plan.title, path))
        plan.findings.extend(self._check_pending_note(project, path))
        return self._propose(plan, dry_run)

    def propose_update(self, project: str, path: str, content: str,
                       expected_sha: str | None = None, dry_run: bool = False) -> dict:
        _, snap = self.build_snapshot(project)
        plan = plan_update(snap, project, path, content, expected_sha)
        plan.findings.extend(self._check_pending_note(project, path))
        return self._propose(plan, dry_run)

    def propose_move(self, project: str, old_path: str, new_path: str, dry_run: bool = False) -> dict:
        _, snap = self.build_snapshot(project)
        plan = plan_move(snap, project, old_path, new_path)
        plan.findings.extend(self._check_pending_note(project, old_path))
        return self._propose(plan, dry_run)

    def propose_deprecate(self, project: str, path: str, reason: str,
                          replacement: str | None = None, dry_run: bool = False) -> dict:
        _, snap = self.build_snapshot(project)
        plan = plan_deprecate(snap, project, path, reason, replacement)
        plan.findings.extend(self._check_pending_note(project, path))
        return self._propose(plan, dry_run)

    def propose_archive(self, project: str, path: str, dry_run: bool = False) -> dict:
        _, snap = self.build_snapshot(project)
        plan = plan_archive(snap, project, path)
        plan.findings.extend(self._check_pending_note(project, path))
        return self._propose(plan, dry_run)

    def propose_delete(self, project: str, path: str, reason: str, dry_run: bool = False) -> dict:
        _, snap = self.build_snapshot(project)
        plan = plan_delete(snap, project, path, reason)
        plan.findings.extend(self._check_pending_note(project, path))
        return self._propose(plan, dry_run)

    def _dedup_findings(self, project: str, title: str, exclude_path: str) -> list[Finding]:
        """作业流程第 2 步：创建前全库查重，候选作为 review 证据附上。"""
        if not title:
            return []
        try:
            results = self._search_engine().search(title, project=project, limit=5)
        except Exception:
            return []
        # 按路径去重（检索结果按块返回，同一文档可能命中多次）
        hits: list[str] = []
        for r in results:
            p = r["path"]
            if p == exclude_path or p in hits:
                continue
            hits.append(p)
            if len(hits) >= 3:
                break
        if not hits:
            return []
        return [Finding("KB-8-topic-overlap", SEVERITY_REVIEW,
                        "创建前查重命中以下既有文档，请确认主题不重复",
                        exclude_path, evidence={"candidates": hits},
                        fix_hint="已有同主题文档时应更新而非新建（§14 步骤 2）")]

    def list_proposals(self, project: str | None = None, status: str | None = None,
                       active_only: bool = False) -> dict:
        items = self.proposals.list(project, status, active_only)
        return {"status": "ok", "proposals": [p.to_dict(include_ops=True) for p in items]}

    def show_proposal(self, proposal_id: int) -> dict:
        p = self.proposals.get(proposal_id)
        if p is None:
            return {"status": "not_found", "proposal_id": proposal_id}
        out = p.to_dict(include_ops=True)
        # 附加项目根路径，供 CLI diff 视图读取当前文件内容
        root = kb_project_root(p.project, self.registry_path)
        if root:
            out["_root"] = root
        return {"status": "ok", "proposal": out}

    # ---- 落盘 ----

    def apply_proposal(self, proposal_id: int) -> dict:
        p = self.proposals.get(proposal_id)
        if p is None:
            return {"status": "not_found", "proposal_id": proposal_id}
        if p.status == "applied":
            return {"status": "already_applied", "proposal_id": proposal_id}
        if p.status != "approved":
            return {"status": "not_approved", "proposal_id": proposal_id,
                    "message": "提案未获批准：先由人工执行 kb_cli proposals approve"}
        root, snap = self.build_snapshot(p.project)
        # 前置条件复核：项目在审批期间发生漂移则拒绝执行
        problems = []
        effective_ops = []
        for op in p.ops:
            path = op["path"]
            current = snap.files.get(path)
            current_sha = snap.sha_of(path)
            if op["op"] == "write":
                if op["before_sha"] is None:
                    if path in snap.all_paths:
                        problems.append(f"`{path}` 不应存在但已存在")
                elif current_sha != op["before_sha"]:
                    # README 是聚合入口：多个跨文档提案各自基于同一份旧 README，
                    # 后者应用时必然不匹配——在「当前 README」上重放条目增量而非拒绝。
                    patch = op.get("patch")
                    if path == "README.md" and patch and current is not None:
                        from .operations import _replay_readme_patch
                        effective_ops.append({**op, "after": _replay_readme_patch(current, patch),
                                              "before_sha": current_sha})
                        continue
                    problems.append(f"`{path}` 内容与提案时不一致")
            elif op["op"] == "delete":
                if current is None:
                    problems.append(f"`{path}` 应存在但已不存在")
                elif current_sha != op.get("before_sha"):
                    problems.append(f"`{path}` 内容与提案时不一致")
            effective_ops.append(op)
        if problems:
            return {"status": "stale", "proposal_id": proposal_id,
                    "problems": problems,
                    "message": "项目状态与提案时不一致（可能由其他提案 apply 造成），"
                               "请重新读取并生成新提案"}
        for op in effective_ops:
            target = root / op["path"]
            if op["op"] == "write":
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(op["after"], encoding="utf-8")
            elif op["op"] == "delete":
                target.unlink(missing_ok=True)
        # 清理因删除而空掉的分类目录（§1 禁止空分类目录）
        removed_dirs = []
        for op in p.ops:
            if op["op"] != "delete" or "/" not in op["path"]:
                continue
            d = (root / op["path"]).parent
            rel_dir = d.relative_to(root).as_posix()
            if rel_dir != "archive" and d.is_dir() and not any(d.iterdir()):
                d.rmdir()
                removed_dirs.append(rel_dir)
        resync = self._resync(p.project, root)
        self.proposals.mark_applied(proposal_id, {
            "ops": len(p.ops), "resync": resync, "removed_dirs": removed_dirs})
        return {"status": "applied", "proposal_id": proposal_id,
                "ops_applied": len(p.ops), "removed_dirs": removed_dirs,
                "resync": resync}

    def _resync(self, project: str, root: Path) -> dict | None:
        """apply 后触发该项目索引 re-sync（FTS 通道；向量由下次 sync_cli 补齐）。"""
        try:
            from ..ingestion.sync import SyncEngine
            engine = SyncEngine(self.store, None, embed=False)
            registry = self.registry()
            extra = registry.get(project, {}).get("extra_exclude_dirs")
            return engine.sync_project(project, str(root), extra)
        except Exception as exc:   # 索引不可用不阻断落盘，但如实上报
            return {"error": str(exc)}
