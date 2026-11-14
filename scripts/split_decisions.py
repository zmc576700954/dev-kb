#!/usr/bin/env python3
"""拆分 decisions.md（ADR 超过 5000 字上限，规范 §8）。

按里程碑分组：
- adr-01-索引与检索.md        ADR-0001~0010（存储、切块、检索基线、评测调参）
- adr-02-分层与写入门禁.md    ADR-0011~0018（事实分层、LLM 后端、提案闭环、MCP 门禁）
- adr-03-规范与工程化.md      ADR-0019~0024（规范修正、批量策略、server 健壮性、kb_paths）
- decisions.md（索引页）      ADR 编号 → 标题 → 一句话 → 所在篇链接

ADR 正文里的交叉引用按编号（「见 ADR-0007」）而非路径，拆分不破坏它们；
指向文件本身的链接（README、architecture.md）需要同步更新。

用法：
  PYTHONPATH=src .venv/bin/python scripts/split_decisions.py            # 预览
  PYTHONPATH=src .venv/bin/python scripts/split_decisions.py --apply    # 生成并应用提案
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

# (新文件名, 标题, 摘要, [ADR 起, ADR 止])
GROUPS = [
    ("docs/01-索引与检索决策.md", "技术决策记录（一）：索引与检索",
     "本文记录存储选型、切块策略、混合检索与评测调参的决策，覆盖 M2–M3 与 v3–v6 演进。",
     1, 10),
    ("docs/02-分层与写入门禁决策.md", "技术决策记录（二）：分层与写入门禁",
     "本文记录事实与推断分层、LLM 后端选型、文档提案闭环与 MCP 只读门禁的决策。",
     11, 18),
    ("docs/03-规范与工程化决策.md", "技术决策记录（三）：知识库规范与工程化",
     "本文记录知识库规范的落地决策：规范修正、批量提案策略、server 健壮性与范围限定。",
     19, 24),
]

ADR_RE = re.compile(r"^## (ADR-(\d{4}))：(.+)$")


def split_adrs(content: str):
    """按 ADR 标题切成 [(编号, 标题, 正文块)]。

    正文块里去掉原有的「## 相关文档」段——分篇会统一补一个，
    否则同一篇里出现两个同名小节（规范 §5.1 禁止同级标题重名）。
    """
    lines = content.split("\n")
    starts = []
    for i, ln in enumerate(lines):
        m = ADR_RE.match(ln)
        if m:
            starts.append((i, m.group(2), m.group(3)))
    out = []
    for idx, (i, num, title) in enumerate(starts):
        end = starts[idx + 1][0] if idx + 1 < len(starts) else len(lines)
        body = "\n".join(lines[i:end]).rstrip("\n")
        body = re.sub(r"\n## 相关文档\n.*\Z", "", body, flags=re.S).rstrip("\n")
        out.append((num, title, body))
    return out, lines


def build_parts(content: str):
    """返回 {新路径: 内容} 与索引页内容。"""
    adrs, lines = split_adrs(content)
    by_num = {num: (title, body) for num, title, body in adrs}
    parts = {}
    index_rows = []
    for path, title, summary, lo, hi in GROUPS:
        chunk = []
        for num, atitle, body in adrs:
            n = int(num)
            if lo <= n <= hi:
                chunk.append(body)
        parts[path] = (
            f"---\n状态: 稳定\n最后更新: {TODAY.isoformat()}\n"
            f"下次复核: {TODAY.replace(year=TODAY.year + 1).isoformat()}\n---\n\n"
            f"# {title}\n\n> {summary}\n\n" + "\n\n".join(chunk) + "\n\n"
            f"## 相关文档\n\n"
            f"- [决策索引](./decisions.md)\n"
            f"- [架构总览](./architecture.md)\n"
        )
    for num, atitle, _ in adrs:
        n = int(num)
        for path, _, _, lo, hi in GROUPS:
            if lo <= n <= hi:
                index_rows.append((num, atitle, path))
                break
    index = (
        f"---\n状态: 稳定\n最后更新: {TODAY.isoformat()}\n"
        f"下次复核: {TODAY.replace(year=TODAY.year + 1).isoformat()}\n---\n\n"
        "# 技术决策记录（ADR）\n\n"
        "> 本文是全部技术决策的索引：按编号列出标题与要点，指向分篇正文；"
        "改动前先看对应条目，判断依据在正文的「理由」与「代价」。\n\n"
        "## 决策索引\n\n"
    )
    for num, atitle, path in index_rows:
        index += f"- [ADR-{num}：{atitle}](./{path.split('/')[-1]})\n"
    index += (
        "\n## 分篇说明\n\n"
        "- [技术决策记录（一）：索引与检索](./01-索引与检索决策.md) — "
        "存储选型、切块策略、混合检索与评测调参的决策。\n"
        "- [技术决策记录（二）：分层与写入门禁](./02-分层与写入门禁决策.md) — "
        "事实与推断分层、LLM 后端、提案闭环与 MCP 只读门禁的决策。\n"
        "- [技术决策记录（三）：知识库规范与工程化](./03-规范与工程化决策.md) — "
        "规范修正、批量提案策略、server 健壮性与范围限定的决策。\n\n"
        "## 相关文档\n\n"
        "- [架构总览](./architecture.md)\n"
        "- [知识点地图](./knowledge-map.md)\n"
    )
    parts["docs/decisions.md"] = index
    return parts


def update_readme(content: str) -> str:
    """README 导航同步：新增 3 篇 ADR 分篇要进索引（规范 §2.2 覆盖要求）。"""
    from normalize_dev_kb import rewrite_readme as _rw
    return _rw(content)


def main():
    parser = argparse.ArgumentParser(description="拆分 decisions.md")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    svc = KBSpecService()
    _, snap = svc.build_snapshot(PROJECT)
    content = snap.files["docs/decisions.md"]
    parts = build_parts(content)
    parts["README.md"] = update_readme(snap.files["README.md"])

    print(f"拆分结果：{len(parts)} 个文件")
    for path, text in parts.items():
        print(f"  {path}（{len(text.splitlines())} 行）")

    if not args.apply:
        print("\n（预览模式；加 --apply 生成并应用提案）")
        return
    ok = 0
    # 顺序：先创建 3 篇 ADR 分篇（它们的相关文档只指向已存在的文件），
    # 再更新索引页与 README（它们要链接到刚创建的文件）
    new_files = [p for p in parts if p not in snap.files]
    existing_files = [p for p in parts if p in snap.files]
    for path in new_files:
        text = parts[path]
        r = svc.propose_create(PROJECT, path, text)
        if r["status"] != "proposal_created":
            print(f"  {path}: {r['status']} {r.get('message', '')[:70]}")
            for f in r.get("reject_findings", [])[:4]:
                print(f"      {f['rule_id']}: {f['message'][:70]}")
            continue
        pid = r["proposal_id"]
        svc.proposals.decide(pid, "approved")
        res = svc.apply_proposal(pid)
        if res["status"] == "applied":
            ok += 1
            print(f"  {path}: 已创建")
        else:
            print(f"  {path}: {res['status']} {res.get('message', '')[:70]}")
    for path in existing_files:
        text = parts[path]
        # 实时取 sha：前面的 create 已经改过 README（自动同步索引条目），
        # 用循环开始时的旧 sha 会触发 KB-OP-sha-conflict
        live_sha = svc.build_snapshot(PROJECT)[1].sha_of(path)
        r = svc.propose_update(PROJECT, path, text, live_sha)
        if r["status"] != "proposal_created":
            print(f"  {path}: {r['status']} {r.get('message', '')[:70]}")
            for f in r.get("reject_findings", [])[:4]:
                print(f"      {f['rule_id']}: {f['message'][:70]}")
            continue
        pid = r["proposal_id"]
        svc.proposals.decide(pid, "approved")
        res = svc.apply_proposal(pid)
        if res["status"] == "applied":
            ok += 1
            print(f"  {path}: 已更新")
        else:
            print(f"  {path}: {res['status']} {res.get('message', '')[:70]}")
    print(f"\n应用成功 {ok}/{len(parts)}")
    v = svc.kb_validate_project(PROJECT)
    print(f"最终：error={v['summary']['errors']} warning={v['summary']['warnings']} "
          f"review={v['summary']['reviews']}")


if __name__ == "__main__":
    main()
