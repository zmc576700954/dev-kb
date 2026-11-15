"""M1 只读扫描器：遍历知识源目录，分类文件，识别项目类型，输出扫描报告。

设计约束：
- 只读，不修改任何项目文件。
- 排除规则见 excludes.py；另尊重项目根 .gitignore 的目录级规则与 .agentignore。
- 锁文件不进入索引清单，但作为技术栈事实单独记录。
"""

from __future__ import annotations

import fnmatch
import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .excludes import (
    BINARY_EXTENSIONS,
    EXCLUDE_DIRS,
    EXCLUDE_FILE_PATTERNS,
    MAX_FILE_SIZE,
    PARTIAL_KEEP_DIRS,
)

# ---- 文件分类规则 --------------------------------------------------------

CODE_EXTENSIONS = {
    ".php": "php",
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".rs": "rust",
    ".vue": "vue",
    ".go": "go",
    ".java": "java",
    ".css": "css",
    ".scss": "scss",
    ".html": "html",
    ".sql": "sql",
    ".sh": "shell",
    ".blade.php": "php",
}

DOC_EXTENSIONS = {".md", ".markdown", ".txt", ".rst", ".adoc"}

CONFIG_NAMES = {
    "composer.json", "package.json", "Cargo.toml", "pyproject.toml",
    "go.mod", "pom.xml", "build.gradle", "tsconfig.json",
    "tailwind.config.js", "postcss.config.js", "vite.config.ts",
    "eslint.config.mjs", ".eslintrc", ".eslintrc.json",
    "docker-compose.yml", "Dockerfile", "Makefile",
}
CONFIG_EXTENSIONS = {".toml", ".yaml", ".yml", ".ini", ".cfg"}

LOCK_FILES = {
    "composer.lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "Cargo.lock", "poetry.lock",
}

@dataclass
class FileEntry:
    path: str           # 相对项目根
    category: str       # code | document | config | data | binary | other
    language: str = ""
    size: int = 0


@dataclass
class ProjectReport:
    name: str
    root: str
    project_type: str = "unknown"
    files: list[FileEntry] = field(default_factory=list)
    excluded_dirs: dict[str, int] = field(default_factory=dict)   # 目录名 -> 命中次数
    excluded_files: dict[str, int] = field(default_factory=dict)  # 模式 -> 命中次数
    lock_files: list[str] = field(default_factory=list)
    manifests: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)
    oversize: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _load_ignore_dirs(root: Path) -> tuple[set[str], set[str]]:
    """从 .gitignore / .agentignore 提取目录级排除规则。

    返回 (目录名集合, 相对路径集合)：
    - 目录名：仅简单目录名与 /*/ 形式（如 `baselines`）
    - 相对路径：含 `/` 的路径规则（如 `evals/baselines`），按相对路径精确匹配
    """
    dirs: set[str] = set()
    paths: set[str] = set()
    for name in (".gitignore", ".agentignore"):
        f = root / name
        if not f.is_file():
            continue
        try:
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("!"):
                    continue
                line = line.strip("/")
                if "/" in line and "*" not in line:
                    paths.add(line)
                elif "*" not in line:
                    dirs.add(line)
        except OSError:
            pass
    return dirs, paths


def _in_content_roots(rel: str, content_roots: list[str]) -> bool:
    """相对路径是否落在契约声明的内容边界内。"""
    rel = rel.replace(os.sep, "/").lstrip("./")
    for root in content_roots:
        root = root.replace(os.sep, "/").strip("/")
        if root in ("", "."):
            return True
        if rel == root or rel.startswith(root + "/"):
            return True
    return False


def _classify(rel: str, size: int) -> FileEntry:
    name = os.path.basename(rel)
    lower = rel.lower()
    ext = Path(lower).suffix

    if lower.endswith(".blade.php"):
        return FileEntry(rel, "code", "php", size)
    if name in CONFIG_NAMES or name in LOCK_FILES:
        return FileEntry(rel, "config", "", size)
    if ext in CODE_EXTENSIONS:
        return FileEntry(rel, "code", CODE_EXTENSIONS[ext], size)
    if ext in DOC_EXTENSIONS:
        return FileEntry(rel, "document", "", size)
    if ext in CONFIG_EXTENSIONS:
        return FileEntry(rel, "config", "", size)
    if ext in BINARY_EXTENSIONS:
        return FileEntry(rel, "binary", "", size)
    if ext in {".json", ".jsonl", ".csv"}:
        return FileEntry(rel, "data", "", size)
    return FileEntry(rel, "other", "", size)


