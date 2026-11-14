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


def chunk_markdown(text: str) -> list[Chunk]:
    lines = text.splitlines()
    tokens = _md.parse(text)

    # 标题边界：token.map = [start_line, end_line)
    headings: list[tuple[int, int, str]] = []  # (line_idx, level, title)
    for i, tok in enumerate(tokens):
        if tok.type == "heading_open" and tok.map:
            inline = tokens[i + 1] if i + 1 < len(tokens) else None
            title = inline.content.strip() if inline and inline.type == "inline" else ""
            headings.append((tok.map[0], _heading_level(tok.tag), title))

    if not headings:
        return _split_plain(text, len(lines))

    chunks: list[Chunk] = []
    # 前言（第一个标题之前）
    if headings[0][0] > 0:
        pre = "\n".join(lines[: headings[0][0]]).strip()
        if pre:
            chunks.append(Chunk(pre, "document", heading_path=[], start_line=1, end_line=headings[0][0]))

    for idx, (line_idx, level, title) in enumerate(headings):
        end = len(lines)
        for nxt_line, nxt_level, _ in headings[idx + 1:]:
            if nxt_level <= level:
                end = nxt_line
                break
        body = "\n".join(lines[line_idx:end]).strip()
        if not body:
            continue
        # heading_path：最近的上级标题链
        path: list[str] = []
        for prev_line, prev_level, prev_title in reversed(headings[: idx + 1]):
            if prev_level < level or prev_line == line_idx:
                path.insert(0, prev_title)
                level = prev_level
                if prev_line == line_idx:
                    break
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
