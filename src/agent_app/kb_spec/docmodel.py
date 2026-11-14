"""文档模型：把一篇 Markdown 解析为可校验的结构（标题/链接/表格/代码块/图片）。

解析是宽容的：语法怪异不报错，交给 checks_* 按规则 ID 判定。
行号一律 1-based。代码围栏内的行不参与标题/链接/表格解析。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .frontmatter import parse_frontmatter
from .vocab import Finding

_FENCE_RE = re.compile(r"^ {0,3}(```+|~~~+).*$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(\s*([^)\s]+)(?:\s+\"[^\"]*\")?\s*\)")
_LINK_RE = re.compile(r"(?<!\!)\[([^\]]+)\]\(\s*([^)\s]+)(?:\s+\"[^\"]*\")?\s*\)")
_WIKI_RE = re.compile(r"\[\[([^\]]+)\]\]")
_LIST_ITEM_RE = re.compile(r"^ {0,3}(?:[-*+]|\d+[.、)])\s+")
_QUOTE_RE = re.compile(r"^ {0,3}>")
_TABLE_ROW_RE = re.compile(r"^ {0,3}\|.*")
_TABLE_SEP_RE = re.compile(r"^ {0,3}\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$")
_EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "ftp://")

RELATED_TITLE = "相关文档"


@dataclass
class Heading:
    level: int
    text: str
    line: int
    parent: int = -1   # 指向 headings 列表下标；-1 表示顶层


@dataclass
class Link:
    text: str
    target: str
    line: int
    kind: str           # doc | external | anchor
    in_related: bool


@dataclass
class ImageRef:
    alt: str
    target: str
    line: int


@dataclass
class CodeBlock:
    language: str
    start_line: int
    end_line: int
    lines: list[str] = field(default_factory=list)


@dataclass
class TableBlock:
    start_line: int
    header: list[str]
    rows: list[tuple[int, list[str]]]   # (行号, 单元格列表)


@dataclass
class RelatedSection:
    heading_line: int
    heading_level: int
    start_line: int
    end_line: int       # 含末行（下一标题前一行 / 文件末行）
    lines: list[str]


@dataclass
class ParsedDoc:
    rel_path: str
    text: str
    lines: list[str]
    fm: dict | None
    fm_findings: list[Finding]
    has_fm_block: bool
    body_start: int          # 正文起始行号（1-based）
    headings: list[Heading]
    links: list[Link]
    images: list[ImageRef]
    code_blocks: list[CodeBlock]
    tables: list[TableBlock]
    malformed_tables: list[tuple[int, int]] = field(default_factory=list)  # (起始行, 行数) 无分隔行
    related: RelatedSection | None = None
    wiki_links: list[tuple[str, int]] = field(default_factory=list)   # ([[target]], 行号)

    @property
    def is_readme(self) -> bool:
        return self.rel_path == "README.md"

    @property
    def title(self) -> str:
        for h in self.headings:
            if h.level == 1:
                return h.text
        return ""

    def h1(self) -> Heading | None:
        h1s = [h for h in self.headings if h.level == 1]
        return h1s[0] if h1s else None

    def fm_value(self, key: str) -> str | None:
        return self.fm.get(key) if self.fm else None

    def status_base(self) -> str | None:
        v = self.fm_value("状态")
        if v is None:
            return None
        return v.split("（", 1)[0].strip()

    def prose_lines(self) -> list[tuple[int, str]]:
        """排除代码块与表格行后的正文行 (行号, 内容)。"""
        excluded: set[int] = set()
        for cb in self.code_blocks:
            excluded.update(range(cb.start_line, cb.end_line + 1))
        for tb in self.tables:
            excluded.update(range(tb.start_line, tb.start_line + 2 + len(tb.rows)))
        return [(i, ln) for i, ln in enumerate(self.lines, 1)
                if i not in excluded and i >= self.body_start]


def count_words(text: str) -> int:
    """字数：CJK 字符逐字计 + 拉丁/数字词元计（机械近似，规则 §8 使用）。"""
    cjk = len(re.findall(r"[一-鿿]", text))
    latin = len(re.findall(r"[A-Za-z0-9]+", text))
    return cjk + latin


def classify_link(target: str) -> str:
    low = target.strip().lower()
    if low.startswith(_EXTERNAL_PREFIXES):
        return "external"
    if target.startswith("#"):
        return "anchor"
    return "doc"


def _resolve_parent_stack(headings: list[Heading], level: int) -> int:
    """返回新标题的父标题下标：向上找最近一个 level 更小的标题。"""
    for i in range(len(headings) - 1, -1, -1):
        if headings[i].level < level:
            return i
    return -1


def parse_document(rel_path: str, text: str) -> ParsedDoc:
    lines = text.split("\n")
    fm, fm_findings, body_start = parse_frontmatter(text)
    has_fm_block = bool(lines) and lines[0].rstrip("\r") == "---"

    headings: list[Heading] = []
    links: list[Link] = []
    images: list[ImageRef] = []
    code_blocks: list[CodeBlock] = []
    tables: list[TableBlock] = []
    malformed_tables: list[tuple[int, int]] = []
    wiki: list[tuple[str, int]] = []

    # ---- 相关文档段定位（先扫描标题，确定段范围后再解析链接归属） ----
    related: RelatedSection | None = None
    fence: CodeBlock | None = None
    fence_marker = ""
    i = 0
    heading_lines: dict[int, tuple[int, str]] = {}   # 行号 -> (level, text)
    while i < len(lines):
        raw = lines[i]
        fm_open = has_fm_block and i < body_start - 1
        m = _FENCE_RE.match(raw) if not fm_open else None
        if fence is None and m:
            marker = m.group(1)
            fence_marker = marker[:3]
            info = raw.lstrip()[3:].strip()
            fence = CodeBlock(language=info.split()[0] if info else "",
                              start_line=i + 1, end_line=i + 1)
            i += 1
            continue
        if fence is not None:
            if raw.lstrip().startswith(fence_marker):
                fence.end_line = i + 1
                code_blocks.append(fence)
                fence = None
            else:
                fence.lines.append(raw)
            i += 1
            continue
        hm = _HEADING_RE.match(raw)
        if hm:
            level = len(hm.group(1))
            heading_lines[i + 1] = (level, hm.group(2).strip())
            headings.append(Heading(level, hm.group(2).strip(), i + 1))
            i += 1
            continue
        i += 1
    if fence is not None:   # 未闭合围栏按到文件尾处理
        fence.end_line = len(lines)
        code_blocks.append(fence)

    for idx, h in enumerate(headings):
        h.parent = _resolve_parent_stack(headings[:idx], h.level)

    # 相关文档段：标题文字逐字为「相关文档」的标题，取到下一个同级或更高级标题
    for idx, h in enumerate(headings):
        if h.text == RELATED_TITLE:
            end = len(lines)
            for h2 in headings[idx + 1:]:
                if h2.level <= h.level:
                    end = h2.line - 1
                    break
            related = RelatedSection(
                heading_line=h.line, heading_level=h.level,
                start_line=h.line + 1, end_line=max(end, h.line),
                lines=lines[h.line:end],
            )
            break

    def in_related(lineno: int) -> bool:
        return related is not None and related.start_line <= lineno <= related.end_line

    # ---- 链接 / 图片 / 表格 / wiki 链接扫描（跳过 frontmatter 与代码块） ----
    excluded: set[int] = set()
    for cb in code_blocks:
        excluded.update(range(cb.start_line, cb.end_line + 1))

    tbl_start = -1
    tbl_rows: list[tuple[int, list[str]]] = []

    def flush_table(cur: int):
        nonlocal tbl_start, tbl_rows
        if tbl_start == -1:
            return
        if len(tbl_rows) >= 2 and _TABLE_SEP_RE.match(tbl_rows[1][1].replace(" ", "")):
            header = _split_row(tbl_rows[0][1])
            rows = [(ln, _split_row(text)) for ln, text in tbl_rows[2:]]
            tables.append(TableBlock(tbl_start, header, rows))
        elif len(tbl_rows) >= 2:
            # 连续管道行但缺分隔行：GFM 意义上不是表格，记录为坏表格供 §9 判定
            malformed_tables.append((tbl_start, len(tbl_rows)))
        tbl_start, tbl_rows = -1, []

    for lineno, raw in enumerate(lines, 1):
        if lineno <= body_start - 1 and has_fm_block:
            continue
        if lineno in excluded:
            flush_table(lineno)
            continue
        stripped = raw.strip()
        if _TABLE_ROW_RE.match(raw):
            if tbl_start == -1:
                tbl_start = lineno
            tbl_rows.append((lineno, stripped))
            continue
        flush_table(lineno)

        for wm in _WIKI_RE.finditer(raw):
            wiki.append((wm.group(1), lineno))
        work = _IMAGE_RE.sub("", raw)   # 先剥掉图片，避免其 alt 被当成链接文字
        for im in _IMAGE_RE.finditer(raw):
            images.append(ImageRef(im.group(1).strip(), im.group(2).strip(), lineno))
        for lm in _LINK_RE.finditer(work):
            target = lm.group(2).strip()
            links.append(Link(lm.group(1).strip(), target, lineno,
                              classify_link(target), in_related(lineno)))
    flush_table(len(lines) + 1)

    return ParsedDoc(
        rel_path=rel_path, text=text, lines=lines, fm=fm, fm_findings=fm_findings,
        has_fm_block=has_fm_block, body_start=body_start, headings=headings,
        links=links, images=images, code_blocks=code_blocks, tables=tables,
        malformed_tables=malformed_tables,
        related=related, wiki_links=wiki,
    )


def _split_row(row: str) -> list[str]:
    parts = row.strip()
    if parts.startswith("|"):
        parts = parts[1:]
    if parts.endswith("|"):
        parts = parts[:-1]
    return [c.strip() for c in parts.split("|")]
