"""相关文档段补全：用 LLM 选出语义最相关的文档，补齐到 3–5 条。

每篇文档的相关文档段给出「标题 + 摘要」，LLM 从全库候选中挑选，
生成后作为提案提交，人工审阅后落盘。

用法：
  PYTHONPATH=src .venv/bin/python scripts/gen_related_links.py ai-video-kb --dry-run
  PYTHONPATH=src .venv/bin/python scripts/gen_related_links.py ai-video-kb --apply
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent_app.kb_spec.docmodel import parse_document
from agent_app.kb_spec.operations import _rel_link, _summary_of
from agent_app.kb_spec.service import KBSpecService

SYSTEM = """你是知识库编辑。为一篇文档挑选相关的同库文档。

规则：
1. 只从给出的候选列表中选，不要编造。
2. 选 2–4 篇最相关的：优先「上下游依赖」「同一流程的前后环节」「同一技术的对比方案」。
3. 不要选明显不相关的（仅标题有共同字眼不算相关）。
4. 输出纯路径列表，每行一个，不要编号、不要解释。

示例候选：
02-技术详解/01-ComfyUI.md
03-工作流/01-漫剧关键帧生成工作流.md
05-音频与动效/01-TTS与对口型工具链.md
输出：
03-工作流/01-漫剧关键帧生成工作流.md
05-音频与动效/01-TTS与对口型工具链.md"""


def build_candidates(snap, current: str) -> list[str]:
    """候选：排除自身、README、归档。"""
    return [p for p in sorted(snap.files)
            if p != current and p != "README.md"
            and not p.startswith("archive/") and not p.startswith("_")]


def ask_llm(current_title: str, current_summary: str, cands: list[str],
            snap, want: int) -> list[str]:
    try:
        from agent_app.llm import backend
        if not backend.available():
            return []
        cand_desc = []
        for c in cands:
            d = parse_document(c, snap.files[c])
            s = _summary_of(d)[:50]
            cand_desc.append(f"{c}｜{d.title or ''}｜{s}")
        prompt = (f"当前文档标题：{current_title}\n当前文档摘要：{current_summary[:100]}\n\n"
                  f"候选列表：\n" + "\n".join(cand_desc) + f"\n\n请选 {want} 篇最相关的，输出纯路径：")
        out = backend.chat(SYSTEM, prompt, max_tokens=200, temperature=0.1)
        picked = []
        for ln in (out or "").split("\n"):
            # 只剥离列表标记，保留路径本身的数字序号
            ln = re.sub(r"^(?:[-*]\s+|\d+[.、)]\s*)", "", ln.strip()).strip()
            ln = ln.strip("`\"' ")
            if not ln or ln not in cands:
                continue
            if ln not in picked:
                picked.append(ln)
        return picked[:want]
    except Exception as exc:
        print(f"    LLM 调用失败：{exc}")
        return []


def current_related(doc) -> list[str]:
    """文档已有的相关文档目标（归一前的原样链接）。"""
    if not doc.related:
        return []
    out = []
    for ln in doc.related.lines:
        m = re.match(r"^-\s+\[[^\]]+\]\(([^)\s]+)\)\s*$", ln.strip())
        if m:
            out.append(m.group(1))
    return out


def rewrite_related(content: str, path: str, picks: list[str], snap) -> str:
    """重写「相关文档」段：保留已有条目，补充新选的。"""
    doc = parse_document(path, content)
    lines = content.split("\n")
    if doc.related is None:
        block = ["## 相关文档", ""]
        for p in picks:
            t = parse_document(p, snap.files[p]).title or p.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            block.append(f"- [{t}]({_rel_link(path.rsplit('/', 1)[0] if '/' in path else '', p)})")
        block.append("")
        return content.rstrip("\n") + "\n\n" + "\n".join(block) + "\n"

    # 保留已有条目行，追加新条目
    start, end = doc.related.start_line, doc.related.end_line
    existing = []
    for i in range(start, end + 1):
        if i <= len(lines) and lines[i - 1].strip().startswith("- "):
            existing.append(lines[i - 1])
    have = set(current_related(doc))
    new_items = list(existing)
    for p in picks:
        rel = _rel_link(path.rsplit('/', 1)[0] if '/' in path else '', p)
        if rel in have:
            continue
        t = parse_document(p, snap.files[p]).title or p.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        new_items.append(f"- [{t}]({rel})")
    # 替换段落内的条目行
    out = []
    inserted = False
    for i, ln in enumerate(lines, 1):
        if start <= i <= end and ln.strip().startswith("- "):
            if not inserted:
                out.extend(new_items)
                inserted = True
            continue
        out.append(ln)
    if not inserted:
        out.extend(new_items)
    return "\n".join(out)


def main():
    parser = argparse.ArgumentParser(description="相关文档段补全")
    parser.add_argument("project")
    parser.add_argument("--want", type=int, default=3, help="目标条目数")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    svc = KBSpecService()
    _, snap = svc.build_snapshot(args.project)

    targets = []
    for path, content in sorted(snap.files.items()):
        if path == "README.md":
            continue
        doc = parse_document(path, content)
        n = len(current_related(doc))
        if n < args.want:
            targets.append((path, doc, n))

    print(f"需要补全：{len(targets)} 篇\n")
    for path, doc, n in targets:
        cands = build_candidates(snap, path)
        picks = ask_llm(doc.title or path, _summary_of(doc), cands, snap,
                        want=args.want)
        print(f"{path}（现有 {n} 条）")
        for p in picks:
            print(f"    + {p}")
        if not picks:
            print("    （LLM 未给出或不可用）")
        if args.apply and picks:
            new = rewrite_related(snap.files[path], path, picks, snap)
            if new != snap.files[path]:
                r = svc.propose_update(args.project, path, new, snap.sha_of(path))
                if r["status"] == "proposal_created":
                    pid = r["proposal_id"]
                    svc.proposals.decide(pid, "approved")
                    res = svc.apply_proposal(pid)
                    print(f"    → {res['status']}")
                else:
                    print(f"    → 提案失败：{r.get('message', '')[:50]}")
    if not args.apply:
        print("\n（dry-run：未生成提案）")


if __name__ == "__main__":
    main()
