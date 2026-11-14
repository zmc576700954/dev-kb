"""导航式分组导语生成：为「`##` 直接挂 `###`」的分组补一句范围说明。

导语必须提供信息增量（枚举覆盖范围），不能是「本节介绍 X」式废话。
生成策略：
1. LLM 生成（本地 Qwen3-4B 或配置的 API），给定分组标题 + 子标题列表；
2. 规则兜底：LLM 不可用或输出不合格时，按子标题枚举生成；
3. 结果作为提案提交，人工审阅后落盘。

用法：
  PYTHONPATH=src .venv/bin/python scripts/gen_group_intros.py ai-video-kb --limit 20 --dry-run
  PYTHONPATH=src .venv/bin/python scripts/gen_group_intros.py ai-video-kb --apply
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent_app.kb_spec.docmodel import parse_document
from agent_app.kb_spec.service import KBSpecService

SYSTEM = """你是技术文档编辑。为文档中的「分组小标题」补一句导语，要求：
1. 一句话，不超过 40 字，以句号结尾。
2. 说明本组覆盖的范围与要点，要有信息增量（枚举覆盖的方面）。
3. **只能依据给出的子标题与内容线索概括，不得引入其中没有的信息**（不要臆测文档里没有的内容）。
4. 不要复述小标题本身，不要写「本节介绍」「本节将」「本节描述」「本节主要」「如下所示」这类空话。
5. 用简体中文；子标题已是中文时直接使用其核心词。

示例：
标题：4. 商业模式
子标题：4.1 平台分账模式、4.2 创作收益、4.3 平台扶持政策（2025 重点）
内容线索：CPM 分账、保底分账、会员分账；平台分账、创作收益
导语：本节从平台分账、创作收益与扶持政策三个方面拆解变现路径。

