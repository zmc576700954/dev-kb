"""Spec 解析：设计文档状态行、任务文件复选框、前置依赖链接。

实测格式（tiangong / repository-manage / new-bargain-laravel 一致）：
- 设计文档 specs/YYYY-MM-DD-<name>-design.md：首行标题 + `> 状态：xxx`
- 任务文件 tasks/YYYY-MM-DD-<name>.md：元信息引用块 + `## 任务清单` 复选框
  `- [x] **D10b-1 标题**：……` 或 `- [x] T1: 标题`
- 前置依赖：元信息行中的 Markdown 链接，如
  `> 前置任务：[2026-07-30-query-writeback.md](...)`

canonical key：文件名去日期前缀、去 -design/-plan 后缀，用于三类文档配对。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}-")
_SUFFIX = re.compile(r"-(design|plan|spec)$")
_STATUS_LINE = re.compile(r"^>\s*状态[：:]\s*(.+?)\s*$", re.M)
_META_LINE = re.compile(r"^>\s*([\w一-鿿]+(?:任务|文档|计划|来源|分支|前置|依赖|范围)?)[：:]\s*(.+)$", re.M)
_MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_CHECKBOX = re.compile(r"^(\s*)- \[( |x|X)\]\s+(.*)$")
_BOLD = re.compile(r"^\*\*([^*]{1,40}?)\*\*[：:]?\s*(.*)$")
_NUM_ID = re.compile(r"^([A-Za-z]*\d+[a-z]?(?:-\d+)?)\s*[:：]\s*(.*)$")
_STEP_ID = re.compile(r"^(Step\s*\d+)\s*[:：.]\s*(.*)$", re.I)


def _task_id(text: str) -> tuple[str, str]:
    """从复选框文本提取任务 id 与标题。

    支持：`**D10b-1 标题**`、`**Step 4: Commit**`、`T1: 标题` 三种实测格式。
    """
    m = _BOLD.match(text)
    if m:
        bold, rest = m.group(1), m.group(2)
        s = _STEP_ID.match(bold)
        if s:
            return s.group(1), (s.group(2) + " " + rest).strip() if rest else s.group(2)
        # 粗体内首个 token 为 id 形态（含数字或全大写缩写），其余为标题
        head, _, tail = bold.partition(" ")
        if re.match(r"^[A-Za-z]*\d", head) or re.match(r"^[A-Z][\w.\-]*$", head):
            return head, (tail + " " + rest).strip() if tail or rest else bold
        return bold, rest.strip()
    m = _NUM_ID.match(text)
    if m:
        return m.group(1), m.group(2).strip()
    return "", text.strip()

# 状态行归一化
STATUS_MAP = {
    "待评审": "pending_review",
    "已确认": "confirmed",
    "待实施": "ready",
    "设计已确认，待实施": "ready",
    "实施中": "in_progress",
    "已完成": "done",
    "已合并": "done",
}


def canonical_key(stem: str) -> str:
    """2026-10-01-d10b-yjs-awareness → d10b-yjs-awareness；…-design → 去后缀；去路径。"""
    s = stem.replace("\\", "/").rsplit("/", 1)[-1]
    s = _DATE_PREFIX.sub("", s)
    return _SUFFIX.sub("", s)


@dataclass
class SpecTask:
    task_id: str
    title: str
    done: bool
    ordinal: int
    start_line: int
    end_line: int
    indent: int = 0


@dataclass
class ParsedSpec:
    kind: str                      # design | plan | task
    key: str                       # canonical key
    title: str = ""
    status: str = ""               # 归一化状态
    status_raw: str = ""
    created: str = ""
    branch: str = ""
    status_text: str = ""          # 「当前状态」段落摘要
    deps: list[str] = field(default_factory=list)        # canonical keys
    code_refs: list[tuple[str, int]] = field(default_factory=list)  # (path, line)
    tasks: list[SpecTask] = field(default_factory=list)


def _first_heading(text: str) -> str:
    m = re.search(r"^#\s+(.+)$", text, re.M)
    return m.group(1).strip() if m else ""


def _status_section(text: str) -> str:
    m = re.search(r"^##\s*当前状态\s*\n(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    if not m:
        return ""
    body = re.sub(r"\s+", " ", m.group(1)).strip()
    return body[:300]


def _code_refs(text: str) -> list[tuple[str, int]]:
    """提取 `path/to/file.ts:123` 形式的代码引用。"""
    refs = []
    for m in re.finditer(r"`((?:[\w.\-/]+/)*[\w.\-]+\.(?:ts|tsx|js|php|py|rs|vue|css|sql)):(\d+)(?:-\d+)?`", text):
        refs.append((m.group(1), int(m.group(2))))
    return refs


def parse_spec(text: str, rel_path: str) -> ParsedSpec | None:
    """解析单个 Spec 相关文件；不是 Spec 形态时返回 None。"""
    stem = rel_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    lower = rel_path.lower()
    if "/specs/" in lower or lower.startswith("specs/"):
        kind = "design" if stem.endswith("-design") else "design"
    elif "/tasks/" in lower:
        kind = "task"
    elif "/plans/" in lower:
        kind = "plan"
    else:
        return None

    spec = ParsedSpec(kind=kind, key=canonical_key(stem), title=_first_heading(text))
    if not spec.title:
        return None

    m = _STATUS_LINE.search(text)
    if m:
        spec.status_raw = m.group(1)
        spec.status = STATUS_MAP.get(spec.status_raw, spec.status_raw)

    # 元信息行：创建日期、分支、前置依赖（只有"前置/依赖"才算依赖；计划来源、设计文档不算）
    for key, val in _META_LINE.findall(text):
        if "创建" in key:
            spec.created = val.strip().strip("`")
        elif "分支" in key:
            spec.branch = val.strip().strip("`")
        elif any(k in key for k in ("前置", "依赖")):
            for label, url in _MD_LINK.findall(val):
                target = url if url.endswith(".md") else label
                if target.endswith(".md"):
                    spec.deps.append(canonical_key(target[:-3]))
    # 正文中指向 specs/tasks 的链接也作为关联（去重；排除 .claude 等工具目录与 plans 计划来源引用）
    for label, url in _MD_LINK.findall(text):
        if ".claude/" in url or "/plans/" in url:
            continue
        target = url if url.endswith(".md") else label
        if target.endswith(".md") and any(d in url for d in ("specs/", "tasks/")):
            k = canonical_key(target.rsplit("/", 1)[-1][:-3])
            if k != spec.key and k not in spec.deps:
                spec.deps.append(k)

    spec.status_text = _status_section(text)
    spec.code_refs = _code_refs(text)

    # 任务复选框
    lines = text.splitlines()
    ordinal = 0
    for i, line in enumerate(lines):
        m = _CHECKBOX.match(line)
        if not m:
            continue
        indent, mark, body = m.groups()
        if indent and len(indent) >= 2 and spec.tasks and spec.tasks[-1].indent == 0:
            continue  # 顶层任务的子项不单独计
        ordinal += 1
        tid, title = _task_id(body)
        spec.tasks.append(SpecTask(
            task_id=tid or f"item-{ordinal}", title=title, done=mark.lower() == "x",
            ordinal=ordinal, start_line=i + 1, end_line=i + 1,
            indent=1 if indent else 0,
        ))

    return spec
