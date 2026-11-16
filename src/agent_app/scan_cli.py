"""M1 扫描命令：读取项目注册表，扫描全部知识源，输出 JSON + Markdown 报告。

用法：
    python -m agent_app.scan_cli --config projects.yaml --out reports/

默认配置路径见 agent_app.config；真实配置不入库，先从 projects.example.yaml 复制。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from agent_app.config import PROJECTS_EXAMPLE, default_projects_config, load_config
from agent_app.ingestion.scanner import scan_project, summarize, report_to_json


def load_projects(config_path: str) -> list[dict]:
    data = load_config(config_path, PROJECTS_EXAMPLE, "项目注册表")
    return data.get("projects", [])


def render_markdown(summaries: list[dict]) -> str:
    lines = [
        "# M1 知识源扫描报告",
        "",
        f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}（只读扫描，未修改任何项目文件）",
        "",
        "## 总览",
        "",
        "| 项目 | 类型 | 可索引文件 | 代码 | 文档 | 配置 | 主要语言 |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        langs = ", ".join(f"{k}({v})" for k, v in list(s["languages"].items())[:4]) or "—"
        c = s["file_counts"]
        lines.append(
            f"| {s['name']} | {s['project_type']} | {s['total_indexable_files']} "
            f"| {c.get('code', 0)} | {c.get('document', 0)} | {c.get('config', 0)} | {langs} |"
        )

    for s in summaries:
        lines += [
            "",
            f"## {s['name']}",
            "",
            f"- 根目录：`{s['root']}`",
            f"- 判定类型：**{s['project_type']}**",
            f"- 清单文件：{', '.join(f'`{m}`' for m in s['manifests']) or '无'}"
            + (f"；锁文件（仅作技术栈事实）：{', '.join(f'`{m}`' for m in s['lock_files'])}" if s["lock_files"] else ""),
        ]
        if s["spec_candidates"]:
            lines.append(f"- 候选 Spec / 任务文档（{len(s['spec_candidates'])}）：")
            for p in s["spec_candidates"][:30]:
                lines.append(f"  - `{p}`")
            if len(s["spec_candidates"]) > 30:
                lines.append(f"  - ……共 {len(s['spec_candidates'])} 份，详见 JSON")
        else:
            lines.append("- 候选 Spec / 任务文档：无")
        if s["excluded_dirs"]:
            top = ", ".join(f"`{k}`({v})" for k, v in list(s["excluded_dirs"].items())[:12])
            lines.append(f"- 已排除目录：{top}")
        if s["excluded_files"]:
            top = ", ".join(f"`{k}`({v})" for k, v in list(s["excluded_files"].items())[:12])
            lines.append(f"- 已排除文件模式：{top}")
        if s["unreadable"]:
            lines.append(f"- ⚠️ 不可读文件：{len(s['unreadable'])} 个")
        if s["oversize"]:
            lines.append(f"- ⚠️ 超 2MB 大文件（未入清单）：{', '.join(f'`{p}`' for p in s['oversize'][:10])}")
        if s["errors"]:
            lines.append(f"- ❌ 错误：{'; '.join(s['errors'])}")

    lines += ["", "---", "", "完整文件清单见同目录 `<项目名>.json`。", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="M1 只读知识源扫描")
    parser.add_argument("--config", default=str(default_projects_config()))
    parser.add_argument("--out", default="reports")
    args = parser.parse_args()

    projects = load_projects(args.config)
    if not projects:
        print("projects.yaml 中没有登记任何项目", file=sys.stderr)
        return 1

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    summaries = []
    for p in projects:
        report = scan_project(
            p["name"], p["path"], p.get("extra_exclude_dirs"),
            content_roots=p.get("content_roots"),
        )
        s = summarize(report)
        summaries.append(s)
        (out_dir / f"{p['name']}.json").write_text(report_to_json(report), encoding="utf-8")
        print(f"[{p['name']}] 类型={s['project_type']} 可索引文件={s['total_indexable_files']} "
              f"候选Spec={len(s['spec_candidates'])}")

    md = render_markdown(summaries)
    (out_dir / "scan-report.md").write_text(md, encoding="utf-8")
    print(f"\n报告已输出：{out_dir / 'scan-report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
