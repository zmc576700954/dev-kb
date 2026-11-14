"""单篇文档校验（规范 §3–§13 中不依赖全库视角的规则）。

check_document(doc, ctx) -> list[Finding]
ctx 提供：路径存在性、文件大小、文件修改时间、当前日期（全部可注入以便测试）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .docmodel import ParsedDoc, count_words
from .vocab import Finding, SEVERITY_ERROR, SEVERITY_REVIEW, SEVERITY_WARNING

STATUS_VALUES = ("草案", "稳定", "需监控", "已废弃", "已归档")
PRIORITY_VALUES = ("高", "中", "低")
GENERIC_LINK_TEXTS = {"点击这里", "点击此处", "本文档", "更多", "详见", "这里"}
GENERIC_ALT_TEXTS = {"图片", "截图", "图", "配图", "照片", "image", "img", "screenshot", "fig"}
UNINFORMATIVE_WORDS = ("未命名", "新建文档", "副本", "copy", "untitled", "无标题")
VERSION_SUFFIX_RE = re.compile(r"-(v\d+|new|final|latest|最新版|最新|新版|最终版|copy|副本|bak|backup)$", re.IGNORECASE)
LOCATION_WORDS = ("教程", "指南", "详解", "汇总", "大全", "手册", "入门", "笔记", "攻略")
PLACEHOLDER_RE = re.compile(r"\bTODO\b|待补充|待完善")
PLACEHOLDER_LINES = {"todo", "待补充", "待完善", "xxx", "xx", "...", "…", "tbd"}
SUMMARY_HEADING_WORDS = {"总结", "小结", "结语", "结论"}
GENERIC_TITLE_EXACT = {"其他", "杂项", "临时", "测试", "未命名", "新建文档", "资料", "备注"}
SECRET_RE = re.compile(
    r"sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|(?i:api[_-]?key|secret|token|password|passwd)\s*[=:]\s*['\"]?[^\s'\"<>]{8,}"
)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CAPTION_RE = re.compile(r"^[>_\s*\-]*图\s*(\d+)\s*[：:]")
# 索引型文档：承载大量链接是设计意图，链接数上限放宽
INDEX_DOC_RE = re.compile(r"(索引|排序|路径|清单|导航|目录|一览|总览)")
DEPRECATION_PREFIX = "> ⚠️ 已废弃"
REPLACEMENT_RE = re.compile(r"替代文档见\s*\[([^\]]*)\]\(([^)]+)\)")


@dataclass
class DocContext:
    """路径存在性等环境查询；测试可注入假实现。"""

    path_exists: object            # Callable[[str], bool]（相对项目根，文件或目录）
    file_size: object = None       # Callable[[str], int] 可选
    mtime_date: object = None      # Callable[[str], date | None] 可选
    today: date = field(default_factory=date.today)


def _loc(doc: ParsedDoc, lineno: int) -> str:
    return f"{doc.rel_path}:{lineno}"


def _resolve(base_dir: str, target: str) -> str | None:
    """归一相对路径；越出项目根时返回 None。target 需已是 ./ ../ 开头的相对路径。"""
    parts = (base_dir + "/" + target).split("/") if base_dir else target.split("/")
    stack: list[str] = []
    for p in parts:
        if p in ("", "."):
            continue
        if p == "..":
            if not stack:
                return None
            stack.pop()
        else:
            stack.append(p)
    return "/".join(stack) or None


def _strip_md_style(text: str) -> str:
    return text.strip().strip("*_` ").strip()


def _valid_date(v: str) -> bool:
    if not DATE_RE.match(v):
        return False
    try:
        date.fromisoformat(v)
        return True
    except ValueError:
        return False


def _is_draft(doc: ParsedDoc) -> bool:
    return doc.status_base() == "草案"


# ---------------------------------------------------------------- §3 frontmatter

def _check_frontmatter(doc: ParsedDoc, ctx: DocContext, out: list[Finding]):
    if doc.is_readme:
        if doc.has_fm_block:
            out.append(Finding("KB-3.1-readme-frontmatter", SEVERITY_ERROR,
                               "README 禁止 frontmatter", f"{doc.rel_path}:1",
                               "删除 frontmatter 块，README 只做导航"))
        return
    if doc.fm is None:
        if not doc.has_fm_block:
            out.append(Finding("KB-3.1-doc-missing-fm", SEVERITY_ERROR,
                               "文档缺少 frontmatter（必须位于文件第一行）",
                               f"{doc.rel_path}:1",
                               "补 `状态: 草案`、`最后更新: <YYYY-MM-DD>`、`下次复核: <日期或按需>`"))
        return  # 块存在但非法：解析器已产出 KB-3.2-* findings
    fm = doc.fm
    for key in ("状态", "最后更新", "下次复核"):
        if key not in fm:
            rule = {"状态": "KB-3.3-missing-status", "最后更新": "KB-3.3-missing-last-updated",
                    "下次复核": "KB-3.3-missing-next-review"}[key]
            out.append(Finding(rule, SEVERITY_ERROR, f"缺必填键 `{key}`",
                               doc.rel_path, f"补 `{key}: <值>`"))
    if "状态" in fm:
        v = fm["状态"]
        base = v.split("（", 1)[0].strip()
        if base not in STATUS_VALUES:
            out.append(Finding("KB-3.5-status-value", SEVERITY_ERROR,
                               f"`状态` 取值非法：`{v}`（基准值：{'/'.join(STATUS_VALUES)}）",
                               doc.rel_path, "改为五个基准值之一；补充说明写在基准值后的全角括号内"))
        elif "（" in v:
            if not (v.endswith("）") and v.count("（") == 1 and v.count("）") == 1
                    and "(" not in v and ")" not in v):
                out.append(Finding("KB-3.5-status-value", SEVERITY_ERROR,
                                   f"`状态` 补充说明格式非法：`{v}`（须用全角括号包裹）",
                                   doc.rel_path, "如 `稳定（价格按季度复核）`"))
        elif "(" in v or ")" in v:
            out.append(Finding("KB-3.5-status-value", SEVERITY_ERROR,
                               f"`状态` 补充说明须用全角括号：`{v}`", doc.rel_path,
                               "半角括号改为全角（…）"))
    last_raw, next_raw = fm.get("最后更新"), fm.get("下次复核")
    last_d = next_d = None
    if last_raw is not None:
        if not _valid_date(last_raw):
            out.append(Finding("KB-3.5-date-format", SEVERITY_ERROR,
                               f"`最后更新` 不是 YYYY-MM-DD：`{last_raw}`", doc.rel_path))
        else:
            last_d = date.fromisoformat(last_raw)
    if next_raw is not None and next_raw != "按需":
        if not _valid_date(next_raw):
            out.append(Finding("KB-3.5-date-format", SEVERITY_ERROR,
                               f"`下次复核` 不是 YYYY-MM-DD 也不是「按需」：`{next_raw}`", doc.rel_path))
        else:
            next_d = date.fromisoformat(next_raw)
    if last_d and next_d and next_d < last_d:
        out.append(Finding("KB-3.5-review-order", SEVERITY_ERROR,
                           "`下次复核` 早于 `最后更新`", doc.rel_path,
                           "复核日期不得早于最后更新；无法确定时填「按需」"))
    if "优先级" in fm and fm["优先级"] not in PRIORITY_VALUES:
        out.append(Finding("KB-3.4-priority-value", SEVERITY_ERROR,
                           f"`优先级` 取值非法：`{fm['优先级']}`（高/中/低）", doc.rel_path))
    if "标签" in fm:
        tags = [t.strip() for t in fm["标签"].split(",") if t.strip()]
        bad = [t for t in tags if len(t) > 12 or t != t.lower()]
        if len(tags) > 5 or bad or fm["标签"] != fm["标签"].strip():
            out.append(Finding("KB-3.4-tag-format", SEVERITY_ERROR,
                               f"`标签` 格式非法：`{fm['标签']}`（逗号分隔小写词，每个≤12字符，总数≤5）",
                               doc.rel_path, "改为小写短词，逗号半角"))
    for key, value in fm.items():
        if len(value) >= 40:
            out.append(Finding("KB-3.2-prose-value", SEVERITY_REVIEW,
                               f"frontmatter 值疑似正文内容：`{key}` 的值有 {len(value)} 字",
                               doc.rel_path, "元数据保持一句话；长内容移入正文"))
    if ctx.mtime_date and last_d and doc.rel_path:
        mtime_d = ctx.mtime_date(doc.rel_path)
        if mtime_d and mtime_d > last_d:
            out.append(Finding("KB-3.5-update-semantics", SEVERITY_REVIEW,
                               "文件系统修改时间晚于 `最后更新`：确认这次改动是否属于实质修改",
                               doc.rel_path,
                               evidence={"最后更新": last_raw,
                                         "文件修改日期": mtime_d.isoformat()}))


# ---------------------------------------------------------------- §5.1 标题

def _check_headings(doc: ParsedDoc, out: list[Finding]):
    h1s = [h for h in doc.headings if h.level == 1]
    first_body = next(((i, ln) for i, ln in enumerate(doc.lines, 1)
                       if i >= doc.body_start and ln.strip()), None)
    if len(h1s) != 1:
        out.append(Finding("KB-5.1-one-h1", SEVERITY_ERROR,
                           f"一级标题数量为 {len(h1s)}，必须恰好 1 个", doc.rel_path))
    elif first_body is None or first_body[0] != h1s[0].line:
        out.append(Finding("KB-5.1-one-h1", SEVERITY_ERROR,
                           "一级标题必须位于 frontmatter 之后正文最前", doc.rel_path,
                           "把 H1 移到正文第一行"))
    prev_level = 0
    max_seen = 0
    warned_depth = False
    for idx, h in enumerate(doc.headings):
        if idx > 0 and h.level > prev_level + 1:
            out.append(Finding("KB-5.1-level-jump", SEVERITY_ERROR,
                               f"标题跳级：`{'#'*prev_level}` 之后直接出现 `{'#'*h.level} {h.text}`",
                               _loc(doc, h.line), "补中间层级或降为上一级"))
        prev_level = h.level
        if h.level > max_seen:
            max_seen = h.level
        if h.level > 4 and not warned_depth:
            warned_depth = True
            out.append(Finding("KB-5.1-max-depth", SEVERITY_WARNING,
                               "标题层级超过 4 级，文档应考虑拆分", _loc(doc, h.line)))
        if "](" in h.text or "![" in h.text or "`" in h.text:
            out.append(Finding("KB-5.1-heading-links", SEVERITY_ERROR,
                               f"标题含链接/图片/代码：`{h.text}`", _loc(doc, h.line)))
        if h.text.endswith(("。", ".")):
            out.append(Finding("KB-5.1-heading-punct", SEVERITY_ERROR,
                               f"标题以句号结尾：`{h.text}`", _loc(doc, h.line)))
        elif re.fullmatch(r"[\d.、\s\-vV]*", h.text or "") and not re.search(r"[一-鿿A-Za-z]", h.text):
            out.append(Finding("KB-5.1-heading-punct", SEVERITY_ERROR,
                               f"标题只有编号没有文字：`{h.text}`", _loc(doc, h.line)))
        if h.text.endswith(("？", "?")):
            out.append(Finding("KB-5.1-question-heading", SEVERITY_WARNING,
                               f"标题为疑问句：`{h.text}`", _loc(doc, h.line),
                               "改写为名词短语或任务短语"))
        if idx > 0:
            prev = doc.headings[idx - 1]
            if prev.level == h.level:
                between = doc.lines[prev.line:h.line - 1]
                if not any(ln.strip() for ln in between):
                    out.append(Finding("KB-5.1-adjacent-same-level", SEVERITY_ERROR,
                                       f"两个同级标题直接相邻：`{prev.text}` → `{h.text}`",
                                       _loc(doc, h.line), "同级标题之间必须有正文"))
    seen: dict[int, dict[str, int]] = {}
    for h in doc.headings:
        bucket = seen.setdefault(h.parent, {})
        if h.text in bucket:
            out.append(Finding("KB-5.1-dup-heading", SEVERITY_ERROR,
                               f"同一父级下标题文字重复：`{h.text}`", _loc(doc, h.line),
                               "改名以体现差异"))
        bucket[h.text] = h.line


# ---------------------------------------------------------------- §5.2 骨架

def _leading_quote_lines(doc: ParsedDoc, h1_line: int) -> list[tuple[int, str]]:
    """H1 之后连续的引用块行（含中间空行容忍），遇到非引用非空行停止。"""
    out = []
    for i in range(h1_line + 1, len(doc.lines) + 1):
        ln = doc.lines[i - 1] if i <= len(doc.lines) else ""
        if not ln.strip():
            continue
        if ln.lstrip().startswith(">"):
            out.append((i, ln))
            continue
        break
    return out


def _check_skeleton(doc: ParsedDoc, ctx: DocContext, out: list[Finding]):
    h1 = doc.h1()
    status = doc.status_base()
    if h1 is not None:
        quotes = _leading_quote_lines(doc, h1.line)
        dep_idx = next((i for i, (_, ln) in enumerate(quotes)
                        if ln.lstrip().startswith(DEPRECATION_PREFIX)), None)
        if status == "已废弃":
            if dep_idx is None:
                out.append(Finding("KB-12.2-deprecation-block", SEVERITY_ERROR,
                                   "已废弃文档缺少文首声明 `> ⚠️ 已废弃：…`",
                                   f"{doc.rel_path}:{h1.line}",
                                   "在摘要之前加 `> ⚠️ 已废弃：<原因>；替代文档见 […]。` 或写「无替代」"))
            summary_quotes = quotes[dep_idx + 1:] if dep_idx is not None else []
            if not summary_quotes:
                out.append(Finding("KB-5.2-summary-required", SEVERITY_ERROR,
                                   "废弃声明之后必须仍有一句 `> ` 摘要",
                                   f"{doc.rel_path}:{h1.line}"))
        else:
            summary_quotes = quotes
            if dep_idx is not None:
                out.append(Finding("KB-12.2-deprecation-block", SEVERITY_ERROR,
                                   "非废弃文档出现 `⚠️ 已废弃` 声明", _loc(doc, quotes[dep_idx][0]),
                                   "状态与声明不一致：改状态为 已废弃 或删除声明"))
        if summary_quotes:
            text = summary_quotes[0][1].lstrip().lstrip(">").strip()
            n = count_words(text)
            if not (30 <= n <= 80):
                out.append(Finding("KB-5.2-summary-length", SEVERITY_ERROR,
                                   f"摘要 {n} 字，须在 30–80 字之间",
                                   _loc(doc, summary_quotes[0][0])))
        else:
            out.append(Finding("KB-5.2-summary-required", SEVERITY_ERROR,
                               "H1 之后第一块必须是一句 `> ` 摘要（不能是代码块、表格或列表）",
                               f"{doc.rel_path}:{h1.line}"))
    # 空章节（§5.2 修正版：标题下既无正文，也无任何子标题）
    for i, h in enumerate(doc.headings):
        nxt = doc.headings[i + 1] if i + 1 < len(doc.headings) else None
        end = nxt.line if nxt else len(doc.lines) + 1
        if any(ln.strip() for ln in doc.lines[h.line:end - 1]):
            continue
        if nxt is not None and nxt.level > h.level:
            # 导航式分组：下有子标题，不算空章节；建议加导语（warning）
            out.append(Finding("KB-5.2-group-intro", SEVERITY_WARNING,
                               f"导航式分组标题下无导语：`{h.text}`",
                               _loc(doc, h.line),
                               "加一句导语说明本组范围与看点（纯分组标题无导语不构成缺陷）"))
            continue
        out.append(Finding("KB-5.2-empty-section", SEVERITY_ERROR,
                           f"空章节：`{h.text}` 下没有任何正文与子标题", _loc(doc, h.line)))
    # 占位内容（草案除外）
    if not _is_draft(doc):
        for i, ln in doc.prose_lines():
            s = ln.strip().strip("*_`> ").strip()
            if s.lower() in PLACEHOLDER_LINES or PLACEHOLDER_RE.search(ln):
                out.append(Finding("KB-5.2-placeholder", SEVERITY_ERROR,
                                   f"占位内容留在正式文档：`{ln.strip()[:30]}`", _loc(doc, i),
                                   "补齐内容，或把文档状态改为 草案"))
        _check_replacement(doc, ctx, out)


def _check_replacement(doc: ParsedDoc, ctx: DocContext, out: list[Finding]):
    """§12.2：废弃声明的替代链接必须存在，或写「无替代」。"""
    if doc.status_base() != "已废弃":
        return
    for i, ln in enumerate(doc.lines, 1):
        if not ln.lstrip().startswith(DEPRECATION_PREFIX):
            continue
        m = REPLACEMENT_RE.search(ln)
        if m:
            target = m.group(2).strip()
            resolved = _resolve(_dir_of(doc), target) if target.startswith(("./", "../")) else None
            if resolved is None or not ctx.path_exists(resolved):
                out.append(Finding("KB-12.2-replacement-valid", SEVERITY_ERROR,
                                   f"废弃声明的替代文档不存在：`{target}`", _loc(doc, i),
                                   "链接到实际存在的替代文档，或改写「无替代」"))
        elif "无替代" not in ln:
            out.append(Finding("KB-12.2-replacement-valid", SEVERITY_ERROR,
                               "废弃声明既无替代链接也未写「无替代」", _loc(doc, i),
                               "补 `替代文档见 […]。` 或 `；无替代。`"))
        return


def _dir_of(doc: ParsedDoc) -> str:
    return doc.rel_path.rsplit("/", 1)[0] if "/" in doc.rel_path else ""


# ---------------------------------------------------------------- §6 链接

def _check_links(doc: ParsedDoc, ctx: DocContext, out: list[Finding]):
    doc_links = [lk for lk in doc.links if lk.kind == "doc" and not lk.in_related]
    base = _dir_of(doc)
    variants: dict[str, set[str]] = {}
    for lk in doc_links:
        text = _strip_md_style(lk.text)
        if text in GENERIC_LINK_TEXTS:
            out.append(Finding("KB-6.1-generic-text", SEVERITY_ERROR,
                               f"链接文字无信息：`{lk.text}`", _loc(doc, lk.line),
                               "写出目标内容的描述"))
        elif len(text) <= 2:
            out.append(Finding("KB-6.1-link-text", SEVERITY_REVIEW,
                               f"链接文字过短，读者可能无法判断去向：`{lk.text}`",
                               _loc(doc, lk.line)))
        if lk.target.startswith("/"):
            out.append(Finding("KB-6.1-abs-link", SEVERITY_ERROR,
                               f"链接以 / 开头（根相对/绝对路径）：`{lk.target}`", _loc(doc, lk.line),
                               "改为 ./ 或 ../ 开头的相对路径"))
            continue
        if not lk.target.startswith(("./", "../")):
            out.append(Finding("KB-6.1-rel-prefix", SEVERITY_ERROR,
                               f"站内链接未以 ./ 或 ../ 开头：`{lk.target}`", _loc(doc, lk.line),
                               "裸相对路径与裸文件名都被禁止"))
            continue
        resolved = _resolve(base, lk.target)
        if resolved is None:
            out.append(Finding("KB-6.1-escapes-root", SEVERITY_ERROR,
                               f"链接越出项目根：`{lk.target}`", _loc(doc, lk.line)))
            continue
        path_only = resolved.split("#", 1)[0]
        if not ctx.path_exists(path_only):
            out.append(Finding("KB-6.1-broken-target", SEVERITY_ERROR,
                               f"链接目标不存在：`{lk.target}`", _loc(doc, lk.line)))
            continue
        variants.setdefault(resolved, set()).add(lk.target)
    for resolved, forms in variants.items():
        if len(forms) > 1:
            out.append(Finding("KB-6.2-style-mix", SEVERITY_ERROR,
                               f"同一目标混用多种链接写法：{sorted(forms)}", doc.rel_path,
                               "同一目标全文使用一种写法"))
    for target, lineno in doc.wiki_links:
        out.append(Finding("KB-6.1-wikilink", SEVERITY_ERROR,
                           f"使用 [[双向链接]] 语法：`[[{target}]]`", _loc(doc, lineno),
                           "改为 `[描述](./路径.md)`"))
    n = len([lk for lk in doc.links if lk.kind == "doc"])   # 含相关文档段（§6.2 计全文）
    # 索引型文档天然承载大量链接，上限放宽到 20（与「>20 主题过宽」一致）
    is_index_doc = bool(INDEX_DOC_RE.search(doc.title or ""))
    if n < 3:
        out.append(Finding("KB-6.2-link-count", SEVERITY_WARNING,
                           f"内部链接仅 {n} 条（建议 3–12 条）", doc.rel_path))
    elif n > 20:
        out.append(Finding("KB-6.2-link-count", SEVERITY_WARNING,
                           f"内部链接 {n} 条（>20），主题过宽应拆分或改为索引页", doc.rel_path))
    elif n > 12 and not is_index_doc:
        out.append(Finding("KB-6.2-link-count", SEVERITY_WARNING,
                           f"内部链接 {n} 条，超出建议的 3–12 条", doc.rel_path))


# ---------------------------------------------------------------- §7 相关文档

def _check_related(doc: ParsedDoc, ctx: DocContext, out: list[Finding]):
    if doc.is_readme:
        return
    for h in doc.headings:
        if "相关文档" in h.text and h.text != "相关文档":
            out.append(Finding("KB-7-title-exact", SEVERITY_ERROR,
                               f"相关文档小节标题必须逐字为 `## 相关文档`：`{h.text}`",
                               _loc(doc, h.line), "去掉编号与后缀"))
    rel = doc.related
    if rel is None:
        out.append(Finding("KB-7-missing", SEVERITY_WARNING,
                           "缺少「相关文档」段", doc.rel_path,
                           "文末加 `## 相关文档` 与 3–8 条链接"))
        return
    if rel.heading_level != 2:
        out.append(Finding("KB-7-title-exact", SEVERITY_ERROR,
                           f"相关文档标题层级须为 `##`：第 {rel.heading_line} 行是 `{'#'*rel.heading_level}`",
                           _loc(doc, rel.heading_line)))
    base = _dir_of(doc)
    items = 0
    seen_targets: set[str] = set()
    for i, ln in enumerate(rel.lines, rel.start_line):
        s = ln.strip()
        if not s:
            continue
        m = re.match(r"^-\s+\[([^\]]+)\]\(([^)\s]+)\)\s*$", s)
        if not m:
            out.append(Finding("KB-7-format", SEVERITY_ERROR,
                               f"相关文档段必须是无序列表 `- [描述](路径)`：`{s[:40]}`",
                               _loc(doc, i), "禁止散文、有序列表、表格、代码块；格式不带尾注"))
            continue
        items += 1
        desc, target = m.group(1).strip(), m.group(2).strip()
        if count_words(desc) > 30:
            out.append(Finding("KB-7-desc-length", SEVERITY_WARNING,
                               f"相关文档描述超 30 字：`{desc[:20]}…`", _loc(doc, i)))
        if desc == "相关文档":
            out.append(Finding("KB-7-desc-length", SEVERITY_WARNING,
                               "描述写「相关文档」本身没有信息量", _loc(doc, i),
                               "写清为什么相关"))
        kind = "doc" if target.startswith(("./", "../")) else (
            "external" if target.startswith(("http://", "https://", "mailto:")) else "other")
        if kind != "doc" or "#" in target:
            out.append(Finding("KB-7-non-doc-target", SEVERITY_ERROR,
                               f"相关文档只允许站内文档目标：`{target}`", _loc(doc, i),
                               "禁止站外链接、文内锚点、图片"))
            continue
        resolved = _resolve(base, target)
        if resolved is None:
            out.append(Finding("KB-7-non-doc-target", SEVERITY_ERROR,
                               f"相关文档链接越出项目根：`{target}`", _loc(doc, i)))
            continue
        if resolved == doc.rel_path:
            out.append(Finding("KB-7-self-link", SEVERITY_ERROR,
                               "相关文档链接了自身", _loc(doc, i)))
            continue
        if resolved in seen_targets:
            out.append(Finding("KB-7-dup-target", SEVERITY_ERROR,
                               f"相关文档重复链接同一目标：`{target}`", _loc(doc, i)))
            continue
        seen_targets.add(resolved)
        if not ctx.path_exists(resolved):
            out.append(Finding("KB-7-broken-target", SEVERITY_ERROR,
                               f"相关文档链接目标不存在：`{target}`", _loc(doc, i)))
    if not (3 <= items <= 8):
        out.append(Finding("KB-7-count", SEVERITY_WARNING,
                           f"相关文档 {items} 条（建议 3–8 条）", doc.rel_path))


# ---------------------------------------------------------------- §8 篇幅

def _check_length(doc: ParsedDoc, out: list[Finding]):
    if doc.is_readme:
        return
    heading_lines = {h.line for h in doc.headings}
    prose = [(i, ln) for i, ln in doc.prose_lines() if i not in heading_lines]
    n = count_words("\n".join(ln for _, ln in prose))
    if n > 5000:
        out.append(Finding("KB-8-over-limit", SEVERITY_ERROR,
                           f"正文 {n} 字，超过 5000 必须拆分为多篇", doc.rel_path,
                           "拆分并由一篇索引型文档串联"))
    elif n < 300:
        out.append(Finding("KB-8-under-limit", SEVERITY_ERROR,
                           f"正文仅 {n} 字（<300），不单独成篇", doc.rel_path,
                           "合并进上位文档，或扩充内容"))
    elif not (800 <= n <= 3000):
        out.append(Finding("KB-8-length-range", SEVERITY_WARNING,
                           f"正文 {n} 字，建议 800–3000 字", doc.rel_path))
    # 段落长度：只统计「文字段落」——连续的非列表、非表格、非引用、非标题文本行
    # （列表、表格、引用各有结构意义，不按段落长度计）
    reported = 0
    para: list[int] = []
    para_texts: list[str] = []

    def flush_para():
        nonlocal reported, para, para_texts
        if len(para) > 5 and reported < 5:
            reported += 1
            out.append(Finding("KB-8-long-para", SEVERITY_WARNING,
                               f"段落超过 5 行（{len(para)} 行）", _loc(doc, para[0]),
                               "按语义断段"))
        para = []
        para_texts = []

    for i, ln in prose + [(10**9, "")]:
        s = ln.strip()
        is_text = (bool(s)
                   and not re.match(r"^([-*+]|\d+[.、)])\s", s)
                   and not s.startswith(("|", ">", "#")))
        if is_text:
            para.append(i)
            para_texts.append(s)
            continue
        flush_para()
    flush_para()
    # 列表项数（相关文档段除外）
    # 列表项的「续行」（缩进但非列表标记的行）属于同一项，不能中断列表段——
    # 否则「1. xxx\n   续行\n2. yyy」会被算成两个单条列表。
    rel_span = ((doc.related.start_line, doc.related.end_line) if doc.related else None)
    run: list[int] = []

    def flush_run():
        if len(run) == 1:
            out.append(Finding("KB-8-list-min", SEVERITY_ERROR,
                               "列表只有 1 项（单条不用列表）",
                               _loc(doc, run[0]), "改写为正文句子"))
        elif len(run) == 2:
            out.append(Finding("KB-8-list-two", SEVERITY_WARNING,
                               "列表只有 2 项（规范建议并列项 3 条以上才用列表）",
                               _loc(doc, run[0]),
                               "若两项是同类并列建议补第三项或改写为正文；"
                               "字段清单/维度清单（如「输入」「参数」）保留列表更清晰"))
        run.clear()

    for i, ln in prose:
        if rel_span and rel_span[0] <= i <= rel_span[1]:
            continue
        if re.match(r"^ {0,3}(?:[-*+]|\d+[.、)])\s+", ln):
            run.append(i)
            continue
        if ln.startswith((" ", "\t")) and ln.strip():
            continue   # 列表项的续行：并入当前项
        flush_run()
    flush_run()
    # 文末总结段
    if doc.headings and doc.headings[-1].text in SUMMARY_HEADING_WORDS:
        out.append(Finding("KB-8-summary-heading", SEVERITY_REVIEW,
                           f"文末「{doc.headings[-1].text}」段疑似复述前文", _loc(doc, doc.headings[-1].line),
                           "总结段不增加结论时删除"))
    # 主题宽度提示
    h2_count = sum(1 for h in doc.headings if h.level == 2)
    if h2_count > 2 and n > 3000:
        out.append(Finding("KB-8-topic-focus", SEVERITY_REVIEW,
                           f"文档含 {h2_count} 个 H2 且 {n} 字，疑似承载多个主主题",
                           doc.rel_path, evidence={"h2_sections": h2_count, "words": n},
                           fix_hint="确认一篇只讲一件事；必要时拆分"))


# ---------------------------------------------------------------- §9 表格

_UNIT_HINT_RE = re.compile(r"(数量|耗时|价格|大小|成本|时间|次数|频率|内存|带宽|容量|长度|金额|费率|费用)$")


def _check_tables(doc: ParsedDoc, out: list[Finding]):
    for start, n_lines in doc.malformed_tables:
        out.append(Finding("KB-9-header-sep", SEVERITY_ERROR,
                           f"表格缺分隔行（表头下一行须为 `|---|`）", _loc(doc, start)))
    for tb in doc.tables:
        width = len(tb.header)
        if width > 6:
            out.append(Finding("KB-9-max-cols", SEVERITY_WARNING,
                               f"表格 {width} 列（建议不超过 6），可改为「字段清单 + 示例」",
                               _loc(doc, tb.start_line)))
        for cell_i, cell in enumerate(tb.header):
            if _UNIT_HINT_RE.search(cell) and "（" not in cell:
                out.append(Finding("KB-9-missing-unit", SEVERITY_REVIEW,
                                   f"数值列表头疑似未写单位：`{cell}`",
                                   _loc(doc, tb.start_line), "如「耗时（ms）」"))
        bad_rows = 0
        for lineno, cells in tb.rows:
            if len(cells) != width and bad_rows < 5:
                bad_rows += 1
                out.append(Finding("KB-9-column-count", SEVERITY_ERROR,
                                   f"表格行列数 {len(cells)} 与表头 {width} 不一致", _loc(doc, lineno)))
            for cell in cells:
                if "![" in cell or "```" in cell or "<br" in cell.lower():
                    out.append(Finding("KB-9-media-in-cell", SEVERITY_ERROR,
                                       "单元格内禁止图片、代码块、多段落", _loc(doc, lineno)))
                elif cell.count("；") >= 1 or cell.count("。") >= 2:
                    out.append(Finding("KB-9-cell-density", SEVERITY_REVIEW,
                                       f"单元格疑似承载多个事实：`{cell[:24]}…`", _loc(doc, lineno),
                                       "一个单元格只放一个事实，拆行表达"))
        if len(tb.rows) > 8:
            out.append(Finding("KB-9-long-table", SEVERITY_REVIEW,
                               f"表格 {len(tb.rows)} 行（>8），疑似承载叙述性内容",
                               _loc(doc, tb.start_line), "叙述性内容改为正文分节"))


# ---------------------------------------------------------------- §10 代码块

_SHELL_LANGS = {"bash", "sh", "shell", "zsh", "console"}

# 凭据占位符特征：出现这些词说明是示例值而非真实密钥
_PLACEHOLDER_WORDS = (
    "your_", "your-", "my_", "example", "placeholder", "dummy", "sample",
    "test_", "fake", "changeme", "xxx", "<token>", "<key>", "<password>",
    "填入", "替换", "此处", "略", "todo", "none", "null", "占位",
)


def _is_placeholder_secret(value: str, line: str) -> bool:
    """判断疑似凭据是否其实是占位符。"""
    low = line.lower()
    if any(w in low for w in _PLACEHOLDER_WORDS):
        return True
    # 值本身是明显的占位形态（如 your_dashscope_api_key、xxx_token）
    if any(w in value.lower() for w in ("your_", "xxx", "example", "dummy", "placeholder")):
        return True
    # 全大写标识符引用（如 api_key=KLING_KEY，值来自环境变量/常量）
    m = re.search(r"[=:]\s*['\"]?([A-Z][A-Z0-9_]*)['\"]?(?=[,)\s]|$)", line)
    if m:
        return True
    return False


def _check_code(doc: ParsedDoc, out: list[Finding]):
    for cb in doc.code_blocks:
        if not cb.language:
            out.append(Finding("KB-10-language-tag", SEVERITY_ERROR,
                               "代码块未标注语言", _loc(doc, cb.start_line),
                               "如 ```bash、```python"))
        if len(cb.lines) > 60:
            out.append(Finding("KB-10-block-length", SEVERITY_WARNING,
                               f"代码块 {len(cb.lines)} 行（建议不超过 60），只保留关键片段",
                               _loc(doc, cb.start_line)))
        for j, ln in enumerate(cb.lines):
            if re.match(r"^\s*\$+\s", ln) or re.match(r"^\s*#\s*\$\s", ln):
                out.append(Finding("KB-10-prompt-strip", SEVERITY_ERROR,
                                   f"命令块含提示符：`{ln.strip()[:40]}`",
                                   _loc(doc, cb.start_line + 1 + j),
                                   "删去提示符，保证可直接复制执行"))
            for m in SECRET_RE.finditer(ln):
                value = m.group(0)
                if _is_placeholder_secret(value, ln):
                    continue
                out.append(Finding("KB-10-secret-scan", SEVERITY_REVIEW,
                                   "代码块疑似包含真实凭据/密钥/内部地址", _loc(doc, cb.start_line + 1 + j),
                                   "替换为 `<token>`、`<host>` 等占位符",
                                   evidence={"pattern": m.re.pattern[:60]}))
                break
        if any("![" in ln for ln in cb.lines):
            out.append(Finding("KB-10-image-in-code", SEVERITY_ERROR,
                               "代码块内出现图片（禁止用截图代替可复制代码）",
                               _loc(doc, cb.start_line)))


# ---------------------------------------------------------------- §11 图片

_ASSET_NAME_RE = re.compile(r"^[一-鿿A-Za-z0-9.\-]+$")


def _check_images(doc: ParsedDoc, ctx: DocContext, out: list[Finding]):
    if doc.is_readme or not doc.images:
        return
    base = _dir_of(doc)
    figure_nos: list[int] = []
    for img in doc.images:
        if img.target.startswith(("http://", "https://", "/")):
            out.append(Finding("KB-11-external-image", SEVERITY_ERROR,
                               f"引用项目外或网络图片：`{img.target}`", _loc(doc, img.line),
                               "图片放入项目 assets/ 并用相对路径引用"))
            continue
        resolved = _resolve(base, img.target)
        if resolved is None:
            out.append(Finding("KB-11-external-image", SEVERITY_ERROR,
                               f"图片路径越出项目根：`{img.target}`", _loc(doc, img.line)))
            continue
        if "assets" not in resolved.split("/")[:-1]:
            out.append(Finding("KB-11-asset-location", SEVERITY_ERROR,
                               f"图片不在 assets/ 目录：`{img.target}`", _loc(doc, img.line),
                               "统一放项目级或分类目录下的 assets/"))
        name = resolved.rsplit("/", 1)[-1]
        if not _ASSET_NAME_RE.match(name) or name.rsplit(".", 1)[-1] != name.rsplit(".", 1)[-1].lower():
            out.append(Finding("KB-11-asset-name", SEVERITY_ERROR,
                               f"附件文件名非法：`{name}`（只含中文/英文/数字/连字符/点，扩展名小写）",
                               _loc(doc, img.line)))
        if not ctx.path_exists(resolved):
            out.append(Finding("KB-11-broken-asset", SEVERITY_ERROR,
                               f"引用的图片/附件不存在：`{img.target}`", _loc(doc, img.line)))
        if ctx.file_size:
            size = ctx.file_size(resolved)
            if size and size > 2 * 1024 * 1024:
                out.append(Finding("KB-11-size", SEVERITY_WARNING,
                                   f"图片 {size // 1024 // 1024}MB（建议不超过 2MB），压缩或拆分",
                                   _loc(doc, img.line)))
        if not img.alt:
            out.append(Finding("KB-11-alt-text", SEVERITY_ERROR,
                               "图片引用缺少替代文本", _loc(doc, img.line),
                               "写描述图中内容的替代文本"))
        elif img.alt.lower() in GENERIC_ALT_TEXTS:
            out.append(Finding("KB-11-alt-quality", SEVERITY_REVIEW,
                               f"替代文本无信息：`{img.alt}`", _loc(doc, img.line),
                               "描述图中内容，不写「图片/截图」"))
        if re.search(r"<img[^>]*(width|height)\s*=", doc.lines[img.line - 1], re.IGNORECASE) \
                or "{:width" in doc.lines[img.line - 1] or "{:height" in doc.lines[img.line - 1]:
            out.append(Finding("KB-11-fixed-size", SEVERITY_ERROR,
                               "图片引用硬编码像素宽高", _loc(doc, img.line)))        # 图注：图片行之后第一行非空行，样式须为斜体（_…_）或引用（> …）
        caption = None
        for i in range(img.line + 1, len(doc.lines) + 1):
            ln = doc.lines[i - 1]
            if not ln.strip():
                continue
            if ln.strip().startswith((">", "_", "*")):
                caption = (i, ln.strip())
            break
        m = CAPTION_RE.match(caption[1]) if caption else None
        if not m:
            out.append(Finding("KB-11-caption", SEVERITY_ERROR,
                               "图片下方缺「图 N：说明」（斜体或引用一行）", _loc(doc, img.line)))
        else:
            figure_nos.append(int(m.group(1)))
    expected = 1
    for n in figure_nos:
        if n != expected:
            out.append(Finding("KB-11-figure-numbering", SEVERITY_ERROR,
                               f"图编号不连续：期望 图{expected}，实际 图{n}", doc.rel_path,
                               "图 N 在同一文档内连续编号"))
            break
        expected += 1
    if len(doc.images) > 8:
        out.append(Finding("KB-11-image-count", SEVERITY_WARNING,
                           f"文档 {len(doc.images)} 张图片（建议不超过 8）", doc.rel_path))
    _check_fixed_size_lines(doc, out)


def _check_fixed_size_lines(doc: ParsedDoc, out: list[Finding]):
    """<img> 标签等非 Markdown 语法的宽高硬编码（不经过 ![]() 解析，需行扫描）。"""
    for i, ln in doc.prose_lines():
        if re.search(r"<img[^>]*(width|height)\s*=", ln, re.IGNORECASE) \
                or "{:width" in ln or "{:height" in ln:
            out.append(Finding("KB-11-fixed-size", SEVERITY_ERROR,
                               "图片引用硬编码像素宽高", _loc(doc, i),
                               "宽度交给渲染器，不在 Markdown 中写像素值"))


# ---------------------------------------------------------------- §12/§13 文档级

def _check_lifecycle(doc: ParsedDoc, ctx: DocContext, out: list[Finding]):
    status = doc.status_base()
    if status == "已归档" and not doc.rel_path.startswith("archive/"):
        out.append(Finding("KB-12.2-archive-dir", SEVERITY_WARNING,
                           "已归档文档不在 archive/ 目录", doc.rel_path,
                           "移入项目内 archive/ 统一管理"))
    if doc.is_readme:
        return
    next_raw = doc.fm_value("下次复核") if doc.fm else None
    if next_raw and next_raw != "按需" and _valid_date(next_raw):
        due = date.fromisoformat(next_raw)
        if due < ctx.today and status in ("稳定", "需监控", "草案"):
            if status == "需监控":
                out.append(Finding("KB-12.2-review-overdue-monitored", SEVERITY_ERROR,
                                   f"`需监控` 文档已过复核日期（{next_raw}），触碰前必须复核",
                                   doc.rel_path, "复核内容并更新 最后更新 与 下次复核"))
            else:
                out.append(Finding("KB-12.2-review-overdue", SEVERITY_WARNING,
                                   f"已过 `下次复核`（{next_raw}），触碰时先复核再更新",
                                   doc.rel_path))


def _check_retrieval(doc: ParsedDoc, out: list[Finding]):
    title = doc.title
    if not title:
        return
    if title.strip() in GENERIC_TITLE_EXACT or re.fullmatch(r"方案\s*[A-Z0-9一二三四五六七八九十]+", title.strip()):
        out.append(Finding("KB-13-generic-title", SEVERITY_ERROR,
                           f"标题为纯代号/无上下文名词：`{title}`", doc.rel_path,
                           "标题包含该文档的核心名词"))
    if re.fullmatch(r"[A-Z0-9_\-]{1,6}", title.strip()):
        out.append(Finding("KB-13-opaque-title", SEVERITY_REVIEW,
                           f"标题疑似纯缩写：`{title}`", doc.rel_path, "补全上下文"))
    hits = sum(1 for w in LOCATION_WORDS if re.search(w, title))
    if hits >= 2:
        out.append(Finding("KB-13-stacked-keywords", SEVERITY_ERROR,
                           f"标题堆叠 {hits} 个定位词：`{title}`", doc.rel_path,
                           "一个标题只保留一个定位词"))
    # 标题核心词与正文首段用词一致性（review 档近似）
    runs = re.findall(r"[一-鿿]{2,}|[A-Za-z][A-Za-z0-9]+", title)
    first_para: list[str] = []
    h1 = doc.h1()
    if h1:
        # 导航式分组：标题后第一行若是「导语」（本节/本组…句），不算正文首段
        group_lines = set()
        for idx, h in enumerate(doc.headings):
            nxt_h = doc.headings[idx + 1] if idx + 1 < len(doc.headings) else None
            if nxt_h is not None and nxt_h.level > h.level:
                group_lines.add(h.line)
        started = False
        # 「正文首个段落」限定在 H1 之后、第一个二级标题之前的开篇区域
        first_h2 = next((h for h in doc.headings if h.level == 2 and h.line > h1.line), None)
        region_end = first_h2.line if first_h2 else len(doc.lines) + 1
        for i, raw in doc.prose_lines():
            if i <= h1.line or i >= region_end:
                if i >= region_end:
                    break
                continue
            ln = raw.strip()
            if not ln:
                if started:
                    break   # 首段结束
                continue
            if ln.startswith(">") or ln.startswith("#"):
                continue   # 跳过摘要（或废弃声明）与各级标题行
            if re.fullmatch(r"[-*_]{3,}", ln):
                continue   # 跳过水平分割线（不是正文）
            if ln.startswith("<!--"):
                continue   # 跳过 HTML 注释（工具元数据，不是正文）
            if re.match(r"^([-*+]|\d+[.、)])\s", ln):
                continue   # 跳过列表项（列表不是段落）
            if re.fullmatch(r"(\*\*[^*]+\*\*|\*[^*]+\*)", ln):
                continue   # 跳过整行加粗（引言元素，不是段落）
            # 导语：位于导航式分组标题下、且是「本节/本组…」句式
            owner = next((h for h in reversed(doc.headings) if h.line < i), None)
            if (owner is not None and owner.line in group_lines and not started
                    and re.match(r"^(本节|本组|本部分|这一节)", ln)):
                continue
            first_para.append(ln)
            started = True
            if len(first_para) >= 6:
                break
    if runs and first_para:
        para_text = "\n".join(first_para)
        cjk_grams = [r[i:i + 2] for r in runs if re.fullmatch(r"[一-鿿]+", r)
                     for i in range(len(r) - 1)]
        latin = [r.lower() for r in runs if re.fullmatch(r"[A-Za-z][A-Za-z0-9]+", r)]
        hit = any(g in para_text for g in cjk_grams) or any(w in para_text.lower() for w in latin)
        if not hit:
            out.append(Finding("KB-13-title-body-mismatch", SEVERITY_REVIEW,
                               f"标题核心词未见正文首段用词：`{title}`", doc.rel_path,
                               "标题核心名词应与正文首段用词一致"))


# ---------------------------------------------------------------- 入口

def check_document(doc: ParsedDoc, ctx: DocContext) -> list[Finding]:
    out: list[Finding] = []
    out.extend(doc.fm_findings)
    _check_frontmatter(doc, ctx, out)
    if not doc.is_readme:
        _check_headings(doc, out)
        _check_skeleton(doc, ctx, out)
        _check_links(doc, ctx, out)
        _check_related(doc, ctx, out)
        _check_length(doc, out)
    _check_tables(doc, out)
    _check_code(doc, out)
    _check_images(doc, ctx, out)
    _check_fixed_size_lines(doc, out)
    _check_lifecycle(doc, ctx, out)
    _check_retrieval(doc, out)
    return out
