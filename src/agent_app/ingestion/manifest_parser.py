"""项目清单解析：技术栈事实提取。

支持（按五个项目实测）：
- composer.json（PHP/Laravel）
- package.json（Node/TS）
- Cargo.toml（Rust/Tauri）
- .venv site-packages（Python 无 requirements/pyproject 时的 fallback，kaiwu）

原则：每条事实都带来源文件；锁文件不解析细节，只记录存在性。
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# 噪音依赖过滤（类型定义、构建工具等，保留也不报错，仅降噪）
_NOISE_PREFIX = ("@types/", "@typescript-eslint", "eslint", "prettier")


@dataclass
class StackFacts:
    language: str = ""
    framework: str = ""
    runtime: str = ""
    key_dependencies: list[str] = field(default_factory=list)
    dev_tools: list[str] = field(default_factory=list)
    scripts: dict[str, str] = field(default_factory=dict)
    source: str = ""           # 事实来源文件（相对路径）


def _top(deps: dict, limit: int = 18) -> list[str]:
    items = [f"{k} {v}" for k, v in deps.items() if not k.startswith(_NOISE_PREFIX)]
    return sorted(items)[:limit]


def parse_package_json(path: Path, rel: str) -> StackFacts:
    data = json.loads(path.read_text(encoding="utf-8"))
    deps = data.get("dependencies", {})
    dev = data.get("devDependencies", {})
    facts = StackFacts(language="TypeScript/JavaScript", source=rel,
                       scripts=data.get("scripts", {}))
    names = set(deps)
    if "react" in names:
        facts.framework = "React"
    if "vue" in names:
        facts.framework = "Vue"
    if "electron" in names or "electron" in set(dev):
        facts.framework = (facts.framework + " + Electron").strip(" +")
    if "tauri" in str(names) or "@tauri-apps/api" in names:
        facts.framework = (facts.framework + " + Tauri").strip(" +")
    if "next" in names:
        facts.framework = "Next.js"
    facts.key_dependencies = _top(deps)
    facts.dev_tools = _top(dev, 10)
    return facts


def parse_composer_json(path: Path, rel: str) -> StackFacts:
    data = json.loads(path.read_text(encoding="utf-8"))
    req = data.get("require", {})
    facts = StackFacts(language="PHP", source=rel,
                       runtime=req.get("php", ""))
    names = set(req)
    if "laravel/framework" in names:
        facts.framework = f"Laravel {req['laravel/framework']}"
    if "filament/filament" in names:
        facts.framework = (facts.framework + " + Filament").strip(" +")
    facts.key_dependencies = _top({k: v for k, v in req.items() if k != "php"})
    facts.dev_tools = _top(data.get("require-dev", {}), 8)
    return facts


def parse_cargo_toml(path: Path, rel: str) -> StackFacts:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    deps = data.get("dependencies", {})
    facts = StackFacts(language="Rust", source=rel)
    if "tauri" in deps:
        v = deps["tauri"]
        v = v.get("version", "") if isinstance(v, dict) else v
        facts.framework = f"Tauri {v}"
    facts.key_dependencies = _top({k: (v.get("version", "") if isinstance(v, dict) else v)
                                   for k, v in deps.items()})
    return facts


def parse_venv(venv_path: Path, rel: str) -> StackFacts:
    """kaiwu fallback：从 .venv site-packages 的 .dist-info 目录取包名与版本。"""
    facts = StackFacts(language="Python", source=rel)
    cfg = venv_path / "pyvenv.cfg"
    if cfg.is_file():
        m = re.search(r"^version\s*=\s*(.+)$", cfg.read_text(), re.M)
        if m:
            facts.runtime = f"Python {m.group(1).strip()}"
    site = next(venv_path.glob("lib/python*/site-packages"), None)
    if site is None:
        return facts
    pkgs = []
    for d in site.glob("*.dist-info"):
        name_ver = d.name[:-10]  # 去 .dist-info
        pkgs.append(name_ver.replace("-", " ", 0).rsplit("-", 1))
    facts.key_dependencies = sorted(f"{n} {v}" for n, v in
                                    (p if len(p) == 2 else (p[0], "") for p in pkgs))[:24]
    return facts


def parse_manifest(root: str, rel: str) -> StackFacts | None:
    path = Path(root) / rel
    if not path.is_file():
        return None
    name = Path(rel).name
    try:
        if name == "package.json":
            return parse_package_json(path, rel)
        if name == "composer.json":
            return parse_composer_json(path, rel)
        if name == "Cargo.toml":
            return parse_cargo_toml(path, rel)
    except Exception:
        return None
    return None


def python_fallback(root: str) -> StackFacts | None:
    """无标准清单时的 Python 项目 fallback（kaiwu）。"""
    venv = Path(root) / ".venv"
    if venv.is_dir():
        return parse_venv(venv, ".venv（site-packages 实测）")
    return None
