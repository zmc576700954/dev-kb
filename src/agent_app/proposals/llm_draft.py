"""LLM 文档更新草案：基于 git diff 证据生成实质性文档更新内容。

与模板草案（只记录"哪些文件变了"）不同，这里让 LLM 读变更内容与文档现状，
产出可直接落定的更新段落。仍走 提案→审批→写入 门禁，LLM 只生成草案。
"""

from __future__ import annotations

import re
import subprocess
from datetime import datetime
from pathlib import Path

DIFF_CAP_PER_FILE = 2500   # 单文件 diff 截断
DIFF_CAP_TOTAL = 9000      # 总 diff 预算
DOC_CAP = 4000             # 文档上下文预算
DRAFT_MAX_TOKENS = 1200

_SYSTEM = """你是技术文档维护助手。某文档引用的代码文件发生了变更。
根据 git diff 和文档相关内容，用中文写一段"变更同步"更新，追加到文档末尾：
1. 概述每个变更文件改了什么（从 diff 归纳，写行为层面的变化，不贴大段代码）；
2. 指出文档中可能因此过时的描述（如引用了具体行号或已改名/删除的符号）；
3. 若证据不足以判断，明确说"需人工核对"，不要编造。
输出为 Markdown，以 `## 变更同步（日期）` 开头，保持简洁专业。"""


def _git(root: str, *args: str) -> str:
    out = subprocess.run(["git", "-C", root, *args],
                         capture_output=True, text=True, timeout=30)
    return out.stdout if out.returncode == 0 else ""


def collect_diffs(root: str, files: list[str], commits: int = 5) -> dict[str, str]:
    """取每个变更文件最近几次提交的 diff（截断到预算内）。"""
    diffs: dict[str, str] = {}
    budget = DIFF_CAP_TOTAL
    for f in files:
        if budget <= 0:
            break
        d = _git(root, "log", f"-{commits}", "-p", "--", f)
        if not d.strip():
            d = _git(root, "diff", "HEAD", "--", f)  # 未提交变更
        d = d[: min(DIFF_CAP_PER_FILE, budget)]
        if d.strip():
            diffs[f] = d
            budget -= len(d)
    return diffs


def doc_context(root: str, doc_path: str, ref_lines: list[int]) -> str:
    """读取文档中引用附近的上下文（有行号取窗口，否则取开头）。"""
    full = Path(root) / doc_path
    if not full.is_file():
        return ""
    lines = full.read_text(encoding="utf-8").splitlines()
    if ref_lines:
        lo = max(0, min(ref_lines) - 15)
        hi = min(len(lines), max(ref_lines) + 15)
        return "\n".join(lines[lo:hi])[:DOC_CAP]
    return "\n".join(lines[:80])[:DOC_CAP]


def llm_draft(root: str, doc_path: str, evidence: list[dict]) -> str:
    """生成 LLM 更新草案；LLM 不可用时回退 None（调用方决定回退模板）。"""
    from ..llm import backend
    if not backend.available():
        return ""

    files = sorted({e["changed_file"] for e in evidence})
    ref_lines = [e["ref_line"] for e in evidence if e.get("ref_line")]
    diffs = collect_diffs(root, files)
    context = doc_context(root, doc_path, ref_lines)

    parts = [f"文档：{doc_path}", ""]
    if context:
        parts += ["## 文档相关内容", "```markdown", context, "```", ""]
    parts.append("## 变更证据")
    for e in evidence[:15]:
        loc = f"，文档第 {e['ref_line']} 行引用" if e.get("ref_line") else ""
        parts.append(f"- {e['changed_file']}（{e['how']}{loc}）")
    parts.append("")
    for f, d in diffs.items():
        parts += [f"## {f} 的 diff", "```diff", d, "```", ""]
    if not diffs:
        parts.append("（未取得 diff 内容，仅有变更文件清单）")

    today = datetime.now().strftime("%Y-%m-%d")
    out = backend.chat(_SYSTEM, "\n".join(parts), max_tokens=DRAFT_MAX_TOKENS, temperature=0.2)
    out = out.strip()
    if not out:
        return ""
    # 统一开头标记，便于人工识别与后续清理
    if not re.match(r"^## 变更同步", out):
        out = f"## 变更同步（{today} · LLM 草案，人工审批后写入）\n\n" + out
    out += kb_check(root, doc_path, out)
    return "\n\n---\n\n" + out + "\n"


def kb_check(root: str, doc_path: str, draft: str) -> str:
    """若该项目受知识库规范管辖，对草案做规范校验并把结果附在末尾。

    通过 MCP client 调 kb_spec——内部 agent 与外部 agent 用同一套校验逻辑，
    不重复实现。server 不可用时静默跳过（不让校验故障阻塞提案生成）。
    """
    try:
        from pathlib import Path
        from ..kb_spec.registry import kb_project_root, load_kb_registry
        registry = load_kb_registry()
        root_r = Path(root).resolve()
        project = next((n for n in registry
                        if kb_project_root(n)
                        and Path(kb_project_root(n)).resolve() == root_r), None)
        if not project:
            return ""
        from ..tools.kb_client import KbSpecClient
        with KbSpecClient() as kb:
            result = kb.validate_document(project, path=doc_path, content=draft)
        s = result.get("summary", {})
        if not s.get("errors") and not s.get("warnings") and not s.get("reviews"):
            return ""
        lines = ["", "### 知识库规范校验", ""]
        if s.get("errors"):
            lines.append(f"**{s['errors']} 处必须修复**（规范硬性要求，修复前不得交付）：")
            lines.append("")
            for f in result["findings"]:
                if f["severity"] != "error":
                    continue
                lines.append(f"- `{f['rule_id']}` {f.get('location', '')} — {f['message']}")
                if f.get("fix_hint"):
                    lines.append(f"  - 建议：{f['fix_hint']}")
        if s.get("warnings"):
            lines.append("")
            lines.append(f"另有 {s['warnings']} 处建议优化、{s.get('reviews', 0)} 处需人工判断。")
        lines.append("")
        return "\n".join(lines)
    except Exception:
        return ""      # 校验不可用不阻塞草案生成
