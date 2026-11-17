#!/usr/bin/env python3
"""按知识库生成规范校验 kb/ 下的项目。

用法::

    python scripts/check_kb.py                     # 全库，有 error 则退出 1
    python scripts/check_kb.py --project kaiwu     # 只查一个项目（可重复）
    python scripts/check_kb.py --strict            # warning 也算失败
    python scripts/check_kb.py --format json       # 机器可读（按项目分组）

只依赖 Python 标准库。规则编号与规范手册一一对应：
S 结构 / M 元数据 / H 标题 / L 链接 / T 文本，共 26 条（17 error + 9 warning）。

已知盲区（规范要求写明的“未检查及原因”）：

* **夹子标题缺正文**：``## A`` → ``### B``（无正文）→ ``## C`` 这种写法，
  H3 只比较“相邻同级标题之间”是否有正文，A 与 C 之间隔着 H3 B，
  两者不算相邻同级，因此检查不到；B 与 A 不同级，也不在比较范围。
  机器只能按“相邻同级”判定，要抓这种夹层需要引入“父级子树是否为空”的新规则，
  会与规范定义的 H3 判据冲突，故不做，交由评审自觉。
* 元数据 M1–M4 整组豁免 README.md（索引页不是文档）。
* 文本 T 组只扫正文散文，代码块、行内代码、链接目标、裸 URL、书名号内容先剥除，
  因此这些区域的用词不查。

两条贯穿全脚本的约定：

* 一个缺陷一条 Finding：一篇文档缺三个必填键，出一条 M2，message 里列全。
* 索引页豁免 M 组：README.md 没有 frontmatter，M1–M4 整组跳过它；
  H 组不豁免，README 的标题结构照查。
"""

from __future__ import annotations

import argparse
import itertools
import json
import posixpath
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

ERROR = 'error'
WARNING = 'warning'

README = 'README.md'
EXEMPT_PROJECTS = frozenset({'_demo'})

PRIORITIES = {'🔴 P0', '🟡 P1', '🟢 P2'}
STATUS_BASES = {'草案', '模板', '稳定', '需持续监控', '过期', '已归档'}
REQUIRED_KEYS = ('优先级', '状态', '最后更新', '下次 review')
BANNED_PATH_CHARS = set(' \t_()（）[]【】')

HEADING_RE = re.compile(r'^( {0,3})(#{1,6})\s+(.+?)\s*$')
DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
STATUS_RE = re.compile(r'^([^（(]+)[（(].*[）)]$')
HEADING_LINK_RE = re.compile(r'\[[^\]]*\]\(')
NUMBER_PREFIX_RE = re.compile(r'^\d{2}-')
CLOSING_HASH_RE = re.compile(r'\s+#+\s*$')
RELATED_SECTION = '相关文档'
RELATED_BULLET_RE = re.compile(r'^-\s+\[[^\]]+\]\([^)]+\)')
RELATED_TARGET_RE = re.compile(r'\[[^\]]+\]\(\s*([^)\s]+)(?:\s+"[^"]*")?\s*\)')
VAGUE_LINK_TEXTS = frozenset({'点击这里', '点击此处', '本文档', '更多', '详见'})
VAGUE_PROSE_TEXTS = ('点击这里', '点击此处')
FILLER_PHRASES = ('简单来说', '显然', '众所周知')
CJK_BANG_RE = re.compile(r'[一-鿿]!')
INLINE_CODE_RE = r'(`+)([^`]*)\1'
BARE_URL_RE = r'https?://[^\s！]+'

FENCE_RE = re.compile(r'^\s*(```|~~~)')
WIKI_RE = re.compile(r'\[\[([^\]]+)\]\]')
HREF_RE = re.compile(r'href="([^"]+)"')
MD_LINK_RE = re.compile(r'(?<!!)\[([^\]]*)\]\(\s*([^)\s]+)(?:\s+"[^"]*")?\s*\)')
MD_IMAGE_RE = re.compile(r'!\[([^\]]*)\]\(\s*([^)\s]+)(?:\s+"[^"]*")?\s*\)')


@dataclass(frozen=True)
class Finding:
    rule_id: str
    level: str
    path: str
    message: str


@dataclass(frozen=True)
class Rule:
    rule_id: str
    level: str
    check: object


class KbFileDecodeError(Exception):
    def __init__(self, project: str, rel: str, path: Path, original: UnicodeDecodeError) -> None:
        super().__init__(f'{project}/{rel} 不是合法 UTF-8')
        self.project = project
        self.rel = rel
        self.path = path
        self.original = original


@dataclass
class Doc:
    rel: str
    path: Path
    text: str
    frontmatter: dict[str, str]
    raw_body: str
    body: str
    headings: list[tuple[int, str]]
    body_offset: int = 0

    def file_line(self, body_index: int) -> int:
        return self.body_offset + body_index + 1


@dataclass
class Project:
    name: str
    root: Path
    kb_root: Path
    docs: dict[str, Doc] = field(default_factory=dict)
    readme: str | None = None
    files: list[str] = field(default_factory=list)
    dirs: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- 解析层


def _frontmatter_bounds(text: str) -> tuple[int, int] | None:
    """返回 frontmatter 内容行的下标区间 [start, end)。

    ``start`` 指向第二条 ``---`` 之后的第一行内容的索引，``end`` 指向闭合
    ``---`` 的索引；没有成对分隔线时返回 ``None``。
    """
    lines = text.split('\n')
    if lines[0].strip() != '---':
        return None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == '---':
            return 1, i
    return None


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """拆出 frontmatter 键值对与正文。

    没有合法 frontmatter 时返回 ``({}, text)``；两条 ``---`` 之间一个
    ``键: 值`` 都切不出来时也算没有 frontmatter（正文里的分隔线不是元数据）。
    """
    lines = text.split('\n')
    if lines[0].strip() != '---':
        return {}, text
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == '---':
            break
    else:
        return {}, text
    data: dict[str, str] = {}
    for line in lines[1:i]:
        key, sep, value = line.partition(': ')
        if not sep:
            continue
        data[key.strip()] = value.strip()
    if not data:
        return {}, text
    return data, '\n'.join(lines[i + 1:])


