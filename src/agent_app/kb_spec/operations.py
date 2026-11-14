"""项目快照与语义操作规划（规范 §14 作业流程的机械化）。

operations 层是纯函数：输入快照 + 参数，输出 Plan（文件操作列表 + findings），
不做任何 I/O。写盘只发生在 proposals.apply（经审批后）。

强制模式（决策 Q2c）：
- error 级违规 → 计划被拒绝（rejected=True，附带全部 error findings）
- 机械项自动补齐：frontmatter 缺省键、README 同步、移动时全量入链改写、
  状态变更时 `最后更新` 刷新为当日
- 建议/判断级 → findings（warning/review）随提案带给审批者
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date

from .checks_doc import DocContext, check_document, _resolve
from .checks_project import ProjectContext, check_project, _categories, NN_PREFIX_RE
from .docmodel import ParsedDoc, parse_document, count_words
from .vocab import Finding, SEVERITY_ERROR, SEVERITY_WARNING

REQUIRED_KEYS = ("状态", "最后更新", "下次复核")
DEFAULTS = {"状态": "草案", "最后更新": None, "下次复核": "按需"}  # 最后更新默认=当日


@dataclass
class ProjectSnapshot:
    files: dict[str, str] = field(default_factory=dict)   # rel md -> 全文
    all_paths: set[str] = field(default_factory=set)      # 全部文件（含 assets）
    dirs: set[str] = field(default_factory=set)
    today: date = field(default_factory=date.today)
    scope: set[str] = field(default_factory=set)          # 知识库范围（顶层目录名）；空=不限制

    def path_exists(self, rel: str) -> bool:
        return rel in self.all_paths or rel in self.dirs
    def read(self, rel: str) -> str | None:
        return self.files.get(rel)

    def sha_of(self, rel: str) -> str | None:
        content = self.files.get(rel)
        if content is None:
            return None
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def doc_ctx(self, today: date | None = None) -> DocContext:
        # 用 all_paths/dirs 做存在性判定（apply 前的预览快照会包含新文档路径）
        paths = set(self.all_paths) | set(self.dirs)
        return DocContext(path_exists=lambda p: p in paths,
                          today=today or self.today)

    def project_ctx(self) -> ProjectContext:
        docs = {p: parse_document(p, t) for p, t in self.files.items()}
        return ProjectContext(docs=docs, dirs=set(self.dirs),
                              file_paths=set(self.all_paths), today=self.today)


@dataclass
class FileOp:
    op: str                     # write | delete
    path: str
    after: str | None = None    # write: 完整最终内容；delete: None
    before_sha: str | None = None   # 前置条件：当前内容 sha；None 表示文件必须不存在
    # README 增量变更：批量 apply 时若 README 已被其他提案改动，
    # 在「当前 README」上重放该增量而非直接用 after（避免跨文档提案互相冲突）
    patch: dict | None = None


@dataclass
class Plan:
    project: str
    kind: str                   # create|update|move|deprecate|archive|delete
    target: str
    title: str = ""
    reason: str = ""
    ops: list[FileOp] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)   # warning/review 留给审批者
    rejected: bool = False
    reject_findings: list[Finding] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "project": self.project, "kind": self.kind, "target": self.target,
            "title": self.title, "reason": self.reason,
            "ops": [{"op": o.op, "path": o.path, "after": o.after,
                     "before_sha": o.before_sha} for o in self.ops],
            "findings": [f.to_dict() for f in self.findings],
            "rejected": self.rejected,
            "reject_findings": [f.to_dict() for f in self.reject_findings],
        }


# ---------------------------------------------------------------- 工具函数

def _dir_of(rel: str) -> str:
    return rel.rsplit("/", 1)[0] if "/" in rel else ""


def _rel_link(from_dir: str, target: str) -> str:
    """计算从 from_dir 指向 target 的规范相对链接（./ 或 ../ 开头）。"""
    if from_dir == _dir_of(target):
        return "./" + target.rsplit("/", 1)[-1]
    from_parts = from_dir.split("/") if from_dir else []
    to_parts = target.split("/")
    common = 0
    for a, b in zip(from_parts, to_parts):
        if a != b:
            break
        common += 1
    ups = [".."] * (len(from_parts) - common)
    downs = to_parts[common:]
    joined = "/".join(ups + downs)
    return joined if ups else f"./{joined}"


def _summary_of(doc: ParsedDoc) -> str:
    h1 = doc.h1()
    if not h1:
        return ""
    from .checks_doc import _leading_quote_lines
    quotes = _leading_quote_lines(doc, h1.line)
    for _, ln in quotes:
        text = ln.lstrip().lstrip(">").strip()
        if not text.startswith("⚠️"):
            return text
    return ""


def _fill_required_keys(content: str, today: date, rel_path: str = "_tmp") -> tuple[str, ParsedDoc, list[str]]:
    """补齐缺失的必填键；返回 (最终内容, 重新解析的 doc, 补了哪些键)。缺整块不补。"""
    doc = parse_document(rel_path, content)
    if doc.fm is None:
        return content, doc, []
    filled = []
    fm = dict(doc.fm)
    for key in REQUIRED_KEYS:
        if key not in fm:
            fm[key] = today.isoformat() if key == "最后更新" else DEFAULTS[key]
            filled.append(key)
    if not filled:
        return content, doc, []
    from .frontmatter import serialize_frontmatter
    body = doc.lines[doc.body_start - 1:]
    rebuilt = serialize_frontmatter(fm) + "\n" + "\n".join(body)
    return rebuilt, parse_document(rel_path, rebuilt), filled


def _validate_content(snapshot: ProjectSnapshot, path: str, content: str,
                      today: date) -> tuple[ParsedDoc, list[Finding], str]:
    final, doc, _ = _fill_required_keys(content, today, path)
    ctx = snapshot.doc_ctx(today)
    findings = check_document(doc, ctx)
    return doc, findings, final


def _reject(plan: Plan, findings: list[Finding]) -> Plan:
    plan.rejected = True
    plan.reject_findings = [f for f in findings if f.severity == SEVERITY_ERROR]
    plan.findings = [f for f in findings if f.severity != SEVERITY_ERROR]
    return plan


def _readme_entry_line(title: str, path: str, summary: str, status: str = "") -> str:
    """生成 README 条目行；草案/已废弃按规范 §12.1 标注状态。"""
    mark = f"（{status}）" if status in ("草案", "已废弃") else ""
    return f"- [{title}](./{path}) — {summary}{mark}"


def _replay_readme_patch(readme_text: str, patch: dict) -> str:
    """在当前 README 上重放一条条目变更（批量 apply 时 README 已被他人改动）。"""
    action = patch.get("action")
    entry = patch.get("entry", "")
    category = patch.get("category", "")
    if action == "insert":
        return _readme_with_entry(readme_text, category, entry)
    if action == "replace":
        target = patch.get("target", "")
        out = _readme_with_entry(readme_text, category, entry,
                                 replace_target=f"](./{target})")
        # 目标条目不存在（README 被改过）→ 退回插入，避免静默丢失条目
        if f"](./{target})" not in readme_text and f"](./{target})" not in out:
            out = _readme_with_entry(out, category, entry)
        return out
    return readme_text


def _readme_with_entry(readme_text: str, cat: str, entry: str,
                       replace_target: str | None = None) -> str:
    """在 README 的 `## <cat>` 小节插入/替换条目；小节不存在则追加到文末。"""
    lines = readme_text.rstrip("\n").split("\n")
    heading_idx = None
    section_end = None
    for i, ln in enumerate(lines):
        if ln.strip() == f"## {cat}":
            heading_idx = i
        elif heading_idx is not None and re.match(r"^ {0,3}#{1,6}\s", ln):
            section_end = i
            break
    if heading_idx is None:
        block = f"\n## {cat}\n\n{entry}\n"
        return readme_text.rstrip("\n") + "\n" + block
    end = section_end if section_end is not None else len(lines)
    last_bullet = None
    for i in range(heading_idx + 1, end):
        if lines[i].strip().startswith("- "):
            last_bullet = i
    if replace_target:
        for i in range(heading_idx + 1, end):
            if replace_target in lines[i]:
                lines[i] = entry
                return "\n".join(lines) + "\n"
    insert_at = (last_bullet + 1) if last_bullet is not None else heading_idx + 1
    lines.insert(insert_at, entry)
    return "\n".join(lines) + "\n"


def _readme_without_entry(readme_text: str, path: str) -> str:
    lines = readme_text.split("\n")
    out = [ln for ln in lines if not (f"](./{path})" in ln or f"]({path})" in ln)]
    return "\n".join(out)

def _naming_findings_for_path(snapshot: ProjectSnapshot, new_path: str) -> list[Finding]:
    """只针对路径本身的命名规则（§4），用于 move/archive 的目标校验。"""
    out: list[Finding] = []
    for seg in new_path.split("/"):
        if not re.match(r"^[一-鿿A-Za-z0-9.\-]+$", seg):
            out.append(Finding("KB-4.1-charset", SEVERITY_ERROR,
                               f"路径含非法字符：`{seg}`", new_path,
                               "只允许中文、英文、数字、-、.、/"))
        if seg.startswith((".", "~")):
            out.append(Finding("KB-4.1-dot-tilde-prefix", SEVERITY_ERROR,
                               f"以 `.` 或 `~` 开头：`{seg}`", new_path))
    name = new_path.rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0]
    if len(stem) > 60:
        out.append(Finding("KB-4.2-name-length", SEVERITY_ERROR,
                           f"文件名主体 {len(stem)} 字符（上限 60）", new_path))
    if re.search(r"-(v\d+|new|final|latest|最新版|最新|新版|最终版|copy|副本|bak|backup)$", stem, re.IGNORECASE):
        out.append(Finding("KB-4.2-version-suffix", SEVERITY_ERROR,
                           f"文件名使用版本后缀：`{stem}`", new_path))
    if "/" in new_path and not NN_PREFIX_RE.match(stem):
        out.append(Finding("KB-4.2-doc-numbering", SEVERITY_WARNING,
                           f"文档建议 `<NN>-<标题>.md`：`{name}`", new_path))
    return out


def _rewrite_links_after_move(content: str, doc: ParsedDoc, old_path: str,
                              new_path: str) -> str:
    """移动后重写：该文档自己的出链/图片相对层级 + 指向旧路径的入链无（自身内容不含）。"""
    old_dir, new_dir = _dir_of(old_path), _dir_of(new_path)
    lines = content.split("\n")
    targets: list[tuple[str, str]] = []   # (原字面量, 归一目标)
    for lk in doc.links:
        if lk.kind == "doc" and lk.target.startswith(("./", "../")):
            resolved = _resolve(old_dir, lk.target)
            if resolved:
                targets.append((lk.target, resolved))
    for img in doc.images:
        if img.target.startswith(("./", "../")):
            resolved = _resolve(old_dir, img.target)
            if resolved:
                targets.append((img.target, resolved))
    seen = set()
    for literal, resolved in targets:
        if literal in seen:
            continue
        seen.add(literal)
        # 指向旧路径的链接（含自引用）：目标改为新路径
        effective = new_path if resolved == old_path else resolved
        new_literal = _rel_link(new_dir, effective)
        if new_literal != literal:
            for i, ln in enumerate(lines):
                if literal in ln:
                    lines[i] = ln.replace(literal, new_literal)
    return "\n".join(lines)


def _backlink_ops(snapshot: ProjectSnapshot, old_path: str, new_path: str | None,
                  plan: Plan):
    """全库检索旧路径：入链文档逐个改写（§6.2 断链为 0）。new_path=None 表示删除（解链）。"""
    for rel, content in sorted(snapshot.files.items()):
        if rel == old_path or rel == "README.md":
            continue
        doc = parse_document(rel, content)
        base = _dir_of(rel)
        changed = False
        lines = content.split("\n")
        for lk in doc.links:
            if lk.kind != "doc" or not lk.target.startswith(("./", "../")):
                continue
            resolved = _resolve(base, lk.target)
            if resolved != old_path:
                continue
            raw = lines[lk.line - 1]
            if raw is None:
                continue   # 同一行早前已处理（多链接指向同一目标）
            changed = True
            if new_path is None:
                # 删除：相关文档段整行移除，正文链接解包为纯文字
                in_related = doc.related and doc.related.start_line <= lk.line <= doc.related.end_line
                if in_related or re.match(r"^ {0,3}-\s+\[", raw):
                    lines[lk.line - 1] = None
                else:
                    lines[lk.line - 1] = re.sub(r"\[([^\]]+)\]\([^)\s]*\)", r"\1", raw)
            else:
                lines[lk.line - 1] = raw.replace(lk.target, _rel_link(base, new_path))
        if changed:
            final = "\n".join(ln for ln in lines if ln is not None)
            plan.ops.append(FileOp("write", rel, final, snapshot.sha_of(rel)))


# ---------------------------------------------------------------- 六个语义操作

def plan_create(snapshot: ProjectSnapshot, project: str, path: str,
                content: str, today: date | None = None) -> Plan:
    today = today or snapshot.today
    plan = Plan(project=project, kind="create", target=path)
    if path in snapshot.all_paths or path in snapshot.dirs:
        return _reject(plan, [Finding("KB-OP-conflict", SEVERITY_ERROR,
                                      f"目标已存在：`{path}`", path, "改用 kb_propose_update")])
    if path == "README.md":
        return _reject(plan, [Finding("KB-1-root-only-md", SEVERITY_ERROR,
                                      "README 由文档操作的同步机制维护，或用 kb_cli 人工创建；"
                                      "本工具不通过 create 生成", path)])
    # 范围限定：新建文档必须落在知识库范围内，否则不会被校验覆盖
    if snapshot.scope and path.split("/")[0] not in snapshot.scope:
        return _reject(plan, [Finding("KB-OP-conflict", SEVERITY_ERROR,
                                      f"路径不在知识库范围内：`{path}`", path,
                                      f"允许的顶层目录：{'、'.join(sorted(snapshot.scope))}")])
    # 路径命名校验（§4）：非法路径直接拒绝
    naming_errors = [f for f in _naming_findings_for_path(snapshot, path)
                     if f.severity == SEVERITY_ERROR]
    if naming_errors:
        plan.reject_findings = naming_errors
        plan.rejected = True
        return plan
    # 校验时把目标视作已存在（新文档引用自己的链接按自链判定，而不是误报断链）
    preview = ProjectSnapshot(files=dict(snapshot.files),
                              all_paths=set(snapshot.all_paths) | {path},
                              dirs=set(snapshot.dirs), today=today)
    preview.files[path] = content
    doc, findings, final = _validate_content(preview, path, content, today)
    errors = [f for f in findings if f.severity == SEVERITY_ERROR]
    if errors:
        plan.reject_findings = errors
        plan.findings = [f for f in findings if f.severity != SEVERITY_ERROR]
        plan.rejected = True
        return plan
    plan.findings = [f for f in findings if f.severity != SEVERITY_ERROR]
    plan.title = doc.title
    readme = snapshot.read("README.md")
    if readme is None:
        plan.reject_findings = [Finding("KB-2.1-h1", SEVERITY_ERROR,
                                        "项目缺少 README.md，无法同步索引", "README.md")]
        plan.rejected = True
        return plan
    cat = path.split("/")[0]
    entry = _readme_entry_line(doc.title, path, _summary_of(doc),
                               doc.status_base() or "")
    new_readme = _readme_with_entry(readme, cat, entry)
    plan.ops = [
        FileOp("write", path, final, None),
        FileOp("write", "README.md", new_readme, snapshot.sha_of("README.md"),
               patch={"action": "insert", "category": cat, "entry": entry}),
    ]
    # README op 也必须合规（新建草案文档 → README 须标注「草案」，规范 §12.1）
    _validate_readme_op(plan, snapshot)
    return plan


def plan_update(snapshot: ProjectSnapshot, project: str, path: str, content: str,
                expected_sha: str | None = None,
                today: date | None = None) -> Plan:
    today = today or snapshot.today
    plan = Plan(project=project, kind="update", target=path)
    current = snapshot.read(path)
    if current is None:
        return _reject(plan, [Finding("KB-OP-conflict", SEVERITY_ERROR,
                                      f"目标不存在：`{path}`", path,
                                      "改用 kb_propose_create")])
    current_sha = snapshot.sha_of(path)
    if expected_sha is not None and expected_sha != current_sha:
        return _reject(plan, [Finding("KB-OP-sha-conflict", SEVERITY_ERROR,
                                      "expected_sha 与当前内容不一致，文件已被他人修改",
                                      path, "重新读取 kb_get_document 后再提交",
                                      evidence={"expected": expected_sha,
                                                "current": current_sha})])
    doc, findings, final = _validate_content(snapshot, path, content, today)
    errors = [f for f in findings if f.severity == SEVERITY_ERROR]
    # 渐进式修复（Q1）：update 允许部分修复，条件是 error 数不增加
    old_doc_for_count = parse_document(path, current)
    old_findings = check_document(old_doc_for_count, snapshot.doc_ctx(today))
    old_error_count = sum(1 for f in old_findings if f.severity == SEVERITY_ERROR)
    new_error_count = len(errors)
    if new_error_count > old_error_count:
        plan.reject_findings = errors
        plan.findings = [f for f in findings if f.severity != SEVERITY_ERROR]
        plan.rejected = True
        plan.reject_findings.insert(0, Finding(
            "KB-OP-regression", SEVERITY_ERROR,
            f"修复后 error 数从 {old_error_count} 增加到 {new_error_count}，不允许回归",
            path, "确保修复不引入新违规"))
        return plan
    plan.findings = [f for f in findings if f.severity != SEVERITY_ERROR]
    plan.title = doc.title
    # 状态变更 → 最后更新=当日（§12.2 机械保证）
    old_doc = parse_document(path, current)
    if old_doc.status_base() != doc.status_base():
        final, doc, _ = _force_last_updated(final, today)
    plan.ops = [FileOp("write", path, final, current_sha)]
    # README 同步：标题、摘要或状态变化时替换条目行
    # （状态变化要同步 §12.1 的状态标注：草案↔稳定）
    readme = snapshot.read("README.md")
    if readme is not None:
        old_summary, new_summary = _summary_of(old_doc), _summary_of(doc)
        old_title, new_title = old_doc.title, doc.title
        old_status, new_status = old_doc.status_base(), doc.status_base()
        changed = (old_summary != new_summary or old_title != new_title
                   or old_status != new_status)
        if changed and old_title:
            cat = path.split("/")[0]
            entry = _readme_entry_line(new_title or old_title, path,
                                       new_summary or old_summary,
                                       new_status or "")
            new_readme = _readme_with_entry(readme, cat, entry,
                                            replace_target=f"](./{path})")
            if new_readme != readme:
                plan.ops.append(FileOp("write", "README.md", new_readme,
                                       snapshot.sha_of("README.md"),
                                       patch={"action": "replace", "category": cat,
                                              "target": path, "entry": entry}))
    # README op 也必须合规——否则提案落盘后会给全库引入 error（规范 §2.2/§12.1）
    # 校验时把本次变更中所有待写入路径计入存在集合，避免误判断链
    _validate_readme_op(plan, snapshot)
    return plan


def _validate_readme_op(plan: Plan, snapshot: ProjectSnapshot) -> None:
    """校验提案中的 README 变更：error 级违规并入 reject_findings。

    README 的多数规则是项目级（§2 索引覆盖/断链、§12.1 状态标注），只跑
    check_document 会漏掉——必须同时跑 check_project 并只取 README 的发现。

    校验基于「变更后」的项目状态：本次 write op 的新内容覆盖旧内容，
    delete op 的路径移除——否则文档状态已变而校验仍用旧值会误报。
    """
    from .checks_doc import check_document
    from .checks_project import ProjectContext, check_project
    readme_op = next((o for o in plan.ops if o.path == "README.md"), None)
    if readme_op is None or readme_op.after is None:
        return
    readme_doc = parse_document("README.md", readme_op.after)
    # 用「变更后」的项目状态校验：本次 write op 的新内容覆盖旧内容，delete op 移除路径
    docs = {p: parse_document(p, t) for p, t in snapshot.files.items()}
    paths = set(snapshot.all_paths) | set(snapshot.dirs)
    for op in plan.ops:
        if op.op == "write" and op.after is not None:
            docs[op.path] = parse_document(op.path, op.after)
            paths.add(op.path)
        elif op.op == "delete":
            docs.pop(op.path, None)
            paths.discard(op.path)
    docs["README.md"] = readme_doc
    ctx = DocContext(path_exists=lambda p: p in paths, today=snapshot.today)
    errors = [f for f in check_document(readme_doc, ctx) if f.severity == SEVERITY_ERROR]
    # 项目级规则：只保留 README 自身的发现（其他文档的既有问题与本次变更无关）
    proj_ctx = ProjectContext(docs=docs, dirs=set(snapshot.dirs),
                              file_paths=paths, today=snapshot.today)
    for f in check_project(proj_ctx):
        if f.severity != SEVERITY_ERROR:
            continue
        if (f.location or "").startswith("README.md"):
            errors.append(f)
    if errors:
        plan.reject_findings.extend(errors)
        plan.rejected = True


def _force_last_updated(content: str, today: date) -> tuple[str, ParsedDoc, bool]:
    doc = parse_document("_tmp", content)
    if doc.fm is None or "最后更新" not in doc.fm:
        return content, doc, False
    from .frontmatter import serialize_frontmatter
    fm = dict(doc.fm)
    if fm["最后更新"] != today.isoformat():
        fm["最后更新"] = today.isoformat()
        body = doc.lines[doc.body_start - 1:]
        rebuilt = serialize_frontmatter(fm) + "\n" + "\n".join(body)
        return rebuilt, parse_document("_tmp", rebuilt), True
    return content, doc, False


def plan_move(snapshot: ProjectSnapshot, project: str, old_path: str, new_path: str,
              today: date | None = None) -> Plan:
    today = today or snapshot.today
    plan = Plan(project=project, kind="move", target=old_path, reason=f"移动到 {new_path}")
    if snapshot.read(old_path) is None:
        return _reject(plan, [Finding("KB-OP-conflict", SEVERITY_ERROR,
                                      f"源文件不存在：`{old_path}`", old_path)])
    if new_path in snapshot.all_paths:
        return _reject(plan, [Finding("KB-OP-conflict", SEVERITY_ERROR,
                                      f"目标已存在：`{new_path}`", new_path)])
    naming = [f for f in _naming_findings_for_path(snapshot, new_path)
              if f.severity == SEVERITY_ERROR]
    if naming:
        plan.reject_findings = naming
        plan.rejected = True
        return plan
    plan.findings = [f for f in _naming_findings_for_path(snapshot, new_path)
                     if f.severity != SEVERITY_ERROR]
    content = snapshot.read(old_path)
    doc = parse_document(old_path, content)
    moved_content = _rewrite_links_after_move(content, doc, old_path, new_path)
    moved_doc = parse_document(new_path, moved_content)
    plan.title = moved_doc.title
    plan.ops = [FileOp("write", new_path, moved_content, None),
                FileOp("delete", old_path, None, snapshot.sha_of(old_path))]
    # README：条目路径改写
    readme = snapshot.read("README.md")
    if readme is not None:
        summary = _summary_of(moved_doc)
        cat = new_path.split("/")[0]
        entry = _readme_entry_line(moved_doc.title or doc.title, new_path, summary,
                                   moved_doc.status_base() or "")
        new_readme = _readme_with_entry(_readme_without_entry(readme, old_path),
                                        cat, entry)
        if new_readme != readme:
            plan.ops.append(FileOp("write", "README.md", new_readme,
                                   snapshot.sha_of("README.md"),
                                   patch={"action": "replace", "category": cat,
                                          "target": old_path, "entry": entry}))
    _backlink_ops(snapshot, old_path, new_path, plan)
    _validate_readme_op(plan, snapshot)
    return plan


def plan_deprecate(snapshot: ProjectSnapshot, project: str, path: str, reason: str,
                   replacement: str | None = None,
                   today: date | None = None) -> Plan:
    today = today or snapshot.today
    plan = Plan(project=project, kind="deprecate", target=path, reason=reason)
    content = snapshot.read(path)
    if content is None:
        return _reject(plan, [Finding("KB-OP-conflict", SEVERITY_ERROR,
                                      f"目标不存在：`{path}`", path)])
    doc = parse_document(path, content)
    if doc.fm is None:
        return _reject(plan, [Finding("KB-3.1-doc-missing-fm", SEVERITY_ERROR,
                                      "文档缺 frontmatter，无法标记状态", path)])
    line = f"> ⚠️ 已废弃：{reason}；"
    if replacement:
        # replacement 是项目根相对路径（如 01-基础概念/03-插件管理机制.md）
        resolved = replacement
        if resolved not in snapshot.all_paths:
            return _reject(plan, [Finding("KB-12.2-replacement-valid", SEVERITY_ERROR,
                                          f"替代文档不存在：`{replacement}`", path,
                                          "链接到存在的文档，或不传 replacement 写「无替代」")])
        repl_doc = parse_document(resolved, snapshot.files.get(resolved, ""))
        link = _rel_link(_dir_of(path), resolved)
        line += f"替代文档见 [{repl_doc.title or resolved}]({link})。"
    else:
        line += "无替代。"
    from .frontmatter import serialize_frontmatter
    fm = dict(doc.fm)
    fm["状态"] = "已废弃"
    fm["最后更新"] = today.isoformat()
    body = content.split("\n")[doc.body_start - 1:]
    rebuilt = serialize_frontmatter(fm) + "\n" + line + "\n" + "\n".join(body)
    plan.title = doc.title
    plan.ops = [FileOp("write", path, rebuilt, snapshot.sha_of(path))]
    readme = snapshot.read("README.md")
    if readme is not None:
        for i, ln in enumerate(readme.split("\n")):
            if f"](./{path})" in ln and "已废弃" not in ln:
                new_readme = readme.replace(ln, ln.rstrip() + "（已废弃）")
                plan.ops.append(FileOp("write", "README.md", new_readme,
                                       snapshot.sha_of("README.md")))
                break
    _validate_readme_op(plan, snapshot)
    return plan


def plan_archive(snapshot: ProjectSnapshot, project: str, path: str,
                 today: date | None = None) -> Plan:
    today = today or snapshot.today
    plan = Plan(project=project, kind="archive", target=path)
    content = snapshot.read(path)
    if content is None:
        return _reject(plan, [Finding("KB-OP-conflict", SEVERITY_ERROR,
                                      f"目标不存在：`{path}`", path)])
    name = path.rsplit("/", 1)[-1]
    cat = path.split("/")[0] if "/" in path else ""
    new_path = f"archive/{cat}/{name}" if cat else f"archive/{name}"
    sub = plan_move(snapshot, project, path, new_path, today)
    if sub.rejected:
        plan.rejected = True
        plan.reject_findings = sub.reject_findings
        plan.findings = sub.findings
        return plan
    # 状态改 已归档 + 最后更新=当日
    moved = sub.ops[0].after
    doc = parse_document(new_path, moved)
    from .frontmatter import serialize_frontmatter
    fm = dict(doc.fm or {})
    fm["状态"] = "已归档"
    fm["最后更新"] = today.isoformat()
    body = moved.split("\n")[doc.body_start - 1:]
    final = serialize_frontmatter(fm) + "\n" + "\n".join(body)
    sub.ops[0] = FileOp("write", new_path, final, None)
    # README：丢弃 plan_move 造出的（`## archive` 小节）README op，
    # 从原始 README 出发：移除旧条目 → 归档分组追加条目
    readme = snapshot.read("README.md")
    if readme is not None:
        sub.ops = [o for o in sub.ops if o.path != "README.md"]
        final_doc = parse_document(new_path, final)
        entry = _readme_entry_line(final_doc.title or doc.title, new_path,
                                   _summary_of(final_doc),
                                   final_doc.status_base() or "")
        with_group = _append_archive_entry(_readme_without_entry(readme, path), entry)
        sub.ops.append(FileOp("write", "README.md", with_group,
                              snapshot.sha_of("README.md")))
    sub.kind = "archive"
    sub.reason = "归档"
    _validate_readme_op(sub, snapshot)
    return sub


def _append_archive_entry(readme_text: str, entry: str) -> str:
    lines = readme_text.rstrip("\n").split("\n")
    idx = None
    for i, ln in enumerate(lines):
        if ln.strip() == "## 归档":
            idx = i
            break
    if idx is None:
        return readme_text.rstrip("\n") + "\n\n## 归档\n\n" + entry + "\n"
    end = len(lines)
    for i in range(idx + 1, len(lines)):
        if re.match(r"^ {0,3}#{1,6}\s", lines[i]):
            end = i
            break
    insert_at = idx + 1
    for i in range(idx + 1, end):
        if lines[i].strip().startswith("- "):
            insert_at = i + 1
    lines.insert(insert_at, entry)
    return "\n".join(lines) + "\n"


def plan_delete(snapshot: ProjectSnapshot, project: str, path: str, reason: str,
                today: date | None = None) -> Plan:
    plan = Plan(project=project, kind="delete", target=path, reason=reason)
    if path == "README.md":
        return _reject(plan, [Finding("KB-1-root-only-md", SEVERITY_ERROR,
                                      "README 是唯一入口，禁止删除", path)])
    if snapshot.read(path) is None:
        return _reject(plan, [Finding("delete-conflict", SEVERITY_ERROR,
                                      f"目标不存在：`{path}`", path)])
    if not (reason or "").strip():
        return _reject(plan, [Finding("KB-12.2-no-silent-delete", SEVERITY_ERROR,
                                      "禁止静默删除：必须给出原因", path,
                                      "在 reason 中写明删除原因（会随提案留痕）")])
    doc = parse_document(path, snapshot.read(path))
    plan.title = doc.title
    plan.ops = [FileOp("delete", path, None, snapshot.sha_of(path))]
    readme = snapshot.read("README.md")
    if readme is not None:
        new_readme = _readme_without_entry(readme, path)
        if new_readme != readme:
            plan.ops.append(FileOp("write", "README.md", new_readme,
                                   snapshot.sha_of("README.md")))
    _backlink_ops(snapshot, path, None, plan)
    _validate_readme_op(plan, snapshot)
    return plan