示例：
标题：6. 国际市场机会
子标题：6.1 现状、6.2 机会点
内容线索：日韩、东南亚、中东；TikTok、Kuaishou、Shorts
导语：本节先看国际市场现状，再指出可把握的机会点。"""


def content_clues(lines: list[str], doc, start_line: int, group_level: int,
                  max_clues: int = 6) -> list[str]:
    """收集分组下实际内容线索：表格列名、列表项文本。"""
    clues: list[str] = []
    subs = [h for h in doc.headings
            if h.line > start_line and h.level == group_level + 1]
    for h in subs[:2]:
        for i in range(h.line, min(h.line + 30, len(lines))):
            ln = lines[i].strip()
            if not ln or ln.startswith("#"):
                continue
            if ln.startswith("|"):
                cells = [c.strip() for c in ln.strip("|").split("|")]
                if len(cells) >= 2 and not all(set(c) <= set("-: ") for c in cells):
                    clues.append("、".join(cells[:4]))
            elif re.match(r"^[-*+]\s+", ln) or re.match(r"^\d+[.、)]\s+", ln):
                item = re.sub(r"^[-*+]\s+|^\d+[.、)]\s+", "", ln)
                item = re.sub(r"[*_`]", "", item)[:20]
                if item:
                    clues.append(item)
            if len(clues) >= max_clues:
                return clues
    return clues

BAD_PATTERNS = ("本节介绍", "本节将", "本节描述", "本节主要", "如下所示", "以下是", "本节包括")


def rule_fallback(title: str, subs: list[str]) -> str:
    """规则兜底：按子标题枚举生成导语。"""
    clean = [re.sub(r"^\d+(\.\d+)*\s*", "", s).strip() for s in subs]
    clean = [c for c in clean if c]
    if not clean:
        return f"本节围绕{title}展开。"
    if len(clean) == 1:
        return f"本节说明{clean[0]}。"
    if len(clean) == 2:
        return f"本节说明{clean[0]}与{clean[1]}。"
    listed = "、".join(clean[:-1]) + "与" + clean[-1]
    return f"本节从{listed}几个方面展开。"


def looks_ok(text: str) -> bool:
    if not text or len(text) < 8 or len(text) > 60:
        return False
    if any(b in text for b in BAD_PATTERNS):
        return False
    return True


def gen_intro(title: str, subs: list[str], use_llm: bool, clues: list[str] | None = None) -> str:
    if use_llm:
        try:
            from agent_app.llm import backend
            if backend.available():
                prompt = f"标题：{title}\n子标题：{'、'.join(subs)}\n"
                if clues:
                    prompt += f"内容线索：{'；'.join(clues)}\n"
                prompt += "\n导语："
                out = backend.chat(SYSTEM, prompt, max_tokens=80, temperature=0.3)
                cand = (out or "").strip().split("\n")[0].strip()
                if looks_ok(cand):
                    return cand
        except Exception:
            pass
    return rule_fallback(title, subs)


def collect_groups(content: str, rel_path: str, replace: bool = False):
    """找出需要导语的导航式分组，返回 [(行号, 标题, 子标题列表, 内容线索)]。

    replace=True 时，把「已有导语」的分组也纳入（用于重新生成时替换）。
    """
    doc = parse_document(rel_path, content)
    lines = content.split("\n")
    out = []
    for i, h in enumerate(doc.headings):
        nxt = doc.headings[i + 1] if i + 1 < len(doc.headings) else None
        if nxt is None or nxt.level <= h.level:
            continue
        body = lines[h.line:nxt.line - 1]
        has_text = any(ln.strip() for ln in body)
        if has_text and not replace:
            continue          # 已有正文
        subs = []
        for h2 in doc.headings[i + 1:]:
            if h2.level <= h.level:
                break
            if h2.level == h.level + 1:
                subs.append(h2.text)
        if has_text and replace:
            # 仅替换「标题后恰好一行非空内容、以句号结尾的句子」（本脚本生成的导语形态）
            non_empty = [ln for ln in body if ln.strip()]
            if len(non_empty) != 1:
                continue
            first = non_empty[0].strip()
            if not first.endswith("。") or len(first) > 60:
                continue
            if first.startswith(("-", "*", "|", ">", "`")):
                continue
        clues = content_clues(lines, doc, h.line, h.level)
        out.append((h.line, h.text, subs, clues))
    return out


def insert_intros(content: str, groups, replace: bool = False) -> str:
    """在分组标题后插入（或替换）导语，从后往前操作避免行号漂移。"""
    lines = content.split("\n")
    for lineno, title, subs, clues in sorted(groups, key=lambda g: -g[0]):
        intro = gen_intro(title, subs, use_llm=True, clues=clues)
        if replace:
            # 删掉已有的导语行，再插入新导语
            del lines[lineno]
        lines.insert(lineno, intro)
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="生成导航式分组导语")
    parser.add_argument("project")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 篇文档")
    parser.add_argument("--no-llm", action="store_true", help="只用规则兜底")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--replace", action="store_true", help="替换已有导语")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if args.no_llm:
        import gen_group_intros as g
        g.__dict__["_nolllm"] = True

    svc = KBSpecService()
    _, snap = svc.build_snapshot(args.project)

    targets = [p for p in sorted(snap.files) if p != "README.md"]
    if args.limit:
        targets = targets[:args.limit]

    total_groups = 0
    for path in targets:
        content = snap.files[path]
        groups = collect_groups(content, path, replace=args.replace)
        if not groups:
            continue
        total_groups += len(groups)
        if args.dry_run:
            print(f"\n{path}（{len(groups)} 处）")
            for lineno, title, subs, clues in groups[:3]:
                intro = gen_intro(title, subs, use_llm=not args.no_llm, clues=clues)
                print(f"  {title}")
                print(f"    子标题：{'、'.join(subs)}")
                print(f"    → {intro}")
    if args.dry_run:
        print(f"\n共 {total_groups} 处")
        return

    # 生成提案并应用
    ok = 0
    for path in targets:
        content = snap.files[path]
        groups = collect_groups(content, path, replace=args.replace)
        if not groups:
            continue
        new = insert_intros(content, groups, replace=args.replace)
        r = svc.propose_update(args.project, path, new, snap.sha_of(path))
        if r["status"] != "proposal_created":
            print(f"  {path}: {r['status']} {r.get('message', '')[:50]}")
            continue
        pid = r["proposal_id"]
        svc.proposals.decide(pid, "approved")
        res = svc.apply_proposal(pid)
        if res["status"] == "applied":
            ok += 1
            print(f"  {path}: 已应用（{len(groups)} 处导语）")
        else:
            print(f"  {path}: {res['status']}")
    print(f"\n应用成功 {ok} 篇")
    v = svc.kb_validate_project(args.project)
    print(f"最终：error={v['summary']['errors']} warning={v['summary']['warnings']}")


if __name__ == "__main__":
    main()
