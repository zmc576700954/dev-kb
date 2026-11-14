#!/usr/bin/env python3
"""dev-kb 自身文档的规范化：补 frontmatter、修摘要、代码块语言标注、补相关文档段、重写 README 导航。

dev-kb 是「代码仓库兼知识库」——规范适用面由 kb_paths 限定（docs/、evals/）。
本脚本产出单个合并提案（每篇文档一个），供人工审阅后应用。

用法：
  PYTHONPATH=src .venv/bin/python scripts/normalize_dev_kb.py            # 预览
  PYTHONPATH=src .venv/bin/python scripts/normalize_dev_kb.py --apply    # 生成并应用提案
"""

import argparse
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent_app.kb_spec.service import KBSpecService

PROJECT = "dev-kb"
TODAY = date(2026, 11, 14)

# 各文档的合规摘要（人工撰写，30–80 字，说明范围与用途）
SUMMARIES = {
    "docs/architecture.md":
        "本文说明 dev-kb 的模块划分、三条主数据流与存储设计，逐层给出职责边界与不可违反的硬约束。",
    "docs/decisions.md":
        "本文按时间顺序记录 dev-kb 的技术决策，每条说明背景、决策内容、理由与代价，是改动前必读的判断依据。",
    "docs/knowledge-map.md":
        "本文汇总项目涉及的知识领域，每条给出用在哪、关键概念与一手参考文档，便于向外查证与深化理解。",
    "evals/README.md":
        "本文说明检索质量的回归评测方法：用例集构成、命中判定口径、基线复现命令与参数调优规则。",
}

# 相关文档段（按规范 §7：无序列表，每行 `- [描述](路径)`）
RELATED = {
    "docs/architecture.md": [
        ("技术决策记录（ADR）", "./decisions.md"),
        ("知识点地图", "./knowledge-map.md"),
        ("检索评测与实验记录", "../evals/README.md"),
    ],
    "docs/decisions.md": [
        ("架构总览", "./architecture.md"),
        ("知识点地图", "./knowledge-map.md"),
    ],
    "docs/knowledge-map.md": [
        ("架构总览", "./architecture.md"),
        ("技术决策记录（ADR）", "./decisions.md"),
    ],
    "evals/README.md": [
        ("架构总览", "../docs/architecture.md"),
        ("技术决策记录（ADR）", "../docs/decisions.md"),
    ],
}

# README 导航：按分类分组（规范 §2.1），链接以 ./ 或 ../ 开头
README_NAV = """
## docs

- [架构总览](./docs/architecture.md) — 模块划分、数据流、存储设计
- [技术决策记录（ADR）](./docs/decisions.md) — 全部技术决策的索引，按编号指向分篇正文
- [决策分篇（一）索引与检索](./docs/01-索引与检索决策.md) — 存储选型、切块策略、混合检索与评测调参
- [决策分篇（二）分层与写入门禁](./docs/02-分层与写入门禁决策.md) — 事实与推断分层、LLM 后端、提案闭环与 MCP 门禁
- [决策分篇（三）规范与工程化](./docs/03-规范与工程化决策.md) — 规范修正、批量策略、server 健壮性与范围限定
- [知识点地图](./docs/knowledge-map.md) — 项目涉及的知识领域及外部参考链接

## evals

- [检索评测与实验记录](./evals/README.md) — 回归评测、独立验证集、逐题诊断与项目路由评测

## 变更记录

- [演进时间线](./CHANGELOG.md) — M1→M7 里程碑与关键修改历史
"""


def tag_code_blocks(text: str) -> str:
    """为无语言标注的围栏代码块补 text（dev-kb 的代码块均为 ASCII 图/流程图）。

    必须用状态机而非配对式实现：配对式会把「纯围栏 closing」误当成新 opening，
    破坏围栏结构（曾导致 closing 被标成 ```text、后续正文被包进代码块）。
    """
    lines = text.split("\n")
    out: list[str] = []
    in_block = False
    needs_tag = False
    block: list[str] = []
    for ln in lines:
        if not in_block:
            m = re.match(r"^ {0,3}(```+)(.*)$", ln)
            if m:
                in_block = True
                needs_tag = m.group(2).strip() == ""
                block = []
                out.append(ln)
                continue
            out.append(ln)
            continue
        if re.match(r"^ {0,3}```+\s*$", ln):
            if needs_tag:
                out[-1] = "```text"
            out.append(ln)
            in_block = False
            block = []
            continue
        block.append(ln)
        out.append(ln)
    return "\n".join(out)


