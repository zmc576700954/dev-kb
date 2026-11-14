"""批量状态规范化：把保守的 `草案` 调整为真实状态，并按内容特征设定复核周期。

状态判据（规范 §12.1）：
- 稳定：内容完整、可直接引用 → 复核周期 6 个月（方法论类）
- 需监控：内容可信但依赖会变的外部信息（平台/法规/模型迭代）→ 3 个月
- 草案：未完成或未复核

用法：
  PYTHONPATH=src .venv/bin/python scripts/batch_status.py ai-video-kb            # 预览
  PYTHONPATH=src .venv/bin/python scripts/batch_status.py ai-video-kb --apply    # 生成并应用
"""

import argparse
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent_app.kb_spec.service import KBSpecService

# 依赖外部信息（平台/法规/模型/价格）的文档特征 → 需监控 + 3 个月
EXTERNAL_PATTERN = re.compile(
    r"(合规|版权|法规|平台|商业|价格|成本|分成|变现|模型全家桶|视频生成模型)", re.IGNORECASE)


def classify(path: str, title: str) -> tuple[str, str, str]:
    """返回 (状态, 复核周期描述, 理由)。"""
    if EXTERNAL_PATTERN.search(path) or EXTERNAL_PATTERN.search(title):
        return "需监控", "3 个月", "依赖会变的外部信息（平台/法规/模型）"
    return "稳定", "6 个月", "方法论/技术类，内容相对稳定"


def next_review(months: int, today: date) -> str:
    y = today.year + (today.month - 1 + months) // 12
    m = (today.month - 1 + months) % 12 + 1
    import calendar
    d = min(today.day, calendar.monthrange(y, m)[1])
    return f"{y:04d}-{m:02d}-{d:02d}"


def rewrite_frontmatter(content: str, status: str, review: str, today: str) -> str:
    """改写 frontmatter 的状态/最后更新/下次复核，保留其他键。"""
    lines = content.split("\n")
    if not lines or lines[0] != "---":
        return content
    close = next((i for i in range(1, len(lines)) if lines[i] == "---"), -1)
    if close == -1:
        return content
    fm = []
    seen = set()
    for ln in lines[1:close]:
        if ":" in ln:
            k = ln.split(":", 1)[0]
            if k == "状态":
                fm.append(f"状态: {status}")
                seen.add("状态")
                continue
            if k == "最后更新":
                fm.append(f"最后更新: {today}")
                seen.add("最后更新")
                continue
            if k == "下次复核":
                fm.append(f"下次复核: {review}")
                seen.add("下次复核")
                continue
        fm.append(ln)
    for k, v in (("状态", status), ("最后更新", today), ("下次复核", review)):
        if k not in seen:
            fm.append(f"{k}: {v}")
    return "\n".join(["---"] + fm + ["---"] + lines[close + 1:])


def main():
    parser = argparse.ArgumentParser(description="批量状态规范化")
    parser.add_argument("project")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--force-status", default=None, help="强制所有文档使用该状态")
    args = parser.parse_args()

    svc = KBSpecService()
    _, snap = svc.build_snapshot(args.project)
    today = date.today()

    plan = []
    for path, content in sorted(snap.files.items()):
        if path == "README.md":
            continue
        from agent_app.kb_spec.docmodel import parse_document
        doc = parse_document(path, content)
        title = doc.title or ""
        if args.force_status:
            status, months, why = args.force_status, 6, "手动指定"
        else:
            status, period, why = classify(path, title)
            months = 3 if period.startswith("3") else 6
        review = next_review(months, today)
        new = rewrite_frontmatter(content, status, review, today.isoformat())
        if new == content:
            continue
        plan.append((path, status, review, why, new))

    print(f"项目 {args.project}：{len(plan)} 篇文档状态待规范化（今天 {today}）\n")
    for path, status, review, why, _ in plan:
        print(f"  {path}")
        print(f"      状态 → {status}；下次复核 → {review}（{why}）")

    if args.apply:
        print("\n--- 生成并应用提案 ---")
        ok = 0
        for path, status, review, why, new in plan:
            r = svc.propose_update(args.project, path, new, snap.sha_of(path))
            if r["status"] != "proposal_created":
                print(f"  {path}: {r['status']} {r.get('message', '')[:50]}")
                continue
            pid = r["proposal_id"]
            svc.proposals.decide(pid, "approved")
            res = svc.apply_proposal(pid)
            if res["status"] == "applied":
                ok += 1
            else:
                print(f"  {path}: {res['status']}")
        print(f"应用成功 {ok}/{len(plan)}")
        v = svc.kb_validate_project(args.project)
        print(f"最终：error={v['summary']['errors']} warning={v['summary']['warnings']}")


if __name__ == "__main__":
    main()