def scan_project(name: str, root: str, extra_exclude_dirs: list[str] | None = None,
                 content_roots: list[str] | None = None) -> ProjectReport:
    """扫描一个知识源目录。

    ``content_roots`` 是契约声明的内容边界（相对项目根；``"."`` 表示全库）。
    边界外的文件不进索引清单——边界由声明给出，而不是靠排除名单碰巧躲开。
    """
    root = os.path.expandvars(os.path.expanduser(root))
    root_path = Path(root)
    report = ProjectReport(name=name, root=root)
    if not root_path.is_dir():
        report.errors.append(f"目录不存在: {root}")
        return report

    ignore_dirs, ignore_paths = _load_ignore_dirs(root_path)
    ignore_dirs = EXCLUDE_DIRS | ignore_dirs
    # extra_exclude_dirs 支持两种形式：目录名（basename）与相对路径（含 /）
    extra_names = {d for d in (extra_exclude_dirs or []) if "/" not in d}
    extra_paths = {d.strip("/") for d in (extra_exclude_dirs or []) if "/" in d}
    ignore_dirs |= extra_names
    ignore_paths |= extra_paths

    for dirpath, dirnames, filenames in os.walk(root_path, followlinks=False):
        rel_dir = os.path.relpath(dirpath, root_path)
        parent_name = os.path.basename(dirpath)

        kept = []
        for d in dirnames:
            # 部分保留目录优先放行（如 .superpowers 虽被 .gitignore 排除，但 plans 子目录是 Spec 来源）
            if d in PARTIAL_KEEP_DIRS:
                kept.append(d)
                continue
            # 部分保留目录内部：只放行白名单子目录
            if parent_name in PARTIAL_KEEP_DIRS and d not in PARTIAL_KEEP_DIRS[parent_name]:
                report.excluded_dirs[f"{parent_name}/{d}"] = report.excluded_dirs.get(f"{parent_name}/{d}", 0) + 1
                continue
            # 相对路径规则：命中即整棵子树排除（如 evals/baselines）
            child_rel = d if rel_dir == "." else f"{rel_dir}/{d}"
            if child_rel in ignore_paths:
                report.excluded_dirs[child_rel] = report.excluded_dirs.get(child_rel, 0) + 1
                continue
            if d in ignore_dirs or d.startswith(".") and d in EXCLUDE_DIRS:
                report.excluded_dirs[d] = report.excluded_dirs.get(d, 0) + 1
            else:
                kept.append(d)
        dirnames[:] = kept

        for fname in filenames:
            rel = fname if rel_dir == "." else os.path.join(rel_dir, fname)

            if fname in LOCK_FILES:
                report.lock_files.append(rel)
                continue

            # 契约边界：声明之外的内容不进索引
            if content_roots and not _in_content_roots(rel, content_roots):
                report.excluded_dirs[f"content_roots:{rel_dir}"] = \
                    report.excluded_dirs.get(f"content_roots:{rel_dir}", 0) + 1
                continue

            matched = next(
                (p for p in EXCLUDE_FILE_PATTERNS if fnmatch.fnmatch(fname, p)),
                None,
            )
            if matched:
                report.excluded_files[matched] = report.excluded_files.get(matched, 0) + 1
                continue

            full = os.path.join(dirpath, fname)
            try:
                size = os.path.getsize(full)
            except OSError:
                report.unreadable.append(rel)
                continue

            if size > MAX_FILE_SIZE:
                report.oversize.append(rel)
                continue

            entry = _classify(rel, size)
            if entry.category == "binary":
                continue  # 二进制不计入索引清单
            report.files.append(entry)
            if entry.category == "config" and os.path.basename(rel) in CONFIG_NAMES:
                report.manifests.append(rel)

    report.project_type = _detect_type(report)
    return report


def _detect_type(report: ProjectReport) -> str:
    n_code = sum(1 for f in report.files if f.category == "code")
    n_doc = sum(1 for f in report.files if f.category == "document")
    has_manifest = bool(report.manifests or report.lock_files)
    if n_code == 0 and not has_manifest:
        return "document" if n_doc > 0 else "empty"
    if n_doc >= 3 and (n_code > 0 or has_manifest):
        return "mixed"
    return "code"


def summarize(report: ProjectReport) -> dict:
    by_cat: dict[str, int] = {}
    by_lang: dict[str, int] = {}
    for f in report.files:
        by_cat[f.category] = by_cat.get(f.category, 0) + 1
        if f.language:
            by_lang[f.language] = by_lang.get(f.language, 0) + 1
    return {
        "name": report.name,
        "root": report.root,
        "project_type": report.project_type,
        "file_counts": by_cat,
        "languages": dict(sorted(by_lang.items(), key=lambda kv: -kv[1])),
        "total_indexable_files": len(report.files),
        "manifests": sorted(report.manifests),
        "lock_files": sorted(report.lock_files),
        "excluded_dirs": dict(sorted(report.excluded_dirs.items(), key=lambda kv: -kv[1])),
        "excluded_files": dict(sorted(report.excluded_files.items(), key=lambda kv: -kv[1])),
        "unreadable": report.unreadable,
        "oversize": report.oversize,
        "errors": report.errors,
    }


def report_to_json(report: ProjectReport) -> str:
    data = summarize(report)
    data["files"] = [asdict(f) for f in report.files]
    return json.dumps(data, ensure_ascii=False, indent=2)