def _iter_fence_lines(text: str):
    offset = 0
    in_fence = False
    for line in text.splitlines(keepends=True):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            yield line, True, offset
        else:
            yield line, in_fence, offset
        offset += len(line)


def strip_fenced_blocks(text: str) -> str:
    out: list[str] = []
    for line, in_fence, _offset in _iter_fence_lines(text):
        if in_fence:
            out.append('\n' if line.endswith('\n') else '')
        else:
            out.append(line)
    return ''.join(out)


def find_md_links_with_text(text: str) -> list[tuple[str, str, int, int]]:
    out: list[tuple[str, str, int, int]] = []
    for line, in_fence, offset in _iter_fence_lines(text):
        if in_fence:
            continue
        for m in MD_LINK_RE.finditer(line):
            out.append((m.group(1), m.group(2), offset + m.start(2), offset + m.end(2)))
        # 图片也是站内链接：L-1 同样要求 ./ 或 ../ 前缀，因此一并收集。
        for m in MD_IMAGE_RE.finditer(line):
            out.append((m.group(1), m.group(2), offset + m.start(2), offset + m.end(2)))
    return out


def find_md_links(text: str) -> list[tuple[str, int, int]]:
    return [(t, s, e) for _text, t, s, e in find_md_links_with_text(text)]


# ---------------------------------------------------------------- 链接层


def _within_kb_root(kb_root: Path, rel: str) -> bool:
    norm = posixpath.normpath(rel)
    if norm.startswith('..') or posixpath.isabs(norm):
        return False
    try:
        root = kb_root.resolve()
        candidate = (root / norm).resolve()
    except OSError:
        return False
    return candidate == root or root in candidate.parents


def resolve_wiki(target: str, project: str, kb_root: Path) -> tuple[str, bool]:
    if '/' in target:
        rel = target if target.lower().endswith('.md') else f'{target}.md'
    else:
        rel = f'{project}/{target}.md'
    rel = posixpath.normpath(rel)
    if not _within_kb_root(kb_root, rel):
        return rel, False
    candidate = kb_root / rel
    return rel, candidate.exists() and candidate.is_file()


def resolve_rel_path(src_path: str, href: str) -> tuple[str, bool]:
    path = href.split('#', 1)[0].split('?', 1)[0].strip()
    if not path:
        return '', False
    base = posixpath.dirname(src_path)
    joined = posixpath.normpath(posixpath.join(base, path))
    if joined == '.':
        return '', False
    if joined.startswith('..'):
        return joined, False
    within = True if '/' not in src_path else (joined.split('/', 1)[0] == src_path.split('/', 1)[0])
    return joined, within


def uri_scheme(href: str) -> str:
    try:
        scheme = urlsplit(href).scheme
    except ValueError:
        return ''
    if len(scheme) <= 1:
        return ''
    return scheme


def classify(href: str, *, project: str, kb_root: Path, src_path: str | None = None) -> dict:
    if href.startswith('[[') and href.endswith(']]'):
        inner = href[2:-2].strip()
        target, exists = resolve_wiki(inner, project, kb_root)
        return {'type': 'wiki', 'target': target, 'text': inner, 'broken': not exists}
    if href.startswith(('http://', 'https://', 'mailto:')) or uri_scheme(href):
        return {'type': 'abs', 'target': href, 'text': href, 'broken': False}
    if href.startswith('./') or href.startswith('../'):
        if src_path:
            target, within = resolve_rel_path(src_path, href)
            if not within:
                return {'type': 'rel', 'target': target, 'text': href, 'broken': True}
            exists = (kb_root / target).is_file()
            return {'type': 'rel', 'target': target, 'text': href, 'broken': not exists}
        return {'type': 'rel', 'target': href, 'text': href, 'broken': False}
    return {'type': 'other', 'target': href, 'text': href, 'broken': False}


def extract_links(html_or_md: str, *, project: str, kb_root: Path, src_path: str | None = None) -> list[dict]:
    seen = set()
    out: list[dict] = []
    scan_text = strip_fenced_blocks(html_or_md)
    for m in HREF_RE.finditer(scan_text):
        href = m.group(1)
        if href in seen:
            continue
        seen.add(href)
        out.append(classify(href, project=project, kb_root=kb_root, src_path=src_path))
    for target, _start, _end in find_md_links(html_or_md):
        if target in seen:
            continue
        seen.add(target)
        out.append(classify(target, project=project, kb_root=kb_root, src_path=src_path))
    for m in WIKI_RE.finditer(scan_text):
        target = m.group(1).strip()
        key = f'[[{target}]]'
        if key in seen:
            continue
        seen.add(key)
        rel, exists = resolve_wiki(target, project, kb_root)
        out.append({'type': 'wiki', 'target': rel, 'text': target, 'broken': not exists})
    return out


# ---------------------------------------------------------------- 文档层


def _heading_text(raw: str) -> str:
    return CLOSING_HASH_RE.sub('', ' ' + raw).strip()


