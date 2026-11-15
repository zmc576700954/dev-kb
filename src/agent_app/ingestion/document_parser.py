"""文档解析与切块：按 Markdown 标题层级切块，保留 heading_path。

规则：
- 每个标题开启新块，块内容为该标题到下一个同级或更高级标题之间。
- 超大块（> MAX_CHUNK_CHARS）按段落边界二次切分。
- 过小的尾部块（< MIN_CHUNK_CHARS）并入前一块。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from markdown_it import MarkdownIt

MAX_CHUNK_CHARS = 2000
MIN_CHUNK_CHARS = 120

_md = MarkdownIt()


@dataclass
class Chunk:
    content: str
    source_type: str            # document | code
    language: str = ""
    symbol: str = ""
    heading_path: list[str] = field(default_factory=list)
    start_line: int = 0         # 1 起始
    end_line: int = 0


def _heading_level(tag: str) -> int:
    return int(tag[1]) if tag.startswith("h") and len(tag) == 2 else 0


def _frontmatter_span(lines: list[str]) -> tuple[int, int] | None:
    """frontmatter 块的行区间（含首尾 ---），没有则 None。"""
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            return (0, i + 1)
    return None


def _extract_headings(text: str) -> list[tuple[int, int, str]]:
    """(行号, 层级, 标题) 列表，并修正 frontmatter 造成的伪标题。

    Markdown 解析器不认识 YAML frontmatter：`状态: 稳定` 会被 setext 规则
    当成二级标题，于是 frontmatter 的最后一个键值变成了一条假标题。
    这里保留它的位置（切块边界依赖它），但把标题换成文档的首个真实标题。
    """
    lines = text.splitlines()
    tokens = _md.parse(text)
    headings: list[tuple[int, int, str]] = []
    for i, tok in enumerate(tokens):
        if tok.type == "heading_open" and tok.map:
            inline = tokens[i + 1] if i + 1 < len(tokens) else None
            title = inline.content.strip() if inline and inline.type == "inline" else ""
            headings.append((tok.map[0], _heading_level(tok.tag), title))

    fm = _frontmatter_span(lines)
    if fm:
        inside = [h for h in headings if fm[0] <= h[0] < fm[1]]
        outside = [h for h in headings if not (fm[0] <= h[0] < fm[1])]
        if inside and outside:
            real_title = outside[0][2]
            headings = sorted(
                [(line, level, real_title) for line, level, _ in inside] + outside,
                key=lambda h: h[0],
            )
    return headings


def _heading_path(headings: list[tuple[int, int, str]], idx: int) -> list[str]:
    """标题链 = 祖先标题 + 自身标题。

    从最近的祖先往回收集：只收层级严格更浅的标题，直到遇到同级或更浅的为止。
    """
    _, level, title = headings[idx]
    path: list[str] = []
    cur_level = level
    for _line, prev_level, prev_title in reversed(headings[:idx]):
        if prev_level < cur_level:
            path.insert(0, prev_title)
            cur_level = prev_level
    path.append(title)   # 自身标题在末尾：链的顺序是 祖先 → 自身
    return path


def first_heading(text: str) -> str:
    """文档的第一个标题（跳过 frontmatter 造成的伪标题；没有则返回空串）。"""
    headings = _extract_headings(text)
    if not headings:
        return ""
    lines = text.splitlines()
    fm = _frontmatter_span(lines)
    if fm:
        real = [h for h in headings if not (fm[0] <= h[0] < fm[1])]
        if real:
            return real[0][2]
    return headings[0][2]


def section_paths(text: str) -> list[tuple[int, list[str]]]:
    """章节起点行 → 标题链。用于按已有索引回填 heading_path（不重切块）。"""
    headings = _extract_headings(text)
    if not headings:
        return []
    sections: list[tuple[int, list[str]]] = []
    if headings[0][0] > 0:
        sections.append((1, [headings[0][2]]))
    for idx, (line_idx, _level, _title) in enumerate(headings):
        sections.append((line_idx + 1, _heading_path(headings, idx)))
    return sections


def chunk_markdown(text: str) -> list[Chunk]:
    lines = text.splitlines()

    # 标题边界：token.map = [start_line, end_line)
    headings = _extract_headings(text)

    if not headings:
        return _split_plain(text, len(lines))

    chunks: list[Chunk] = []
    # 前言（第一个标题之前）：frontmatter 等元数据落在这一段。
    # 它归属文档的首个标题——否则这块没有标题信号，在检索里等于死块。
    if headings[0][0] > 0:
        pre = "\n".join(lines[: headings[0][0]]).strip()
        if pre:
            chunks.append(Chunk(pre, "document", heading_path=[headings[0][2]],
                                start_line=1, end_line=headings[0][0]))

    for idx, (line_idx, level, title) in enumerate(headings):
        end = len(lines)
        for nxt_line, nxt_level, _ in headings[idx + 1:]:
            if nxt_level <= level:
                end = nxt_line
                break
        body = "\n".join(lines[line_idx:end]).strip()
        if not body:
            continue
        # heading_path：祖先标题链 + 自身标题
        path = _heading_path(headings, idx)
        for part in _split_long(body, line_idx + 1, end):
            part.heading_path = path
            chunks.append(part)

    return _merge_small(chunks)


def _split_plain(text: str, n_lines: int) -> list[Chunk]:
    text = text.strip()
    if not text:
        return []
    c = Chunk(text, "document", start_line=1, end_line=n_lines)
    return _split_long(c.content, 1, n_lines)


def _split_long(body: str, start_line: int, end_line: int) -> list[Chunk]:
    if len(body) <= MAX_CHUNK_CHARS:
        return [Chunk(body, "document", start_line=start_line, end_line=end_line)]
    out: list[Chunk] = []
    buf: list[str] = []
    buf_start = start_line
    cur = start_line
    for para in body.split("\n\n"):
        para_lines = para.count("\n") + 1
        if buf and sum(len(p) + 2 for p in buf) + len(para) > MAX_CHUNK_CHARS:
            out.append(Chunk("\n\n".join(buf), "document", start_line=buf_start, end_line=cur - 1))
            buf, buf_start = [], cur
        buf.append(para)
        cur += para_lines + 1  # 空行
    if buf:
        out.append(Chunk("\n\n".join(buf), "document", start_line=buf_start, end_line=end_line))
    return out


def _merge_small(chunks: list[Chunk]) -> list[Chunk]:
    merged: list[Chunk] = []
    for c in chunks:
        # 以新标题开头的小块不并入前块，避免 heading_path 语义混乱
        starts_with_heading = c.content.lstrip().startswith("#")
        if merged and len(c.content) < MIN_CHUNK_CHARS and not starts_with_heading:
            prev = merged[-1]
            prev.content += "\n\n" + c.content
            prev.end_line = c.end_line
        else:
            merged.append(c)
    return merged
