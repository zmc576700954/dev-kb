"""kb_spec 测试共享夹具：一个合规的最小知识库项目（3 分类 × 3 文档 + README）。"""

from __future__ import annotations

from pathlib import Path

BODY = (
    "工作流管理的第一步是理解文件结构，第二步是建立命名规范，第三步是团队共享前的清理。"
    "导出工作流时注意保留元数据字段，导入时先检查节点兼容性再执行。"
    "命名统一使用小写连字符格式，版本信息记录在正文变更说明中而不是文件名里。"
    "共享前先清理个人节点配置，避免把本机路径与私有凭据带进共享文件。"
    "团队仓库中的每一个工作流都应当能够追溯到明确的登记日期与复核日期。"
) * 2

P2 = (
    "版本管理是团队协作的关键环节。每次修改先在正文写清变更原因与影响范围，"
    "再更新元数据中的登记日期。评审通过后状态改为稳定，六个月后按计划复核。"
    "出现依赖变化时把状态改为需监控，并在复核日期到期时强制检查外部接口是否仍然一致。"
) * 2

P3 = (
    "常见问题集中在三类：节点缺失、模型路径错误、显存不足。"
    "节点缺失时先确认插件是否安装，模型路径错误时检查目录挂载配置，"
    "显存不足时降低分辨率或更换更小的底模。以上处理都不奏效时，记录日志并升级到维护者。"
) * 2

CATEGORIES = {
    "01-基础概念": ("01-安装与环境配置", "02-节点连接方式", "03-插件管理机制"),
    "02-核心技术": ("01-工作流结构解析", "02-变量传递原理", "03-批处理调度方案"),
    "03-应用实践": ("01-素材批量生产", "02-质量抽检流程", "03-发布与归档方式"),
}

SUMMARY = ("本文档系统说明该主题的核心概念、操作步骤与边界注意事项，"
           "帮助读者快速上手并排查常见问题，所有命令均可直接复制执行。")
SUMMARY_LINE = f"> {SUMMARY}"


def base_doc(title: str, related: list[str]) -> str:
    related_lines = "\n".join(f"- [{_name_of(t)}]({t})" for t in related)
    return f"""---
状态: 稳定
最后更新: 2026-01-15
下次复核: 2027-07-15
优先级: 中
标签: 知识库
---

# {title}

> {SUMMARY}

## 概念与结构

本节先给出整体图景，再逐层展开细节。{BODY}

| 项目 | 说明 | 状态 |
|---|---|---|
| 节点图 | 工作流的核心结构 | 稳定 |
| 元数据 | 登记与复核信息 | 稳定 |

## 操作步骤

按顺序执行以下步骤，每步完成后核对输出。{P2}

```bash
python -m workflow_cli export --clean
python -m workflow_cli validate ./out
```

## 常见问题

{P3}

## 相关文档

{related_lines}
"""


def _name_of(target: str) -> str:
    stem = target.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    digits = "".join(ch for ch in stem if ch.isdigit() or ch == "-")
    return stem[len(digits):]


def project_files() -> dict[str, str]:
    """返回完整合规项目的 {相对路径: 内容}（不含空目录）。"""
    files: dict[str, str] = {}
    for cat, docs in CATEGORIES.items():
        for i, name in enumerate(docs):
            path = f"{cat}/{name}.md"
            siblings = [f"./{n}.md" for n in docs if n != name]
            other_cat = next(c for c in CATEGORIES if c != cat)
            other_doc = f"../{other_cat}/{CATEGORIES[other_cat][0]}.md"
            files[path] = base_doc(_title(name), siblings + [other_doc])
    files["README.md"] = _readme(files)
    return files


def _title(name: str) -> str:
    digits = "".join(ch for ch in name if ch.isdigit() or ch == "-")
    return name[len(digits):]


def _readme(files: dict[str, str]) -> str:
    out = ["# 测试知识库", "",
           "> 覆盖工作流从安装到发布全流程的知识库项目，供自动化校验测试使用。", ""]
    for cat, docs in CATEGORIES.items():
        out.append(f"## {cat}")
        out.append("")
        for name in docs:
            path = f"{cat}/{name}.md"
            out.append(f"- [{_title(name)}](./{path}) — 一句话说明")
        out.append("")
    return "\n".join(out) + "\n"


def write_project(root: Path, extra_dirs: list[str] | None = None) -> dict[str, str]:
    """把合规项目写到磁盘；返回 {相对路径: 内容}。"""
    files = project_files()
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    for d in extra_dirs or []:
        (root / d).mkdir(parents=True, exist_ok=True)
    return files


def kb_registry_yaml(root: Path, name: str = "fixture-kb") -> str:
    import json
    return json.dumps({"projects": [{"name": name, "path": str(root)}]},
                      ensure_ascii=False)
