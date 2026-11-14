"""M5 项目画像命令：技术栈、目录结构、入口与关键文档。

用法：
    python -m agent_app.profile_cli tiangong
    python -m agent_app.profile_cli --all
"""

from __future__ import annotations

import argparse
from pathlib import Path

from agent_app.indexing.metadata_store import MetadataStore
from agent_app.tools.profile import get_project_profile
from agent_app.tools.projects import load_registry

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def render(p: dict) -> str:
    if "error" in p:
        return p["error"]
    out = [f"# 项目画像：{p['project']}", ""]
    out.append("## 技术栈事实（每条均可溯源）")
    for s in p["stacks"]:
        head = " / ".join(x for x in (s["language"], s["framework"], s["runtime"]) if x)
        out.append(f"- **{head or '未识别'}** ← `{s['source']}`")
        if s["key_dependencies"]:
            out.append(f"  - 核心依赖：{'、'.join(d.split(' ')[0] for d in s['key_dependencies'][:12])}")
        if s["scripts"]:
            cmds = "、".join(f"`{k}`" for k in list(s["scripts"])[:8])
            out.append(f"  - 脚本：{cmds}")
    if not p["stacks"]:
        out.append("- 未识别到清单文件")
    out += ["", "## 目录结构（按文件数）"]
    for d in p["structure"][:12]:
        out.append(f"- `{d['dir']}/` — {d['files']} 文件")
    out += ["", f"## 语言分布", "- " + "、".join(f"{k}({v})" for k, v in p["languages"].items())]
    if p["entry_points"]:
        out += ["", "## 入口文件", "- " + "、".join(f"`{e}`" for e in p["entry_points"])]
    if p["key_documents"]:
        out += ["", "## 关键文档（可配合语义检索深入）"]
        for d in p["key_documents"]:
            out.append(f"- `{d}`")
    out += ["", "> 注：以上全部为程序读取的确定事实；模块划分建议等归纳性内容由问答层生成并单独标注。"]
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="项目画像（M5）")
    parser.add_argument("project", nargs="?")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()

    store = MetadataStore(DATA_DIR / "meta.db")
    names = list(load_registry()) if args.all else [args.project]
    for name in names:
        if not name:
            continue
        print(render(get_project_profile(store, name)))
        print("\n" + "=" * 70 + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
