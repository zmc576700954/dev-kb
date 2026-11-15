"""本地环境配置的定位与解析。

仓库只分发配置模板副本（``projects.example.yaml``）；真实配置（``projects.yaml``）
含本机项目路径与环境信息，属于本地状态，不入库。

配置来源优先级：
1. 显式传入的路径（``--config`` 或 API 参数）
2. 环境变量 ``DEV_KB_PROJECTS``
3. 仓库根目录下的 ``projects.yaml``
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PROJECTS_FILENAME = "projects.yaml"
PROJECTS_EXAMPLE = "projects.example.yaml"

PROJECTS_ENV = "DEV_KB_PROJECTS"


def _from_env(var: str) -> Path | None:
    value = os.environ.get(var)
    if not value:
        return None
    return Path(os.path.expandvars(os.path.expanduser(value)))


def default_projects_config() -> Path:
    """知识源注册表（含接入契约声明）的默认位置。"""
    return _from_env(PROJECTS_ENV) or ROOT / PROJECTS_FILENAME


def require_config(path: str | Path, example: str, kind: str) -> Path:
    """校验配置存在；缺失时给出可直接照做的提示。

    真实配置不入库，新克隆的仓库必然缺失——这里把「去仓库根建一份」讲清楚，
    而不是让调用方撞一个裸 FileNotFoundError。
    """
    p = Path(path)
    if p.exists():
        return p
    raise FileNotFoundError(
        f"缺少{kind}配置：{p}\n"
        f"该文件记录本机项目路径，不随仓库分发。请从模板副本创建并填入实际路径：\n"
        f"    cp {ROOT / example} {p}"
    )


def load_config(path: str | Path, example: str, kind: str) -> dict:
    """读取 YAML 配置；缺失时输出可照做的提示并退出（供 CLI 直接使用）。"""
    import sys

    import yaml

    try:
        p = require_config(path, example, kind)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from None
    return yaml.safe_load(open(p, encoding="utf-8")) or {}
