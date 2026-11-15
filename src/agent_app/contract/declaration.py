"""知识源声明：知识源接入契约的声明侧模型。

一个知识源 = 一个已登记的项目目录。声明写在 ``projects.yaml`` 的对应条目里，
说明这个源**提供哪些结构字段**、**内容边界在哪**、**中文意图对应哪些路径**、
**哪些文档是什么角色**、**有哪些术语别名**。

检索器按声明消费：声明里没有的字段一律当缺失处理（降级），不拒绝接入。
声明是"这个源长什么样"的显式约定，不是文档质量评分。

注册表里的声明只放**契约**（几十行以内）；调优表（别名 / 意图映射 / 角色）
放按源独立的 ``tuning/*.yaml`` 文件，注册表用 ``tuning:`` 引用。
这样注册表不会随调优膨胀成第二个程序。

约定字段（缺省值见 :meth:`SourceDeclaration.from_entry`）：

============  ============================================================
字段          含义
============  ============================================================
provides      声明本源提供的可选结构字段，取值见 ``OPTIONAL_FIELDS``
content_roots 内容边界（相对项目根）；``"."`` 表示全库
entry_docs    入口文档（相对项目根），用于"这个库整体是什么"类问题
intent_map    中文意图 → 本库路径模式，供检索器做路径提示
roles         路径通配 → 文档角色（index / entry / faq / reference / guide）
terms         术语别名清单文件（相对项目根或注册表目录）
tuning        调优表文件（含 aliases / intent_map / roles 三节）
============  ============================================================
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..tools.projects import load_registry, resolve_root

#: 契约里可选的结构字段。声明与 chunk schema 共用这一份取值。
OPTIONAL_FIELDS = ("heading_path", "category", "doc_role", "aliases")

#: 文档角色取值。
DOC_ROLES = ("index", "entry", "faq", "reference", "guide")


@dataclass
class SourceDeclaration:
    """单个知识源的契约声明。"""

    name: str
    root: str
    type: str = "auto"
    provides: tuple[str, ...] = ()
    content_roots: tuple[str, ...] = ()
    entry_docs: tuple[str, ...] = ()
    terms_files: tuple[str, ...] = ()
    tuning_files: tuple[str, ...] = ()
    extra_exclude_dirs: tuple[str, ...] = ()
    config_dir: str = ""
    _inline_intent: dict = field(default_factory=dict, repr=False)
    _inline_roles: dict = field(default_factory=dict, repr=False)
    _cache: dict = field(default_factory=dict, repr=False)

    # ---- 构造 ----

    @classmethod
    def from_entry(cls, name: str, entry: dict, config_dir: str = "") -> "SourceDeclaration":
        terms = entry.get("terms")
        terms_files = (terms,) if isinstance(terms, str) else tuple(str(t) for t in (terms or ()))
        tuning = entry.get("tuning")
        tuning_files = (tuning,) if isinstance(tuning, str) else tuple(str(t) for t in (tuning or ()))
        inline_intent = {
            str(k): tuple(str(x) for x in (v if isinstance(v, (list, tuple)) else [v]))
            for k, v in (entry.get("intent_map") or {}).items()
        }
        inline_roles = {str(k): str(v) for k, v in (entry.get("roles") or {}).items()}
        return cls(
            name=name,
            root=resolve_root(entry["path"]),
            type=entry.get("type", "auto"),
            provides=tuple(str(p) for p in (entry.get("provides") or ())),
            content_roots=tuple(str(r) for r in (entry.get("content_roots") or ())),
            entry_docs=tuple(str(d) for d in (entry.get("entry_docs") or ())),
            terms_files=terms_files,
            tuning_files=tuning_files,
            extra_exclude_dirs=tuple(str(d) for d in (entry.get("extra_exclude_dirs") or ())),
            config_dir=config_dir,
            _inline_intent=inline_intent,
            _inline_roles=inline_roles,
        )

    # ---- 调优表 ----

    def _load_yaml(self, rel: str) -> dict:
        """按 绝对路径 → 知识源根 → 注册表目录 的顺序找一个 YAML 文件。"""
        candidate = Path(os.path.expandvars(os.path.expanduser(rel)))
        candidates = [candidate] if candidate.is_absolute() else []
        for base in (self.root, self.config_dir):
            if base:
                candidates.append(Path(base) / rel)
        for path in candidates:
            if path.is_file():
                try:
                    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                except (OSError, yaml.YAMLError):
                    return {}
        return {}

    def tuning(self) -> dict:
        """加载调优表：aliases / intent_map / roles。

        调优表文件与注册表内联写法合并，**内联优先**（就近覆盖）。
        """
        if self._cache:
            return self._cache
        aliases: dict[str, list[str]] = {}
        intent: dict[str, tuple[str, ...]] = {}
        roles: dict[str, str] = {}

        def merge_aliases(raw: dict) -> None:
            for term, values in (raw or {}).items():
                if isinstance(values, str):
                    values = [values]
                bucket = aliases.setdefault(str(term), [])
                for v in values:
                    if str(v) not in bucket:
                        bucket.append(str(v))

        def merge_intent(raw: dict, replace: bool = False) -> None:
            for key, values in (raw or {}).items():
                if isinstance(values, str):
                    values = [values]
                if replace:
                    intent[str(key)] = tuple(str(v) for v in values)
                    continue
                bucket = list(intent.get(str(key), ()))
                for v in values:
                    if str(v) not in bucket:
                        bucket.append(str(v))
                intent[str(key)] = tuple(bucket)

        for rel in self.terms_files:
            merge_aliases(self._load_yaml(rel).get("aliases"))
        for rel in self.tuning_files:
            data = self._load_yaml(rel)
            merge_aliases(data.get("aliases"))
            merge_intent(data.get("intent_map"))
            for pattern, role in (data.get("roles") or {}).items():
                roles[str(pattern)] = str(role)

        merge_aliases(self._inline_aliases())
        merge_intent(self._inline_intent, replace=True)
        roles.update(self._inline_roles)

        self._cache = {"aliases": aliases, "intent_map": intent, "roles": roles}
        return self._cache

    def _inline_aliases(self) -> dict:
        return {}

    # ---- 派生视图 ----

    @property
    def intent_map(self) -> dict[str, tuple[str, ...]]:
        return self.tuning()["intent_map"]

    @property
    def roles(self) -> dict[str, str]:
        return self.tuning()["roles"]

    def terms(self) -> dict[str, list[str]]:
        """canonical term → 别名列表。"""
        return self.tuning()["aliases"]

    def alias_surfaces(self) -> list[str]:
        """全部别名表面形式（含 canonical term 本身）。"""
        out: list[str] = []
        for term, aliases in self.terms().items():
            for a in [term, *aliases]:
                if a not in out:
                    out.append(a)
        return out

    def alias_to_canonical(self) -> dict[str, str]:
        """别名表面形式 → canonical term（只含真别名，不含 canonical 自身）。

        用于**查询侧扩展**：用户问"低秩适配"，扩展成"LoRA"再交给正常通道排序。

        canonical 自身不映射到自己——精确词本来就由符号/FTS 通道处理，
        再走一遍术语通道等于给同一批块重复加权。
        """
        out: dict[str, str] = {}
        for term, aliases in self.terms().items():
            for a in aliases:
                if a != term:
                    out.setdefault(a, term)
        return out

    # ---- 能力查询 ----

    def provides_field(self, field_name: str) -> bool:
        """本源是否声明提供某个可选结构字段。"""
        return field_name in self.provides

    def unknown_provides(self) -> list[str]:
        """声明了但不属于契约字段集的名字（R-07 用）。"""
        return [p for p in self.provides if p not in OPTIONAL_FIELDS]

    # ---- 内容边界 ----

    def in_content(self, rel: str) -> bool:
        """相对路径是否落在声明的内容边界内。"""
        if not self.content_roots:
            return False
        rel = rel.replace(os.sep, "/")
        for root in self.content_roots:
            root = root.replace(os.sep, "/").strip("/")
            if root in ("", "."):
                return True
            if rel == root or rel.startswith(root + "/"):
                return True
        return False

    # ---- 文档角色 ----

    def role_of(self, rel: str) -> str:
        """按路径通配匹配文档角色；最长模式优先。"""
        rel = rel.replace(os.sep, "/")
        best, best_len = "", -1
        for pattern, role in self.roles.items():
            if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(rel, f"*/{pattern}"):
                if len(pattern) > best_len:
                    best, best_len = role, len(pattern)
        return best


def load_declarations(config: str | Path | None = None) -> dict[str, SourceDeclaration]:
    """加载全部知识源的声明。"""
    from ..config import default_projects_config, require_config
    cfg = Path(config) if config else default_projects_config()
    try:
        resolved = require_config(cfg, "projects.example.yaml", "项目注册表")
    except FileNotFoundError:
        resolved = cfg
    config_dir = str(resolved.parent)
    return {
        name: SourceDeclaration.from_entry(name, entry, config_dir)
        for name, entry in load_registry(config).items()
    }


def get_declaration(name: str, config: str | Path | None = None) -> SourceDeclaration | None:
    """加载单个知识源的声明。"""
    decls = load_declarations(config)
    return decls.get(name)


# ---- 契约字段的推导（ingestion 与回填共用） --------------------------------

def category_of(rel: str) -> str:
    """分类 = 相对路径的首段；项目根下的文件没有分类。

    这一条对所有知识源通用：代码库自动得到 src / app / docs，
    文档知识库得到 01-业务概览 这类一级目录。取原样不做前缀剥离，
    这样它与 path 子串匹配用的是同一份字面量。
    """
    rel = rel.replace(os.sep, "/").lstrip("./")
    head, sep, _ = rel.partition("/")
    return head if sep else ""


def aliases_in_content(content: str, terms: dict[str, list[str]]) -> list[str]:
    """该块正文里出现的术语及其别名（canonical + 全部别名表面形式）。"""
    if not terms:
        return []
    out: list[str] = []
    for term, aliases in terms.items():
        if term not in content:
            continue
        for surface in [term, *aliases]:
            if surface not in out:
                out.append(surface)
    return out
