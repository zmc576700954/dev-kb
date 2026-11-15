"""项目注册表：projects.yaml → 名称/路径映射，供各工具使用。"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

DEFAULT_CONFIG = Path(__file__).resolve().parents[3] / "projects.yaml"


def load_registry(config: str | Path | None = None) -> dict[str, dict]:
    cfg = Path(config) if config else DEFAULT_CONFIG
    data = yaml.safe_load(open(cfg, encoding="utf-8"))
    return {p["name"]: p for p in data.get("projects", [])}


def resolve_root(path: str) -> str:
    """展开注册表里的路径：支持 `~` 与环境变量，避免配置里写死绝对路径。"""
    return os.path.expandvars(os.path.expanduser(path))


def list_projects(config: str | Path | None = None) -> list[dict]:
    """工具：列出已登记项目。"""
    return [
        {"name": p["name"], "path": resolve_root(p["path"]), "type": p.get("type", "auto")}
        for p in load_registry(config).values()
    ]


def project_root(name: str, config: str | Path | None = None) -> str | None:
    p = load_registry(config).get(name)
    return resolve_root(p["path"]) if p else None