def ensure_frontmatter(text: str, status: str = "稳定") -> str:
    """补齐 frontmatter（规范 §3.1/§3.3）。"""
    if text.startswith("---"):
        return text
    fm = (f"---\n状态: {status}\n最后更新: {TODAY.isoformat()}\n"
          f"下次复核: {TODAY.replace(year=TODAY.year + 1).isoformat()}\n---\n\n")
    return fm + text


def replace_summary(text: str, summary: str) -> str:
    """把首段（或首个引用块）替换为合规摘要（30–80 字）。"""
    lines = text.split("\n")
    # 跳过 frontmatter
    start = 0
    if lines and lines[0] == "---":
        for i in range(1, len(lines)):
            if lines[i] == "---":
                start = i + 1
                break
    # 找 H1
    h1 = None
    for i in range(start, len(lines)):
        if lines[i].startswith("# "):
            h1 = i
            break
    if h1 is None:
        return text
    # H1 之后：跳过空行
    j = h1 + 1
    while j < len(lines) and not lines[j].strip():
        j += 1
    # 跳过旧的摘要块（连续引用行）
    while j < len(lines) and lines[j].lstrip().startswith(">"):
        j += 1
    while j < len(lines) and not lines[j].strip():
        j += 1
    new_lines = lines[:h1 + 1] + ["", f"> {summary}", ""] + lines[j:]
    return "\n".join(new_lines)


def ensure_related(text: str, items: list[tuple[str, str]]) -> str:
    """补「## 相关文档」段（规范 §7）。"""
    if "## 相关文档" in text:
        return text
    block = ["", "## 相关文档", ""]
    block += [f"- [{desc}]({path})" for desc, path in items]
    block.append("")
    return text.rstrip("\n") + "\n" + "\n".join(block)


def rewrite_readme(text: str) -> str:
    """重写 README 导航：按分类分组 + 链接前缀 + 摘要。"""
    # 摘要：第 3 行（首段）改成引用块
    lines = text.split("\n")
    intro = ""
    for i, ln in enumerate(lines):
        if ln.startswith("# "):
            continue
        if ln.strip() and not ln.startswith("#"):
            intro = ln.strip()
            lines[i] = f"> {intro}"
            break
    text = "\n".join(lines)
    # 替换「## 文档导航（知识库本体）」整节
    m = re.search(r"\n## 文档导航[^\n]*\n.*?(?=\n## |\Z)", text, re.S)
    if m:
        text = text[:m.start()] + "\n" + README_NAV.rstrip("\n") + text[m.end():]
    else:
        text = text.replace("\n## 快速开始", "\n" + README_NAV.strip("\n") + "\n\n## 快速开始")
    return text


def main():
    parser = argparse.ArgumentParser(description="dev-kb 文档规范化")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    svc = KBSpecService()
    _, snap = svc.build_snapshot(PROJECT)

    plans = []
    for path, content in sorted(snap.files.items()):
        if path == "README.md":
            new = rewrite_readme(content)
            new = tag_code_blocks(new)
            if new != content:
                plans.append((path, new))
            continue
        if path not in SUMMARIES:
            continue
        new = content
        new = ensure_frontmatter(new)
        new = replace_summary(new, SUMMARIES[path])
        new = tag_code_blocks(new)
        new = ensure_related(new, RELATED.get(path, []))
        if new != content:
            plans.append((path, new))

    print(f"项目 {PROJECT}：{len(plans)} 篇文档待规范化")
    for path, _ in plans:
        print(f"  {path}")

    if not args.apply:
        print("\n（预览模式；加 --apply 生成并应用提案）")
        return
    ok = 0
    for path, new in plans:
        r = svc.propose_update(PROJECT, path, new, snap.sha_of(path))
        if r["status"] != "proposal_created":
            print(f"  {path}: {r['status']} {r.get('message', '')[:60]}")
            for f in r.get("reject_findings", [])[:5]:
                print(f"      {f['rule_id']}: {f['message'][:60]}")
            continue
        pid = r["proposal_id"]
        svc.proposals.decide(pid, "approved")
        res = svc.apply_proposal(pid)
        if res["status"] == "applied":
            ok += 1
            print(f"  {path}: 已应用")
        else:
            print(f"  {path}: {res['status']} {res.get('message', '')[:60]}")
    print(f"\n应用成功 {ok}/{len(plans)}")
    v = svc.kb_validate_project(PROJECT)
    print(f"最终：error={v['summary']['errors']} warning={v['summary']['warnings']} "
          f"review={v['summary']['reviews']}")


if __name__ == "__main__":
    main()
