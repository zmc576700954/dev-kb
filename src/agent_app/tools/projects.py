"""项目注册表：projects.yaml → 名称/路径映射，供各工具使用。

配置定位与缺失提示见 agent_app.config。
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from ..config import (
    PROJECTS_EXAMPLE,
    default_projects_config,
    require_config,
)

# 兼容旧引用：默认配置位置（实际解析在调用时进行，见 load_registry）。
DEFAULT_CONFIG = default_projects_config()


def load_registry(config: str | Path | None = None) -> dict[str, dict]:
    # 每次调用重新定位默认配置：环境变量可能在导入之后才设置。
    cfg = require_config(config or default_projects_config(), PROJECTS_EXAMPLE, "项目注册表")
    data = yaml.safe_load(open(cfg, encoding="utf-8"))
    return {p["name"]: p for p in data.get("projects", [])}


def resolve_root(path: str) -> str:
    """展开注册表里的路径：支持 `~` 与环境变量，避免配置里写死绝对路径。"""
    return os.path.expandvars(os.path.expanduser(path))


def list_projects(config: str | Path | None = None) -> list[dict]:
    """工具：列出已登记项目。"""
    return [
        {"name": p["name"], "path": resolve_root(p["path"])}
        for p in load_registry(config).values()
    ]


def project_root(name: str, config: str | Path | None = None) -> str | None:
    p = load_registry(config).get(name)
    return resolve_root(p["path"]) if p else None
