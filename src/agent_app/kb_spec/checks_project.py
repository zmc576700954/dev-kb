"""项目级校验（规范 §1、§2、§6.2 及 §12 归档分组的全库视角规则）。

check_project(ctx) -> list[Finding]
ProjectContext 携带：全部文档解析结果、目录集合、文件集合、当前日期。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .checks_doc import (_dir_of, _leading_quote_lines, _resolve, _strip_md_style,
                         VERSION_SUFFIX_RE, UNINFORMATIVE_WORDS)
from .docmodel import ParsedDoc
from .graph import build_graph
from .vocab import Finding, SEVERITY_ERROR, SEVERITY_REVIEW, SEVERITY_WARNING

NN_PREFIX_RE = re.compile(r"^(\d{2})-")
PATH_ALLOWED_RE = re.compile(r"^[一-鿿A-Za-z0-9.\-]+$")
RESERVED_DIRS = {"assets", "archive"}


@dataclass
class ProjectContext:
    docs: dict[str, ParsedDoc]       # rel_path -> 解析结果（含 README）
    dirs: set[str] = field(default_factory=set)      # 全部目录（相对路径）
    file_paths: set[str] = field(default_factory=set)  # 全部文件（含 assets）
    today: date = field(default_factory=date.today)

    def path_exists(self, rel: str) -> bool:
        return rel in self.file_paths or rel in self.dirs


def _categories(dirs: set[str]) -> list[str]:
    cats = []
    for d in dirs:
        if "/" not in d:
            if d in RESERVED_DIRS or d.startswith(("_", ".", "~")):
                continue
            cats.append(d)
    return sorted(cats)


def _internal_dir(rel: str) -> bool:
    top = rel.split("/", 1)[0]
    return top.startswith("_")


def _check_structure(ctx: ProjectContext, out: list[Finding]):
    md = [p for p in ctx.docs if p != "README.md"]
    root_md = [p for p in md if "/" not in p]
    if root_md:
        out.append(Finding("KB-1-root-only-md", SEVERITY_ERROR,
                           f"项目根只允许 README.md 一个 Markdown 文件，发现：{root_md[:5]}",
                           "README.md", "把文档移入分类目录"))
    cats = _categories(ctx.dirs)
    if not cats:
        out.append(Finding("KB-1-has-category", SEVERITY_ERROR,
                           "项目至少要有一个分类目录", "README.md",
                           "新建 `01-<分类名>/` 并把文档移入"))
    for p in md:
        if p.count("/") > 1:
            out.append(Finding("KB-1-nested-category", SEVERITY_ERROR,
                               f"文档超过「项目/分类/文档」层级：`{p}`", p,
                               "分类目录下不允许再建子分类"))
    for cat in cats:
        children = [p for p in md if p.startswith(cat + "/") and p.count("/") == 1]
        if not children:
            out.append(Finding("KB-1-empty-category", SEVERITY_ERROR,
                               f"空分类目录：`{cat}/`", cat + "/",
                               "放入文档或删除目录"))
        if not (3 <= len(children) <= 15):
            out.append(Finding("KB-1-category-size", SEVERITY_WARNING,
                               f"分类 `{cat}/` 有 {len(children)} 篇文档（建议 3–15）",
                               cat + "/",
                               "超过 15 篇按主题拆分为两个分类" if len(children) > 15 else ""))


def _check_naming(ctx: ProjectContext, out: list[Finding]):
    # 命名检查只针对「知识库路径」：参与校验的文档 + 它们所在的目录。
    # 不能用 ctx.file_paths——那包含项目里的所有文件（.venv/、__pycache__ 等），
    # 它们不是知识库内容，路径里的特殊字符不构成规范违规。
    kb_paths = set(ctx.docs) | set(ctx.dirs)
    for path in sorted(kb_paths):
        if path == "README.md" or _internal_dir(path):
            continue  # `_` 内部目录不参与正式内容，只在被链接时报告（KB-4.1-internal-linked）
        for seg in path.split("/"):
            if seg in (".", "..", ""):
                continue
            if not PATH_ALLOWED_RE.match(seg):
                bad = [c for c in seg if not re.match(r"[一-鿿A-Za-z0-9.\-]", c)]
                out.append(Finding("KB-4.1-charset", SEVERITY_ERROR,
                                   f"路径含非法字符 `{bad[:3]}`：`{seg}`", path,
                                   "只允许中文、英文、数字、-、.、/"))
            if seg.startswith((".", "~")):
                out.append(Finding("KB-4.1-dot-tilde-prefix", SEVERITY_ERROR,
                                   f"以 `.` 或 `~` 开头的名字：`{seg}`", path,
                                   "临时/备份文件不入库"))
    cats = _categories(ctx.dirs)
    for cat in cats:
        if not NN_PREFIX_RE.match(cat):
            out.append(Finding("KB-4.2-category-naming", SEVERITY_WARNING,
                               f"分类目录建议 `<NN>-<分类名>`：`{cat}`", cat + "/"))
    by_cat: dict[str, list[str]] = {}
    for p, doc in ctx.docs.items():
        if p == "README.md" or "/" not in p:
            continue
        cat = p.split("/")[0]
        name = p.rsplit("/", 1)[-1]
        stem = name.rsplit(".", 1)[0]
        by_cat.setdefault(cat, []).append(p)
        if not NN_PREFIX_RE.match(stem):
            out.append(Finding("KB-4.2-doc-numbering", SEVERITY_WARNING,
                               f"文档建议 `<NN>-<标题>.md`：`{name}`", p))
        if len(stem) > 60:
            out.append(Finding("KB-4.2-name-length", SEVERITY_ERROR,
                               f"文件名主体 {len(stem)} 字符（上限 60）：`{stem}`", p))
        if VERSION_SUFFIX_RE.search(stem):
            out.append(Finding("KB-4.2-version-suffix", SEVERITY_ERROR,
                               f"文件名使用版本后缀：`{stem}`", p,
                               "版本通过文档状态与正文变更说明表达"))
        segs = re.split(r"[-_.]", stem.lower())
        if stem.lower() in UNINFORMATIVE_WORDS or any(w in segs for w in UNINFORMATIVE_WORDS):
            out.append(Finding("KB-4.2-uninformative", SEVERITY_ERROR,
                               f"文件名使用无信息词：`{stem}`", p,
                               "文件名描述文档内容"))
        # 文件名大小写：只提示「整个文件名是普通英文单词首字母大写」（如 MyDoc、Note）；
        # 专有名词形态（ComfyUI、SDXL、LoRA、IP-Adapter、Flux 等驼峰或全大写缩写）保留原文
        words = [w for w in re.split(r"[-_.\s]+", stem) if w and not w.isdigit()]
        if words and all(re.fullmatch(r"[A-Z][a-z]+", w) for w in words):
            out.append(Finding("KB-4.2-lowercase", SEVERITY_WARNING,
                               f"文件名英文建议统一小写（普通单词）：`{stem}`", p,
                               "专有名词与缩写保留原文大小写"))
    # 同分类编号唯一（建议级）
    for cat, paths in by_cat.items():
        seen: dict[str, list[str]] = {}
        for p in paths:
            m = NN_PREFIX_RE.match(p.rsplit("/", 1)[-1])
            if m:
                seen.setdefault(m.group(1), []).append(p)
        for nn, ps in seen.items():
            if len(ps) > 1:
                out.append(Finding("KB-4.2-doc-numbering", SEVERITY_WARNING,
                                   f"分类 `{cat}` 内编号 {nn} 重复：{[p.rsplit('/',1)[-1] for p in ps]}",
                                   cat + "/"))
    # 仅大小写差异（macOS 大小写不敏感文件系统上会互相覆盖）
    by_dir: dict[str, dict[str, str]] = {}
    for p in ctx.file_paths:
        d, name = (p.rsplit("/", 1) + [""])[:2] if "/" in p else ("", p)
        key = name.lower()
        if key in by_dir.setdefault(d, {}) and by_dir[d][key] != name:
            out.append(Finding("KB-4.2-case-collide", SEVERITY_ERROR,
                               f"同目录文件名仅大小写不同：`{by_dir[d][key]}` 与 `{name}`", p))
        by_dir[d][key] = name


def _check_readme(ctx: ProjectContext, out: list[Finding]):
    readme = ctx.docs.get("README.md")
    md = [p for p in ctx.docs if p != "README.md" and not _internal_dir(p)]
    if readme is None:
        out.append(Finding("KB-2.1-h1", SEVERITY_ERROR,
                           "项目缺少 README.md（唯一入口）", "README.md"))
        for p in md:
            out.append(Finding("KB-6.2-orphan", SEVERITY_ERROR,
                               f"文档无任何入链（README 缺失）：`{p}`", p))
        return
    h1 = readme.h1()
    first_body = next(((i, ln) for i, ln in enumerate(readme.lines, 1)
                       if i >= readme.body_start and ln.strip()), None)
    if h1 is None or first_body is None or first_body[0] != h1.line:
        out.append(Finding("KB-2.1-h1", SEVERITY_ERROR,
                           "README 第一块必须是一级标题（项目名称）", "README.md:1"))
    quotes = _leading_quote_lines(readme, h1.line) if h1 else []
    if not quotes:
        out.append(Finding("KB-2.1-intro-quote", SEVERITY_ERROR,
                           "H1 后必须紧跟一段 `> ` 项目说明引用", "README.md"))
    if len(readme.lines) > 150:
        out.append(Finding("KB-2.2-length", SEVERITY_WARNING,
                           f"README {len(readme.lines)} 行（建议 150 行以内）", "README.md",
                           "分类过粗或条目说明过长"))
    if readme.code_blocks or readme.tables:
        out.append(Finding("KB-2.2-no-body", SEVERITY_REVIEW,
                           "README 含代码块/表格，疑似堆叠正文", "README.md",
                           "README 只做导航，正文留在文档里"))

    cats = _categories(ctx.dirs)
    h2_names = [h.text for h in readme.headings if h.level == 2]
    for cat in cats:
        if cat not in h2_names:
            out.append(Finding("KB-2.1-grouped-list", SEVERITY_ERROR,
                               f"分类 `{cat}` 没有对应的 README 小节标题", "README.md",
                               "每个分类一个 `## ` 小节，列出该分类全部文档"))
    stripped = {re.sub(NN_PREFIX_RE, "", c): c for c in cats}
    for name in h2_names:
        if name in cats or "归档" in name:
            continue
        if name in stripped:
            out.append(Finding("KB-13-section-name-mismatch", SEVERITY_WARNING,
                               f"README 小节 `{name}` 与分类目录 `{stripped[name]}` 不一致",
                               "README.md", "目录名与小节标题保持一致"))

    # 条目格式与断链
    entry_lines: dict[str, tuple[int, str]] = {}   # resolved -> (行号, 原行)
    for lk in readme.links:
        if lk.kind != "doc":
            continue
        raw = readme.lines[lk.line - 1].strip()
        if lk.target.startswith("/"):
            out.append(Finding("KB-2.1-entry-format", SEVERITY_ERROR,
                               f"README 链接须为项目相对路径：`{lk.target}`",
                               f"README.md:{lk.line}"))
            continue
        if not lk.target.startswith(("./", "../")):
            out.append(Finding("KB-2.1-entry-format", SEVERITY_ERROR,
                               f"README 条目必须 `- [标题](./<category>/<doc>.md) — 说明`：`{raw[:50]}`",
                               f"README.md:{lk.line}"))
            continue
        m = re.match(r"^-\s+\[[^\]]+\]\([^)\s]+\)(\s+—\s*(.*))?$", raw)
        if not m or not (m.group(1) or "").strip(" —-"):
            out.append(Finding("KB-2.1-entry-format", SEVERITY_ERROR,
                               f"README 条目缺「 — 一句话说明」：`{raw[:50]}`",
                               f"README.md:{lk.line}"))
        resolved = _resolve("", lk.target)
        if resolved is None:
            out.append(Finding("KB-2.2-broken-link", SEVERITY_ERROR,
                               f"README 链接越出项目根：`{lk.target}`", f"README.md:{lk.line}"))
            continue
        if resolved.split("#", 1)[0] not in ctx.file_paths:
            out.append(Finding("KB-2.2-broken-link", SEVERITY_ERROR,
                               f"README 链接目标不存在：`{lk.target}`", f"README.md:{lk.line}"))
            continue
        if _internal_dir(resolved):
            out.append(Finding("KB-4.1-internal-linked", SEVERITY_ERROR,
                               f"README 链接到 `_` 开头的内部目录：`{lk.target}`",
                               f"README.md:{lk.line}"))
            continue
        entry_lines.setdefault(resolved, (lk.line, raw))

    # 覆盖率：除 README 与 `_` 内部目录外全部文档必须被链接
    missing = [p for p in md if p not in entry_lines]
    if missing:
        out.append(Finding("KB-2.2-coverage", SEVERITY_ERROR,
                           f"README 有 {len(missing)} 篇文档未收录：{missing[:5]}",
                           "README.md", "每个链接写成 `- [标题](./路径) — 说明`"))

    # 归档分组与状态标注
    archived = [p for p, d in ctx.docs.items()
                if p != "README.md" and d.status_base() == "已归档"]
    has_archive_group = any("归档" in n for n in h2_names)
    if archived and not has_archive_group:
        out.append(Finding("KB-12.2-archive-group", SEVERITY_ERROR,
                           "存在已归档文档但 README 未单列归档分组", "README.md",
                           "加 `## 归档` 小节"))
    for p, doc in ctx.docs.items():
        if p == "README.md" or p not in entry_lines:
            continue
        status = doc.status_base()
        if status == "草案" and "草案" not in entry_lines[p][1]:
            out.append(Finding("KB-12.1-readme-status-mark", SEVERITY_ERROR,
                               f"草案文档未在 README 标注：`{p}`",
                               f"README.md:{entry_lines[p][0]}", "条目说明中标注「草案」"))
        if status == "已废弃" and "已废弃" not in entry_lines[p][1]:
            out.append(Finding("KB-12.1-readme-status-mark", SEVERITY_ERROR,
                               f"已废弃文档未在 README 标注：`{p}`",
                               f"README.md:{entry_lines[p][0]}", "条目说明中标注「已废弃」"))


def _check_graph(ctx: ProjectContext, out: list[Finding]):
    g = build_graph(ctx.docs, ctx.path_exists)
    for p in ctx.docs:
        if p == "README.md":
            continue
        if p not in g.in_links:
            out.append(Finding("KB-6.2-orphan", SEVERITY_ERROR,
                               f"文档没有任何入链（README 也不链接它）：`{p}`", p,
                               "在 README 或相关文档中链接它"))
    return g


def _check_title_overlap(ctx: ProjectContext, out: list[Finding]):
    def norm(title: str) -> str:
        t = re.sub(NN_PREFIX_RE, "", title).strip()
        t = re.sub(r"[（(].*?[)）]$", "", t)
        for w in ("教程", "指南", "详解", "汇总", "大全", "手册", "入门", "笔记", "攻略"):
            t = re.sub(rf"{w}$", "", t)
        return t.strip().lower()

    def bigrams(s: str) -> set[str]:
        s = re.sub(r"\s", "", s)
        return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s}

    paths = sorted(p for p in ctx.docs if p != "README.md")
    docs = ctx.docs
    reported = 0
    for i, a in enumerate(paths):
        for b in paths[i + 1:]:
            ta, tb = norm(docs[a].title), norm(docs[b].title)
            if not ta or not tb:
                continue
            if ta == tb:
                out.append(Finding("KB-8-topic-overlap", SEVERITY_REVIEW,
                                   f"标题完全相同，疑似重复文档：`{docs[a].title}`",
                                   f"{a} + {b}", evidence={"paths": [a, b]},
                                   fix_hint="保留一篇，另一篇改为指向它的短文档或删除"))
                reported += 1
            elif bigrams(ta) & bigrams(tb) and \
                    len(bigrams(ta) & bigrams(tb)) / max(len(bigrams(ta) | bigrams(tb)), 1) >= 0.6:
                out.append(Finding("KB-13-naming-consistency", SEVERITY_REVIEW,
                                   f"标题高度相似，疑似同一概念多种叫法：`{docs[a].title}` vs `{docs[b].title}`",
                                   f"{a} + {b}", evidence={"paths": [a, b]},
                                   fix_hint="同一概念在项目内只用一种叫法"))
                reported += 1
            if reported >= 10:
                return


def check_project(ctx: ProjectContext) -> list[Finding]:
    out: list[Finding] = []
    _check_structure(ctx, out)
    _check_naming(ctx, out)
    _check_readme(ctx, out)
    _check_graph(ctx, out)
    _check_title_overlap(ctx, out)
    return out
