"""README 重写：按规范 §2.1 生成「分类分组的链接清单」。

条目说明取自文档自身的摘要（`> ` 行），缺失时回退到现有 README 的说明文字。
原有的「必读清单 / 按主题快速跳转」信息归入规范的「阅读路径」建议小节。

用法：
  PYTHONPATH=src .venv/bin/python scripts/rewrite_readme.py ai-video-kb
  PYTHONPATH=src .venv/bin/python scripts/rewrite_readme.py ai-video-kb --apply
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent_app.kb_spec.operations import _summary_of
from agent_app.kb_spec.docmodel import parse_document
from agent_app.kb_spec.service import KBSpecService
from agent_app.kb_spec.vocab import SEVERITY_ERROR


def collect_existing_descriptions(readme: str) -> dict[str, str]:
    """从现有 README 抽取「路径 → 说明文字」。"""
    out = {}
    for m in re.finditer(r"\[([^\]]+)\]\(([^)]+\.md)\)([^|\n]*)", readme):
        path, tail = m.group(2), m.group(3)
        # 去掉引导符、编号与强调标记，只留说明文字
        desc = re.sub(r"^[\s—–\-–—:：*]*", "", tail).strip().strip("*").strip()
        desc = desc.rstrip("。")
        if desc and path not in out and len(desc) <= 45 and not desc.endswith("？"):
            out[path] = desc
    return out


def build_readme(project: str, snap, old_readme: str) -> str:
    """按分类分组生成规范 README。"""
    existing = collect_existing_descriptions(old_readme)
    # 分类 → 文档列表（排除内部目录与归档）
    cats: dict[str, list[str]] = {}
    for path in sorted(snap.files):
        if path == "README.md":
            continue
        if path.startswith("_") or path.startswith("archive/"):
            continue
        cat = path.split("/")[0]
        if cat in ("assets",):
            continue
        cats.setdefault(cat, []).append(path)

    # 项目名与说明：从旧 README 取 H1 与首个引用块
    old_doc = parse_document("README.md", old_readme)
    title = old_doc.title or "知识库"
    intro = ""
    for h in old_doc.headings:
        if h.level == 1:
            continue
    quote = re.search(r"^>\s+(.+)$", old_readme, re.MULTILINE)
    if quote:
        intro = quote.group(1).strip()

    lines = [f"# {title}", ""]
    if intro:
        lines += [f"> {intro}", ""]
    else:
        lines += [f"> 本文档是项目索引，按分类串联全部 {len(snap.files) - 1} 篇文档。", ""]

    for cat in sorted(cats):
        lines.append(f"## {cat}")
        lines.append("")
        for path in cats[cat]:
            doc = parse_document(path, snap.files[path])
            doc_title = doc.title or path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            # 说明优先取旧 README 的精炼描述，其次取文档摘要
            desc = existing.get(path, "")
            if not desc:
                summary = _summary_of(doc)
                # 去掉自我描述句式（本文档…），取第一句作为说明
                summary = re.sub(r"^本文档[是用的]?", "", summary).strip()
                first = re.split(r"(?<=[。！？])", summary, maxsplit=1)[0].strip()
                desc = first or summary
            if not desc:
                desc = f"{cat}主题下的说明文档"
            if len(desc) > 50:
                desc = desc[:48].rstrip("，。、 ") + "…"
            # 规范 §12.1：草案/已废弃文档在 README 中须标注状态（放在说明末尾，保持链接文字为纯标题）
            status = doc.status_base()
            mark = f"（{status}）" if status in ("草案", "已废弃") else ""
            lines.append(f"- [{doc_title}](./{path}) — {desc}{mark}")
        lines.append("")
    # 阅读路径（规范 §2.1 建议）：从旧 README 的优先级清单抽取
    reading = _extract_reading_path(old_readme)
    if reading:
        lines.append("## 阅读路径")
        lines.append("")
        lines.extend(reading)
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def _extract_reading_path(old_readme: str) -> list[str]:
    """从旧 README 抽取优先级分组的学习路径。"""
    lines = old_readme.split("\n")
    out: list[str] = []
    in_section = False
    for i, ln in enumerate(lines):
        if re.match(r"^##\s*2\.\s*必读清单", ln):
            in_section = True
            continue
        if in_section and re.match(r"^##\s", ln):
            break
        if not in_section:
            continue
        m = re.match(r"^###\s+(P\d[^（(]*)", ln)
        if m:
            out.append(f"### {m.group(1).strip()}")
            continue
        m = re.match(r"^\d+\.\s+\*\*\[([^\]]+)\]\(([^)]+)\)\*\*\s*(?:——|--)?\s*(.*)", ln)
        if m:
            title, path, desc = m.group(1), m.group(2), m.group(3).strip()
            out.append(f"- [{title}](./{path})" + (f" — {desc}" if desc else ""))
    return out


def main():
    parser = argparse.ArgumentParser(description="按规范重写 README")
    parser.add_argument("project")
    parser.add_argument("--apply", action="store_true", help="提交提案并应用")
    args = parser.parse_args()

    svc = KBSpecService()
    _, snap = svc.build_snapshot(args.project)
    old_readme = snap.files.get("README.md", "")
    new_readme = build_readme(args.project, snap, old_readme)

    # 校验新 README
    from agent_app.kb_spec.checks_doc import check_document
    doc = parse_document("README.md", new_readme)
    findings = check_document(doc, snap.doc_ctx())
    errors = [f for f in findings if f.severity == SEVERITY_ERROR]
    print(f"新 README：{len(new_readme.splitlines())} 行")
    print(f"校验：error={len(errors)} warning={len(findings) - len(errors)}")
    for f in findings[:8]:
        print(f"  [{f.severity}] {f.rule_id} {f.message[:60]}")

    if args.apply:
        result = svc.propose_update(args.project, "README.md", new_readme,
                                   snap.sha_of("README.md"))
        print(f"\n提案：{result['status']} #{result.get('proposal_id')}")
        if result.get("proposal_id"):
            pid = result["proposal_id"]
            svc.proposals.decide(pid, "approved")
            r = svc.apply_proposal(pid)
            print(f"应用：{r['status']}")
            if r["status"] == "applied":
                v = svc.kb_validate_project(args.project)
                print(f"\n全库：error={v['summary']['errors']} warning={v['summary']['warnings']}")
    else:
        print("\n--- 前 30 行预览 ---")
        print("\n".join(new_readme.split("\n")[:30]))


if __name__ == "__main__":
    main()
