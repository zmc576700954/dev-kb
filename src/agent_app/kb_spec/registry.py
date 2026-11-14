"""KB 项目注册表：kb_projects.yaml → 名称/路径映射（决策 Q6：独立于 projects.yaml）。"""

from __future__ import annotations

from pathlib import Path

import yaml

DEFAULT_CONFIG = Path(__file__).resolve().parents[3] / "kb_projects.yaml"


def load_kb_registry(config: str | Path | None = None) -> dict[str, dict]:
    cfg = Path(config) if config else DEFAULT_CONFIG
    data = yaml.safe_load(open(cfg, encoding="utf-8")) or {}
    return {p["name"]: p for p in data.get("projects", [])}


def kb_project_root(name: str, config: str | Path | None = None) -> str | None:
    p = load_kb_registry(config).get(name)
    return p["path"] if p else None


def kb_paths(name: str, config: str | Path | None = None) -> list[str]:
    """知识库内容范围：项目内哪些顶层目录参与规范校验。

    未配置 → 空列表，表示项目内所有 .md 都纳入。
    配置后 → 只有这些目录下的文档参与（项目根 README 始终参与）。
    """
    p = load_kb_registry(config).get(name)
    if not p:
        return []
    return list(p.get("kb_paths") or [])