def _is_date(value: str) -> bool:
    if not DATE_RE.match(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _heading_positions(body: str) -> list[tuple[int, int, str]]:
    out: list[tuple[int, int, str]] = []
    for i, line in enumerate(body.split('\n')):
        m = HEADING_RE.match(line)
        if m:
            out.append((i, len(m.group(2)), _heading_text(m.group(3))))
    return out


def _is_mechanism(rel: str) -> bool:
    """`_` 前缀路径段 = 机制目录（批次 3：MCP 语义写提案落 `_proposals/`）。

    与 rm validator 同口径：机制目录仍被索引展示，但豁免全部规范检查
    （S/M/H/L 组）——提案文件有自己的元数据形态，不应被规则打回。
    点前缀隐藏目录本来就不进收集面，无需处理。"""
    return any(seg.startswith('_') for seg in rel.split('/'))


def load_project(kb_root: Path, project: str) -> Project:
    root = kb_root / project
    docs: dict[str, Doc] = {}
    readme: str | None = None
    files: list[str] = []
    dirs: list[str] = []
    if root.is_dir():
        # S-2/S-3/S-4 要覆盖非 .md 文件与目录，因此单独收集全部条目；
        # 只有 .md 会进入 docs 并参与 M/H/L 组检查。
        # `_` 前缀机制目录（`_proposals/` 等）双侧统一豁免。
        for entry in sorted(root.rglob('*')):
            rel = entry.relative_to(root).as_posix()
            if _is_mechanism(rel):
                continue
            if entry.is_dir():
                dirs.append(rel)
            elif entry.is_file():
                files.append(rel)
        paths = [p for p in root.rglob('*.md') if p.is_file()]
        for path in sorted(paths, key=lambda p: p.relative_to(root).as_posix()):
            rel = path.relative_to(root).as_posix()
            if _is_mechanism(rel):
                continue
            try:
                text = path.read_text(encoding='utf-8')
            except UnicodeDecodeError as exc:
                raise KbFileDecodeError(project, rel, path, exc) from exc
            frontmatter, raw_body = parse_frontmatter(text)
            body = strip_fenced_blocks(raw_body)
            body_offset = len(text.split('\n')) - len(raw_body.split('\n'))
            docs[rel] = Doc(
                rel=rel,
                path=path,
                text=text,
                frontmatter=frontmatter,
                raw_body=raw_body,
                body=body,
                headings=[(lvl, t) for _i, lvl, t in _heading_positions(body)],
                body_offset=body_offset,
            )
            if rel == README:
                readme = rel
    return Project(
        name=project,
        root=root,
        kb_root=kb_root,
        docs=docs,
        readme=readme,
        files=files,
        dirs=dirs,
    )


def _content_docs(project: Project) -> list[Doc]:
    return [doc for rel, doc in sorted(project.docs.items()) if rel != README]


# ---------------------------------------------------------------- S 结构


def check_s1(project: Project) -> list[Finding]:
    if project.readme is not None:
        return []
    return [Finding('S1', ERROR, project.name, '项目缺少 README.md 索引页')]


def check_s2(project: Project) -> list[Finding]:
    out: list[Finding] = []
    # 根目录只允许 README.md：任何其它文件（含 notes.txt、logo.png 等）都是错误。
    for rel in sorted(project.files):
        if rel == README or '/' in rel:
            continue
        out.append(Finding('S2', ERROR, rel, f'项目根下只允许 README.md，其他文件必须放进分类目录：{rel}'))
    return out


def check_s3(project: Project) -> list[Finding]:
    out: list[Finding] = []
    # 目录与非 .md 文件也参与路径字符判定。
    for rel in sorted(set(project.files) | set(project.dirs)):
        bad: list[str] = []
        for seg in rel.split('/'):
            for ch in seg:
                if ch in BANNED_PATH_CHARS and ch not in bad:
                    bad.append(ch)
        if bad:
            shown = '、'.join(repr(ch) for ch in bad)
            out.append(Finding('S3', ERROR, rel, f'路径含非法字符 {shown}：{rel}'))
    return out


def check_s4(project: Project) -> list[Finding]:
    out: list[Finding] = []
    # 分类目录逐个检查各层名字；文件只查自身文件名（父目录已单独检查）。
    for rel in sorted(project.dirs):
        problems = [
            f'分类目录 {seg}'
            for seg in rel.split('/')
            if not NUMBER_PREFIX_RE.match(seg)
        ]
        if problems:
            out.append(Finding('S4', WARNING, rel, f'{"、".join(problems)} 建议用 NN- 前缀（如 01-）'))
    for rel in sorted(project.files):
        if rel == README:
            continue
        name = rel.rsplit('/', 1)[-1]
        if not NUMBER_PREFIX_RE.match(name):
            out.append(Finding('S4', WARNING, rel, f'文档名 {name} 建议用 NN- 前缀（如 01-）'))
    return out


# ---------------------------------------------------------------- M 元数据


def check_m1(project: Project) -> list[Finding]:
    """frontmatter 必须存在，且为扁平标量：每行恰好一个「键: 值」。"""
    out: list[Finding] = []
    for doc in _content_docs(project):
        lines = doc.text.split('\n')
        if not doc.frontmatter:
            out.append(Finding('M1', ERROR, doc.rel, '文档开头缺少 frontmatter 块'))
            continue
        bounds = _frontmatter_bounds(doc.text)
        if bounds is None:
            continue
        start, end = bounds
        for idx in range(start, end):
            line = lines[idx]
            lineno = idx + 1
            if not line.strip():
                out.append(Finding('M1', ERROR, doc.rel, f'frontmatter 第 {lineno} 行是空行；每行必须是「键: 值」'))
                continue
            if line[0] in ' \t':
                out.append(Finding('M1', ERROR, doc.rel, f'frontmatter 第 {lineno} 行缩进；不支持嵌套、列表与多行值'))
                continue
            stripped = line.strip()
            if stripped.startswith('- ') or stripped == '-':
                out.append(Finding('M1', ERROR, doc.rel, f'frontmatter 第 {lineno} 行是列表项；只允许扁平标量'))
                continue
            if line.count(': ') != 1:
                out.append(Finding(
                    'M1', ERROR, doc.rel,
                    f'frontmatter 第 {lineno} 行不是「键: 值」；需要恰好一个半角冒号加一个空格',
                ))
                continue
            key, value = line.split(': ', 1)
            if not key.strip() or not value.strip():
                out.append(Finding('M1', ERROR, doc.rel, f'frontmatter 第 {lineno} 行的键或值为空'))
    return out


def check_m2(project: Project) -> list[Finding]:
    out: list[Finding] = []
    for doc in _content_docs(project):
        if not doc.frontmatter:
            continue
        missing = [key for key in REQUIRED_KEYS if key not in doc.frontmatter]
        if missing:
            out.append(Finding('M2', ERROR, doc.rel, f'缺 frontmatter 必填键：{"、".join(missing)}'))
    return out


def check_m3(project: Project) -> list[Finding]:
    out: list[Finding] = []
    for doc in _content_docs(project):
        if not doc.frontmatter:
            continue
        priority = doc.frontmatter.get('优先级')
        if priority is not None and priority not in PRIORITIES:
            allowed = '、'.join(sorted(PRIORITIES))
            out.append(Finding('M3', ERROR, doc.rel, f'优先级取值非法：{priority}（应为 {allowed}）'))
        updated = doc.frontmatter.get('最后更新')
        if updated is not None and not _is_date(updated):
            out.append(Finding('M3', ERROR, doc.rel, f'最后更新不是可解析的 YYYY-MM-DD 日期：{updated}'))
    return out


def check_m3a(project: Project) -> list[Finding]:
    out: list[Finding] = []
    for doc in _content_docs(project):
        if not doc.frontmatter:
            continue
        status = doc.frontmatter.get('状态')
        if status is None:
            continue
        m = STATUS_RE.match(status)
        base = (m.group(1) if m else status).strip()
        if base not in STATUS_BASES:
            allowed = '、'.join(sorted(STATUS_BASES))
            out.append(Finding('M3a', ERROR, doc.rel, f'状态基准值非法：{base}（应为 {allowed}）'))
    return out


def check_m3b(project: Project) -> list[Finding]:
    out: list[Finding] = []
    for doc in _content_docs(project):
        if not doc.frontmatter:
            continue
        review = doc.frontmatter.get('下次 review')
        if review is None:
            continue
        if review != '按需' and not _is_date(review):
            out.append(Finding(
                'M3b', ERROR, doc.rel,
                f'下次 review 既不是可解析的 YYYY-MM-DD 日期也不是「按需」：{review}',
            ))
    return out


def check_m4(project: Project) -> list[Finding]:
    out: list[Finding] = []
    today = date.today()
    for doc in _content_docs(project):
        if not doc.frontmatter:
            continue
        review = doc.frontmatter.get('下次 review')
        if review is None or not _is_date(review):
            continue
        due = date.fromisoformat(review)
        if due < today:
            out.append(Finding('M4', WARNING, doc.rel, f'下次 review 已过复审期：{review}'))
    return out


# ---------------------------------------------------------------- H 标题


def check_h1(project: Project) -> list[Finding]:
    out: list[Finding] = []
    for rel in sorted(project.docs):
        doc = project.docs[rel]
        count = sum(1 for lvl, _text in doc.headings if lvl == 1)
        if count != 1:
            out.append(Finding('H1', ERROR, rel, f'每篇文档必须恰好一个 H1，实际 {count} 个'))
    return out


def check_h2(project: Project) -> list[Finding]:
    out: list[Finding] = []
    for rel in sorted(project.docs):
        doc = project.docs[rel]
        prev: int | None = None
        for lvl, text in doc.headings:
            if prev is None:
                # 首个标题也参与层级判定：文档必须以 H1 开头，否则视为跳级。
                if lvl != 1:
                    out.append(Finding('H2', ERROR, rel, f'首个标题必须是 H1，实际是 H{lvl}「{text}」'))
            elif lvl - prev > 1:
                out.append(Finding(
                    'H2', ERROR, rel,
                    f'标题层级跳级：H{prev} 之后直接出现 H{lvl}「{text}」',
                ))
            prev = lvl
    return out


def check_h3(project: Project) -> list[Finding]:
    """相邻同级标题之间必须有正文。

    只比较“相邻同级”标题对：``## A`` → ``### B``（无正文）→ ``## C``
    这类夹着子标题的缺正文写法抓不到——A 与 C 之间隔着 B，不算相邻同级；
    B 与 A 不同级，也不在比较范围。这是规范写明的已知盲区，不是实现遗漏。
    """
    out: list[Finding] = []
    for rel in sorted(project.docs):
        doc = project.docs[rel]
        positions = _heading_positions(doc.body)
        raw_lines = doc.raw_body.split('\n')
        for (i1, lvl1, t1), (i2, lvl2, t2) in zip(positions, positions[1:]):
            if lvl1 != lvl2:
                continue
            between = raw_lines[i1 + 1:i2]
            if any(line.strip() for line in between):
                continue
            out.append(Finding(
                'H3', ERROR, rel,
                f'同级标题之间没有正文：H{lvl1}「{t1}」与 H{lvl2}「{t2}」',
            ))
    return out


def check_h4(project: Project) -> list[Finding]:
    out: list[Finding] = []
    for rel in sorted(project.docs):
        doc = project.docs[rel]
        for lvl, text in doc.headings:
            if HEADING_LINK_RE.search(text):
                out.append(Finding('H4', ERROR, rel, f'标题里不能放 markdown 链接：H{lvl}「{text}」'))
    return out


def check_h5(project: Project) -> list[Finding]:
    out: list[Finding] = []
    for rel in sorted(project.docs):
        doc = project.docs[rel]
        counts: dict[tuple[int, str], int] = {}
        for lvl, text in doc.headings:
            counts[(lvl, text)] = counts.get((lvl, text), 0) + 1
        for (lvl, text), count in counts.items():
            if count > 1:
                out.append(Finding(
                    'H5', WARNING, rel,
                    f'同级标题文字重复：H{lvl}「{text}」出现了 {count} 次',
                ))
    return out


# ---------------------------------------------------------------- L 链接


def _doc_links(project: Project, doc: Doc) -> list[dict]:
    return extract_links(
        doc.raw_body,
        project=project.name,
        kb_root=project.kb_root,
        src_path=f'{project.name}/{doc.rel}',
    )


def _link_path(target: str) -> str:
    return target.split('#', 1)[0].split('?', 1)[0]


def _resolve_in_project(doc_rel: str, target: str) -> str:
    return posixpath.normpath(posixpath.join(posixpath.dirname(doc_rel), target))


LINK_SKIP = 'skip'
LINK_MISSING = 'missing'
LINK_ESCAPED = 'escaped'


@dataclass(frozen=True)
class LinkStatus:
    status: str
    resolved: str = ''


def _link_target_status(project: Project, doc_rel: str, href: str) -> LinkStatus:
    path = _link_path(href)
    if not path:
        return LinkStatus(LINK_SKIP)
    if '://' in path:
        return LinkStatus(LINK_SKIP)
    kind = classify(
        path,
        project=project.name,
        kb_root=project.kb_root,
        src_path=f'{project.name}/{doc_rel}',
    )['type']
    if kind == 'abs':
        return LinkStatus(LINK_SKIP)
    if path.startswith('/'):
        return LinkStatus(LINK_SKIP)
    if not path.endswith('.md'):
        return LinkStatus(LINK_SKIP)
    resolved = _resolve_in_project(doc_rel, path)
    if resolved.startswith('..'):
        return LinkStatus(LINK_ESCAPED, resolved)
    if resolved in project.docs:
        return LinkStatus(LINK_SKIP)
    return LinkStatus(LINK_MISSING, resolved)


def _link_graph(project: Project) -> tuple[dict[str, set[str]], dict[str, int]]:
    edges: dict[str, set[str]] = {rel: set() for rel in project.docs}
    prefix = project.name + '/'
    for rel in sorted(project.docs):
        doc = project.docs[rel]
        for link in _doc_links(project, doc):
            if link['type'] != 'rel' or link['broken']:
                continue
            target = link['target']
            if not target.startswith(prefix):
                continue
            inner = target[len(prefix):]
            if inner not in project.docs:
                continue
            edges[rel].add(inner)
    indegree: dict[str, int] = {rel: 0 for rel in project.docs}
    for targets in edges.values():
        for dst in targets:
            indegree[dst] += 1
    return edges, indegree


def check_l1(project: Project) -> list[Finding]:
    out: list[Finding] = []
    for rel in sorted(project.docs):
        doc = project.docs[rel]
        for link in _doc_links(project, doc):
            if link['type'] != 'other':
                continue
            target = link['target']
            # 纯锚点与协议相对地址不是站内文档链接，不要求 ./ 前缀。
            if target.startswith('#') or target.startswith('//'):
                continue
            out.append(Finding('L1', ERROR, rel, f'站内链接必须以 ./ 或 ../ 开头：{link["text"]}'))
    return out


def check_l1a(project: Project) -> list[Finding]:
    out: list[Finding] = []
    for rel in sorted(project.docs):
        doc = project.docs[rel]
        for link in _doc_links(project, doc):
            if link['type'] != 'wiki':
                continue
            out.append(Finding('L1a', ERROR, rel, f'不使用 [[wiki]] 语法：[[{link["text"]}]]'))
    return out


def check_l2(project: Project) -> list[Finding]:
    out: list[Finding] = []
    for rel in sorted(project.docs):
        doc = project.docs[rel]
        for link in _doc_links(project, doc):
            if link['type'] != 'rel':
                continue
            status = _link_target_status(project, rel, link['text'])
            if status.status == LINK_MISSING:
                out.append(Finding(
                    'L2', ERROR, rel,
                    f'站内链接目标不存在：{link["text"]}（归一为 {status.resolved}）',
                ))
            elif status.status == LINK_ESCAPED:
                out.append(Finding(
                    'L2', ERROR, rel,
                    f'站内链接目标越出项目根：{link["text"]}（归一为 {status.resolved}）',
                ))
    return out


def check_l3(project: Project) -> list[Finding]:
    if project.readme is None:
        return []
    edges, _indegree = _link_graph(project)
    seen = {project.readme}
    stack = [project.readme]
    while stack:
        for nxt in edges.get(stack.pop(), ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    out: list[Finding] = []
    for rel in sorted(project.docs):
        if rel in seen:
            continue
        out.append(Finding('L3', ERROR, rel, f'无法从 README.md 沿相对链接到达：{rel}'))
    return out


def check_l4(project: Project) -> list[Finding]:
    edges, indegree = _link_graph(project)
    out: list[Finding] = []
    for rel in sorted(project.docs):
        if rel == README:
            continue
        if indegree[rel] or edges[rel]:
            continue
        out.append(Finding('L4', WARNING, rel, f'孤儿文档：既没有入边也没有出边（{rel}）'))
    return out


def _related_target_missing(project: Project, doc_rel: str, target: str) -> str | None:
    status = _link_target_status(project, doc_rel, target)
    if status.status != LINK_MISSING:
        return None
    return status.resolved


@dataclass(frozen=True)
class RelatedSection:
    heading_index: int
    start: int
    end: int
    lines: tuple[str, ...] = ()


def find_related_section(text: str) -> RelatedSection | None:
    stripped = strip_fenced_blocks(text)
    positions = _heading_positions(stripped)
    raw_lines = text.split('\n')
    for idx, (i, lvl, heading_text) in enumerate(positions):
        if lvl != 2 or heading_text != RELATED_SECTION:
            continue
        end = len(raw_lines)
        for i2, lvl2, _t2 in positions[idx + 1:]:
            if lvl2 <= 2:
                end = i2
                break
        return RelatedSection(
            heading_index=i,
            start=i + 1,
            end=end,
            lines=tuple(raw_lines[i + 1:end]),
        )
    return None


def related_section_is_bullets(lines: tuple[str, ...] | list[str]) -> bool:
    return all(not line.strip() or RELATED_BULLET_RE.match(line) for line in lines)


def check_l5(project: Project) -> list[Finding]:
    out: list[Finding] = []
    for rel in sorted(project.docs):
        doc = project.docs[rel]
        positions = _heading_positions(doc.body)
        raw_lines = doc.raw_body.split('\n')
        for idx, (i, lvl, text) in enumerate(positions):
            if text != RELATED_SECTION:
                continue
            if lvl != 2:
                out.append(Finding(
                    'L5', ERROR, rel,
                    f'「{RELATED_SECTION}」小节第 {doc.file_line(i)} 行必须是 '
                    f'## {RELATED_SECTION}，实际是 H{lvl}',
                ))
                continue
            end = len(raw_lines)
            for i2, lvl2, _t2 in positions[idx + 1:]:
                if lvl2 <= 2:
                    end = i2
                    break
            for offset, line in enumerate(raw_lines[i + 1:end]):
                if not line.strip():
                    continue
                lineno = doc.file_line(i + 1 + offset)
                if not RELATED_BULLET_RE.match(line):
                    out.append(Finding(
                        'L5', ERROR, rel,
                        f'「{RELATED_SECTION}」小节内容必须是 bullet list，第 {lineno} 行不是',
                    ))
                    continue
                m = RELATED_TARGET_RE.search(line)
                target = m.group(1) if m else ''
                missing = _related_target_missing(project, rel, target)
                if missing is not None:
                    out.append(Finding(
                        'L5', ERROR, rel,
                        f'「{RELATED_SECTION}」小节第 {lineno} 行指向不存在的项目文档：{missing}',
                    ))
    return out


def check_l6(project: Project) -> list[Finding]:
    out: list[Finding] = []
    for rel in sorted(project.docs):
        doc = project.docs[rel]
        for text, target, _start, _end in find_md_links_with_text(doc.raw_body):
            shown = text.strip()
            if shown not in VAGUE_LINK_TEXTS:
                continue
            out.append(Finding('L6', WARNING, rel, f'链接文字太笼统：[{shown}]({target})'))
    return out


# ---------------------------------------------------------------- T 文本


def _strip(pattern: str, text: str, repl: str = '', flags: int = 0) -> str:
    return re.sub(pattern, lambda m: repl + '\n' * m.group(0).count('\n'), text, flags=flags)


def _prose(text: str) -> str:
    text = strip_fenced_blocks(text)
    text = _strip(INLINE_CODE_RE, text)
    text = _strip(r'<!--.*?-->', text, flags=re.S)
    text = _strip(r'\]\([^)]*\)', text, repl=']()')
    text = _strip(BARE_URL_RE, text)
    text = _strip(r'《[^》]*》', text)
    return text


def _prose_lines(doc: Doc) -> list[str]:
    return _prose(doc.raw_body).split('\n')


def check_t1(project: Project) -> list[Finding]:
    out: list[Finding] = []
    for rel in sorted(project.docs):
        doc = project.docs[rel]
        for index, line in enumerate(_prose_lines(doc)):
            if '！' not in line and not CJK_BANG_RE.search(line):
                continue
            out.append(Finding('T1', WARNING, rel, f'第 {doc.file_line(index)} 行有感叹号，语气过于强烈'))
    return out


def check_t2(project: Project) -> list[Finding]:
    out: list[Finding] = []
    for rel in sorted(project.docs):
        doc = project.docs[rel]
        for index, line in enumerate(_prose_lines(doc)):
            hit = next((p for p in VAGUE_PROSE_TEXTS if p in line), None)
            if hit is None:
                continue
            out.append(Finding('T2', WARNING, rel, f'第 {doc.file_line(index)} 行有「{hit}」这类无信息指代'))
    return out


def check_t3(project: Project) -> list[Finding]:
    out: list[Finding] = []
    for rel in sorted(project.docs):
        doc = project.docs[rel]
        for index, line in enumerate(_prose_lines(doc)):
            hit = next((p for p in FILLER_PHRASES if p in line), None)
            if hit is None:
                continue
            out.append(Finding('T3', WARNING, rel, f'第 {doc.file_line(index)} 行有填充语「{hit}」'))
    return out


def check_t4(project: Project) -> list[Finding]:
    out: list[Finding] = []
    for rel in sorted(project.docs):
        if rel == README:
            continue
        doc = project.docs[rel]
        h1 = next((i for i, lvl, _t in _heading_positions(doc.body) if lvl == 1), None)
        if h1 is None:
            continue
        lines = doc.raw_body.split('\n')
        first = next(
            ((h1 + 1 + k, line) for k, line in enumerate(lines[h1 + 1:]) if line.strip()),
            None,
        )
        if first is not None and first[1].lstrip().startswith('>'):
            continue
        where = f'（第 {doc.file_line(first[0])} 行）' if first else f'（第 {doc.file_line(h1)} 行）'
        out.append(Finding(
            'T4', WARNING, rel,
            f'H1 之后应紧跟一句 > 摘要（一句话说清这篇讲什么）{where}',
        ))
    return out


# ---------------------------------------------------------------- M5 文档代号（批次 2 新增，Q12）

DOC_CODE_RE = re.compile(r'^\d{1,4}-\d{1,4}$')


def doc_code_of(doc: Doc) -> str | None:
    """文档代号：frontmatter `文档代号` 优先；否则从路径机械派生
    （目录首段数字前缀-文件 stem 数字前缀，如 02-硬件/01-x.md → 02-01）。
    任一侧无数字前缀 → None（不硬造代号）。"""
    code = doc.frontmatter.get('文档代号')
    if code is not None:
        return code.strip() or None
    parts = doc.rel.split('/')
    if len(parts) < 2:
        return None
    dir_seg = parts[0]
    stem = parts[-1].rsplit('.', 1)[0]
    dir_num = ''.join(itertools.takewhile(str.isdigit, dir_seg))
    file_num = ''.join(itertools.takewhile(str.isdigit, stem))
    if dir_num and file_num:
        return f'{dir_num}-{file_num}'
    return None


def check_m5(project: Project) -> list[Finding]:
    """M5（错误级，条件规则）：frontmatter 有 `文档代号` 键时，取值必须是
    NN-MM 形态。**键缺失不报**——盲区声明见规范附录：老库渐进补齐，
    强制齐全会把所有历史文档一次打成错误，违背渐进采纳。"""
    out: list[Finding] = []
    for doc in _content_docs(project):
        code = doc.frontmatter.get('文档代号')
        if code is None:
            continue
        code = code.strip()
        if not DOC_CODE_RE.match(code):
            out.append(Finding(
                'M5', ERROR, doc.rel,
                f'文档代号取值非法：{code!r}（应为 NN-MM 数字形态，如 02-01）'))
    return out


# ---------------------------------------------------------------- L7 stem 唯一（批次 2 新增，Q17）

def check_l7(project: Project) -> list[Finding]:
    """L7（警告级）：同项目内文件 stem 不得重复。rm 的链接解析以 stem
    为最后兜底，重名 stem 会让解析结果依赖遍历顺序——这是 Q17 的
    健壮性契约。README 豁免（索引页不是解析目标）。"""
    seen: dict[str, str] = {}
    out: list[Finding] = []
    for rel in sorted(project.docs):
        if rel == README:
            continue
        stem = rel.rsplit('/', 1)[-1].rsplit('.', 1)[0]
        first = seen.setdefault(stem, rel)
        if first != rel:
            out.append(Finding(
                'L7', WARNING, rel,
                f'文件 stem 重复：{stem}（与 {first} 同名，链接解析兜底会二义）'))
    return out


# ---------------------------------------------------------------- L8/L9 依赖矩阵一致性（批次 2 新增，D8-3）

MATRIX_SECTION = '依赖矩阵'
DOC_CODE_CELL_SPLIT = re.compile(r'[、,;，；]')


def _matrix_rows(text: str) -> list[tuple[str, list[str], list[str]]] | None:
    """解析 README 的 `## 依赖矩阵` 小节。返回 [(代号, 依赖代号列表, 被依赖代号列表)]。
    **小节不存在返回 None（规则整体沉默）；存在但表格式不对返回 None 之外的
    空表行为由调用方区分——格式不合法时同样沉默（与 L-5 同哲学：格式严格
    才能享受校验）**。表头必须含 文档/依赖/被依赖 三列。"""
    stripped = strip_fenced_blocks(text)
    positions = _heading_positions(stripped)
    raw_lines = text.split('\n')
    start = end = None
    for idx, (i, lvl, heading_text) in enumerate(positions):
        if lvl == 2 and heading_text == MATRIX_SECTION:
            end = len(raw_lines)
            for i2, lvl2, _t2 in positions[idx + 1:]:
                if lvl2 <= 2:
                    end = i2
                    break
            start = i + 1
            break
    if start is None:
        return None
    rows: list[tuple[str, list[str], list[str]]] = []
    header_seen = False
    doc_col = dep_col = rev_col = -1
    for line in raw_lines[start:end]:
        cells = [c.strip() for c in line.strip().strip('|').split('|')] if line.strip().startswith('|') else []
        if len(cells) < 3:
            continue
        if set(cells[0]) <= {'-', ' ', ':'}:
            continue  # 分隔行
        if not header_seen:
            lowered = [c for c in cells]
            if any('文档' in c for c in lowered) and any('依赖' in c for c in lowered) and any('被依赖' in c for c in lowered):
                for j, c in enumerate(cells):
                    if '文档' in c:
                        doc_col = j
                    elif '被依赖' in c:
                        rev_col = j
                    elif '依赖' in c:
                        dep_col = j
                header_seen = True
            continue
        if max(doc_col, dep_col, rev_col) >= len(cells):
            continue
        def split_codes(cell: str) -> list[str]:
            return [c.strip() for c in DOC_CODE_CELL_SPLIT.split(cell) if c.strip()]
        rows.append((cells[doc_col], split_codes(cells[dep_col]), split_codes(cells[rev_col])))
    if not header_seen:
        return None
    return rows


def _related_codes(project: Project, doc_rel: str) -> set[str]:
    """该文档 `## 相关文档` 小节里链接目标的代号集合（按文档代号解析）。"""
    codes: set[str] = set()
    doc = project.docs.get(doc_rel)
    if doc is None:
        return codes
    section = find_related_section(doc.text)
    if section is None:
        return codes
    for line in section.lines:
        m = re.search(r'\(([^)]+)\)', line)
        if not m:
            continue
        path = _link_path(m.group(1).split('#')[0])
        if not path or '://' in path or not path.endswith('.md'):
            continue
        resolved = _resolve_in_project(doc_rel, path)
        if not resolved.startswith('..') and resolved in project.docs:
            code = doc_code_of(project.docs[resolved])
            if code:
                codes.add(code)
    return codes


def check_l8(project: Project) -> list[Finding]:
    """L8（警告级，条件规则）：README 有 `## 依赖矩阵` 小节时，每行「依赖」列
    的代号必须 ⊆ 该文档 `## 相关文档` 小节的链接目标（D8-3：依赖是相关文档
    的子集——两种关系不同时成立才报警）。"""
    rows = _matrix_rows(project.docs[project.readme].text if project.readme in project.docs
                        else _readme_text(project))
    if not rows:
        return []
    code_to_rel: dict[str, str] = {}
    for rel in project.docs:
        code = doc_code_of(project.docs[rel])
        if code:
            code_to_rel.setdefault(code, rel)
    out: list[Finding] = []
    for code, deps, _rev in rows:
        rel = code_to_rel.get(code)
        if rel is None:
            out.append(Finding('L8', WARNING, project.readme or README,
                               f'依赖矩阵行的代号 {code} 找不到对应文档'))
            continue
        related = _related_codes(project, rel)
        for dep in deps:
            if dep not in related:
                out.append(Finding(
                    'L8', WARNING, rel,
                    f'依赖矩阵声明依赖 {dep}，但相关文档小节未链接它（依赖 ⊆ 相关文档）'))
    return out


def check_l9(project: Project) -> list[Finding]:
    """L9（警告级，条件规则）：依赖矩阵的依赖/被依赖两列必须互惠——
    X 的依赖列含 D ⟺ D 的被依赖列含 X。"""
    rows = _matrix_rows(project.docs[project.readme].text if project.readme in project.docs
                        else _readme_text(project))
    if not rows:
        return []
    deps_map: dict[str, set[str]] = {}
    rev_map: dict[str, set[str]] = {}
    for code, deps, rev in rows:
        deps_map.setdefault(code, set()).update(deps)
        rev_map.setdefault(code, set()).update(rev)
    out: list[Finding] = []
    for code, deps in sorted(deps_map.items()):
        for dep in sorted(deps):
            if code not in rev_map.get(dep, set()):
                out.append(Finding(
                    'L9', WARNING, project.readme or README,
                    f'依赖矩阵不互惠：{code} 依赖 {dep}，但 {dep} 的被依赖列没有 {code}'))
    for code, revs in sorted(rev_map.items()):
        for r in sorted(revs):
            if code not in deps_map.get(r, set()):
                out.append(Finding(
                    'L9', WARNING, project.readme or README,
                    f'依赖矩阵不互惠：{code} 被依赖含 {r}，但 {r} 的依赖列没有 {code}'))
    return out


def _readme_text(project: Project) -> str:
    doc = project.docs.get(project.readme) if project.readme else None
    return doc.text if doc else ''



RULES: list[Rule] = [
    Rule('S1', ERROR, check_s1),
    Rule('S2', ERROR, check_s2),
    Rule('S3', ERROR, check_s3),
    Rule('S4', WARNING, check_s4),
    Rule('M1', ERROR, check_m1),
    Rule('M2', ERROR, check_m2),
    Rule('M3', ERROR, check_m3),
    Rule('M3a', ERROR, check_m3a),
    Rule('M3b', ERROR, check_m3b),
    Rule('M4', WARNING, check_m4),
    Rule('H1', ERROR, check_h1),
    Rule('H2', ERROR, check_h2),
    Rule('H3', ERROR, check_h3),
    Rule('H4', ERROR, check_h4),
    Rule('H5', WARNING, check_h5),
    Rule('L1', ERROR, check_l1),
    Rule('L1a', ERROR, check_l1a),
    Rule('L2', ERROR, check_l2),
    Rule('L3', ERROR, check_l3),
    Rule('L4', WARNING, check_l4),
    Rule('L5', ERROR, check_l5),
    Rule('L6', WARNING, check_l6),
    Rule('T1', WARNING, check_t1),
    Rule('T2', WARNING, check_t2),
    Rule('T3', WARNING, check_t3),
    Rule('T4', WARNING, check_t4),
    # 批次 2 新增（kaiwu × rm 合并）：M5 文档代号 / L7 stem 唯一 / L8+L9 依赖矩阵
    Rule('M5', ERROR, check_m5),
    Rule('L7', WARNING, check_l7),
    Rule('L8', WARNING, check_l8),
    Rule('L9', WARNING, check_l9),
]




def check_project(kb_root: Path, project: str) -> list[Finding]:
    project_obj = load_project(kb_root, project)
    findings: list[Finding] = []
    for rule in RULES:
        findings.extend(rule.check(project_obj))
    return findings


def scan_projects(kb_root: Path) -> list[str]:
    if not kb_root.exists():
        return []
    return sorted([p.name for p in kb_root.iterdir() if p.is_dir() and not p.name.startswith('.')])


def projects_in_scope(kb_root: Path, projects: list[str] | None = None) -> list[str]:
    if projects is not None:
        return list(dict.fromkeys(projects))
    return [
        name
        for name in scan_projects(kb_root)
        if not name.startswith('_') and name not in EXEMPT_PROJECTS
    ]


# ---------------------------------------------------------------- CLI


LEVEL_LABELS = {ERROR: 'ERROR', WARNING: 'WARN'}
LEVEL_WIDTH = 5


def _check_project_name_shape(names: list[str]) -> None:
    for name in names:
        if not name or name in ('.', '..') or '/' in name or '\\' in name:
            raise SystemExit(f'项目名不合法：{name!r}——项目名要是一级目录名，不是路径')


def _check_projects_exist(kb_root: Path, names: list[str]) -> None:
    for name in names:
        if not (kb_root / name).is_dir():
            raise SystemExit(f'项目不存在：{name}（在 {kb_root} 下找不到）')


def _print_text(groups: list[tuple[str, list[Finding]]], errors: int, warnings: int) -> None:
    printed = False
    for name, findings in groups:
        if not findings:
            continue
        printed = True
        print(name)
        for f in findings:
            label = LEVEL_LABELS.get(f.level, f.level.upper())
            print(f'  {label:<{LEVEL_WIDTH}}  {f.rule_id}  {f.path}   {f.message}')
    if printed:
        print('')
    print(f'{errors} 个错误，{warnings} 个警告')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='按知识库生成规范校验 kb/ 下的项目。')
    parser.add_argument('--project', action='append', default=[], help='只查指定项目，可重复；不给就扫全库')
    parser.add_argument('--strict', action='store_true', help='warning 也算失败')
    parser.add_argument('--format', choices=['text', 'json'], default='text', help='输出格式')
    parser.add_argument(
        '--root',
        default=str(Path(__file__).resolve().parents[1] / 'kb'),
        help='知识库根目录，默认是仓库下的 kb/',
    )
    options = parser.parse_args(argv)

    kb_root = Path(options.root)
    projects = options.project or None
    names = projects_in_scope(kb_root, projects)
    if projects is not None:
        _check_project_name_shape(names)
    _check_projects_exist(kb_root, names)

    groups: list[tuple[str, list[Finding]]] = []
    try:
        for name in names:
            groups.append((name, check_project(kb_root, name)))
    except KbFileDecodeError as exc:
        print(f'{exc.project} 的文件 {exc.rel} 不是合法 UTF-8，无法解析（{exc.path}）', file=sys.stderr)
        return 1

    findings = [f for _name, group in groups for f in group]
    errors = sum(1 for f in findings if f.level == ERROR)
    warnings = sum(1 for f in findings if f.level == WARNING)

    if options.format == 'json':
        print(json.dumps(
            [
                {
                    'project': name,
                    'findings': [
                        {
                            'rule_id': f.rule_id,
                            'level': f.level,
                            'path': f.path,
                            'message': f.message,
                        }
                        for f in group
                    ],
                }
                for name, group in groups
                if group
            ],
            ensure_ascii=False,
        ))
    else:
        _print_text(groups, errors, warnings)

    if errors:
        return 1
    if options.strict and warnings:
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
