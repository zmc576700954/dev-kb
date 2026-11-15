"""KB 项目注册表：kb_projects.yaml → 名称/路径映射（决策 Q6：独立于 projects.yaml）。

配置定位与缺失提示见 agent_app.config。
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from ..config import (
    KB_PROJECTS_EXAMPLE,
    default_kb_projects_config,
    require_config,
)

# 兼容旧引用：默认配置位置（实际解析在调用时进行，见 load_kb_registry）。
DEFAULT_CONFIG = default_kb_projects_config()


def load_kb_registry(config: str | Path | None = None) -> dict[str, dict]:
    # 每次调用重新定位默认配置：环境变量可能在导入之后才设置。
    cfg = require_config(config or default_kb_projects_config(),
                         KB_PROJECTS_EXAMPLE, "知识库项目注册表")
    data = yaml.safe_load(open(cfg, encoding="utf-8")) or {}
    return {p["name"]: p for p in data.get("projects", [])}


def kb_project_root(name: str, config: str | Path | None = None) -> str | None:
    p = load_kb_registry(config).get(name)
    if not p:
        return None
    return os.path.expandvars(os.path.expanduser(p["path"]))


def kb_paths(name: str, config: str | Path | None = None) -> list[str]:
    """知识库内容范围：项目内哪些顶层目录参与规范校验。

    未配置 → 空列表，表示项目内所有 .md 都纳入。
    配置后 → 只有这些目录下的文档参与（项目根 README 始终参与）。
    """
    p = load_kb_registry(config).get(name)
    if not p:
        return []
    return list(p.get("kb_paths") or [])
