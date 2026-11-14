"""kb_cli：知识库规范的人工入口（决策 Q13：审批发生在 agent 循环之外）。

用法：
  PYTHONPATH=src .venv/bin/python -m agent_app.kb_spec.cli projects
  PYTHONPATH=src .venv/bin/python -m agent_app.kb_spec.cli validate <project> [--document PATH]
  PYTHONPATH=src .venv/bin/python -m agent_app.kb_spec.cli proposals list [--project P] [--all]
  PYTHONPATH=src .venv/bin/python -m agent_app.kb_spec.cli proposals show <id>
  PYTHONPATH=src .venv/bin/python -m agent_app.kb_spec.cli proposals approve|reject <id>
  PYTHONPATH=src .venv/bin/python -m agent_app.kb_spec.cli apply <id>
"""

from __future__ import annotations

import argparse
import json
import sys

from .service import KBSpecError, KBSpecService
from .vocab import summarize


def _fmt_finding(f: dict) -> str:
    loc = f.get("location") or ""
    hint = f"（{f['fix_hint']}）" if f.get("fix_hint") else ""
    return f"  [{f['severity']}] {f['rule_id']}  {loc}  {f['message']}{hint}"


def _print_validate(result: dict, document_only: bool, progress: bool = False) -> None:
    s = result.get("summary", {})
    if progress:
        # 显示修复进展：当前 error 数 vs 初始 error 数（从提案历史推断）
        _print_progress(result)
        return
    print(f"项目 {result['project']}{' · ' + result['target'] if document_only else ''}"
          f"：error={s.get('errors', 0)} warning={s.get('warnings', 0)}"
          f" review={s.get('reviews', 0)} → {result.get('verdict', '?')}")
    findings = result.get("findings", [])
    by_doc: dict[str, list[dict]] = {}
    for f in findings:
        doc = (f.get("location") or "").rsplit(":", 1)[0] or result.get("target", "")
        by_doc.setdefault(doc, []).append(f)
    for doc, fs in sorted(by_doc.items()):
        if len(by_doc) > 1:
            print(f"\n{doc}")
        for f in fs:
            print(_fmt_finding(f))
    if result.get("undecodable_files"):
        print("\n无法按 UTF-8 解码的文件（必须级缺陷，规范 §0.3）：")
        for p in result["undecodable_files"]:
            print(f"  {p}")
    # 显示修复建议
    if s.get("errors", 0) > 0:
        print("\n--- 下一步修复建议 ---")
        _print_fix_suggestions(findings)


def _print_progress(result: dict) -> None:
    """显示修复进展：当前状态 + 历史对比 + 下一步。"""
    s = result.get("summary", {})
    project = result["project"]
    print(f"=== 修复进展：{project} ===")
    print(f"当前：error={s.get('errors', 0)} warning={s.get('warnings', 0)} review={s.get('reviews', 0)}")
    # 从提案历史推断修复前状态
    from agent_app.kb_spec.service import KBSpecService
    from agent_app.kb_spec.registry import kb_project_root
    root = kb_project_root(project)
    if root:
        svc = KBSpecService()
        applied = svc.list_proposals(project, status="applied")
        if applied["proposals"]:
            print(f"已应用修复提案：{len(applied['proposals'])} 个")
            # 显示每个已应用提案修复了什么
            for p in applied["proposals"][:5]:
                ops_desc = []
                for op in p.get("ops", []):
                    if op.get("after") and "状态: 草案" in op["after"]:
                        ops_desc.append("frontmatter")
                    if op.get("after") and ("```bash" in op["after"] or "```text" in op["after"]):
                        ops_desc.append("语言标注")
                    if op.get("after") and "## 相关文档" in op["after"]:
                        ops_desc.append("相关文档段")
                if ops_desc:
                    print(f"  #{p['id']} {p['target']}: {'+'.join(set(ops_desc))}")
        pending = svc.list_proposals(project, status="pending")
        if pending["proposals"]:
            print(f"\n待审批提案：{len(pending['proposals'])} 个")
            # 按文档分组显示待审批提案
            from collections import defaultdict
            by_doc = defaultdict(list)
            for p in pending["proposals"]:
                by_doc[p["target"]].append(p)
            print("待审批提案分布（前 5 个文档）：")
            for doc, props in list(by_doc.items())[:5]:
                print(f"  {doc}: {len(props)} 个提案（ID: {[p['id'] for p in props]}）")
    print("\n--- 当前剩余问题 ---")
    findings = result.get("findings", [])
    errors = [f for f in findings if f["severity"] == "error"]
    if errors:
        # 按规则分组显示
        from collections import Counter
        rule_counts = Counter(f["rule_id"] for f in errors)
        print("Error 分布：")
        for rule, count in rule_counts.most_common():
            print(f"  {rule}: {count} 处")
        print("\n建议修复顺序：")
        if "KB-10-language-tag" in rule_counts:
            print("  1. 代码块语言标注（kb_batch_fix --rule-id KB-10-language-tag）")
        if "KB-5.2-empty-section" in rule_counts:
            print("  2. 空章节补过渡正文（需人工编写或 LLM 生成）")
        if "KB-3.1-doc-missing-fm" in rule_counts:
            print("  3. 补 frontmatter（kb_batch_fix --rule-id KB-3.1-doc-missing-fm）")
    else:
        print("  无 error 级违规！")


def _print_fix_suggestions(findings: list[dict]) -> None:
    """根据当前 findings 显示修复建议。"""
    errors = [f for f in findings if f["severity"] == "error"]
    if not errors:
        return
    from collections import Counter
    rule_counts = Counter(f["rule_id"] for f in errors)
    if "KB-10-language-tag" in rule_counts:
        print("  代码块语言标注：kb_batch_fix --rule-id KB-10-language-tag")
    if "KB-5.2-empty-section" in rule_counts:
        print("  空章节：需人工为每个分组标题补 1-2 句过渡正文")
    if "KB-7-missing" in [f["rule_id"] for f in findings]:
        print("  相关文档段：kb_batch_fix --rule-id KB-7-missing")


def _print_proposal(p: dict, full: bool, diff: bool = False) -> None:
    print(f"#{p['id']} [{p['status']}] {p['kind']} {p['target']}")
    if p.get("title"):
        print(f"  标题：{p['title']}")
    if p.get("reason"):
        print(f"  原因：{p['reason']}")
    # 显示修复了哪些规则（从 findings 的反面推断）
    if p.get("ops"):
        op_desc = []
        for op in p["ops"]:
            if op["op"] == "write" and op.get("after"):
                after = op["after"]
                if "状态: 草案" in after and "最后更新:" in after:
                    op_desc.append("补 frontmatter")
                if "```bash" in after or "```text" in after or "```python" in after:
                    op_desc.append("代码块语言标注")
                if "## 相关文档" in after:
                    op_desc.append("补相关文档段")
            elif op["op"] == "delete":
                op_desc.append(f"删除 {op['path']}")
        if op_desc:
            print(f"  修改内容：{' + '.join(set(op_desc))}")
    for f in p.get("findings", []):
        if f.get("severity") == "error":
            print(_fmt_finding(f))
    if not full:
        return
    if diff:
        _print_diff(p)
        return
    for op in p.get("ops", []):
        print(f"  op={op['op']} path={op['path']} before_sha={op.get('before_sha')}")
        if op.get("after") is not None:
            for ln in op["after"].split("\n"):
                print(f"    | {ln}")


def _print_diff(p: dict) -> None:
    """人类可读的 diff 视图：显示修改前后的关键差异。"""
    import difflib
    for op in p.get("ops", []):
        path = op["path"]
        print(f"\n{'='*60}")
        print(f"文件：{path}")
        print(f"操作：{op['op']}")
        print(f"{'='*60}")
        if op["op"] == "delete":
            print("  → 删除此文件")
            continue
        after = op.get("after", "")
        # 尝试获取当前内容做对比
        try:
            from pathlib import Path
            current = Path(p.get("_root", "")) / path
            if current.exists():
                before = current.read_text(encoding="utf-8")
                diff_lines = list(difflib.unified_diff(
                    before.split("\n"), after.split("\n"),
                    fromfile=f"当前：{path}", tofile=f"修改后：{path}",
                    lineterm="", n=3))
                for ln in diff_lines[:80]:  # 最多显示 80 行 diff
                    if ln.startswith("+") and not ln.startswith("+++"):
                        print(f"  \033[32m{ln}\033[0m")  # 绿色：新增
                    elif ln.startswith("-") and not ln.startswith("---"):
                        print(f"  \033[31m{ln}\033[0m")  # 红色：删除
                    elif ln.startswith("@@"):
                        print(f"  \033[36m{ln}\033[0m")  # 青色：位置
                    else:
                        print(f"  {ln}")
                if len(diff_lines) > 80:
                    print(f"  ...（共 {len(diff_lines)} 行 diff，只显示前 80 行）")
            else:
                print("  → 新建文件，内容如下：")
                for ln in after.split("\n")[:20]:
                    print(f"    {ln}")
                if len(after.split("\n")) > 20:
                    print(f"    ...（共 {len(after.split(chr(10)))} 行）")
        except Exception:
            # 无法读取当前文件，只显示 after
            print("  修改后内容：")
            for ln in after.split("\n")[:30]:
                print(f"    {ln}")


def _select_proposals(service, ids: list[int], project: str | None,
                      all_flag: bool, status: str) -> list[dict]:
    """按 id / 项目 / 全部 选出提案（按 id 升序，保证依赖顺序）。

    status 传 None 表示不按状态过滤（驳回时需要同时看到 pending 与 approved）。
    """
    if ids:
        items = []
        for pid in ids:
            r = service.show_proposal(pid)
            if r["status"] == "not_found":
                print(f"提案 #{pid} 不存在")
                continue
            items.append(r["proposal"])
        return sorted(items, key=lambda p: p["id"])
    if not (all_flag or project):
        return []
    listing = service.list_proposals(project, status, active_only=False)
    return sorted(listing["proposals"], key=lambda p: p["id"])


def _group_by_target(proposals: list[dict]) -> list[tuple[str, list[dict]]]:
    """按目标文档分组，保持组内 id 升序——同文档的提案有 before_sha 依赖。"""
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for p in proposals:
        key = p.get("target") or ""
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(p)
    return [(k, sorted(groups[k], key=lambda x: x["id"])) for k in order]


def _cmd_approve(service, ids, project, all_flag, dry_run, action: str) -> int:
    """批量批准/驳回。

    批准只针对 pending；驳回可针对 pending 与 approved（撤回批准），
    已 applied 的提案不可改状态。
    """
    if ids:
        proposals = _select_proposals(service, ids, project, all_flag, None)
    elif action == "reject":
        # 驳回：pending + approved 都可选
        listing = service.list_proposals(project, None, active_only=False)
        proposals = sorted(
            [p for p in listing["proposals"] if p["status"] in ("pending", "approved")],
            key=lambda p: p["id"])
    else:
        proposals = _select_proposals(service, ids, project, all_flag, "pending")
    if not proposals:
        print("没有符合条件的提案")
        return 0
    verb = "批准" if action == "approve" else "驳回"
    new_status = "approved" if action == "approve" else "rejected"
    print(f"将{verb} {len(proposals)} 个提案"
          + ("（dry-run，未执行）" if dry_run else ""))
    ok = skipped = 0
    for p in proposals:
        pid = p["id"]
        if dry_run:
            print(f"  #{pid} {p['kind']} {p['target']}"
                  + (f"（当前状态 {p['status']}）" if p["status"] != "pending" else ""))
            continue
        if service.proposals.decide(pid, new_status):
            ok += 1
            if p["status"] == "approved":
                print(f"  #{pid} 已撤回批准" if action == "reject" else f"  #{pid} 已批准")
        else:
            skipped += 1
            print(f"  #{pid} 跳过（当前状态 {p['status']}，不可改为 {new_status}）")
    print(f"{verb}成功 {ok} 个，跳过 {skipped} 个")
    if action == "approve" and ok and not dry_run:
        print(f"下一步：kb_cli apply（可加 --project 按项目批量应用）")
    # 跳过不是失败（状态不符是预期情况），仅当一条都没成功时提示
    return 0


def _print_apply_preview(service, groups) -> None:
    """dry-run 预览：标出哪些提案会 stale（前置条件已不成立）。

    不做这个预检会误导——历史遗留的 approved 提案可能早已过时，
    真正 apply 时会因 before_sha 不匹配而被拒。
    """
    from pathlib import Path
    stale_count = 0
    for target, ps in groups:
        print(f"  {target}")
        project = ps[0].get("project")
        root = None
        if project:
            try:
                root = str(service.require_root(project))
            except Exception:
                root = None
        for p in ps:
            pid = p["id"]
            note = ""
            if root:
                stale = _check_stale(root, p)
                if stale:
                    note = f"  ⚠ 将 stale：{stale}"
                    stale_count += 1
            tail = "（同文档后续提案）" if len(ps) > 1 else ""
            print(f"    #{pid} {p['kind']}{tail}{note}")
    if stale_count:
        print(f"\n⚠ {stale_count} 个提案的前置条件已不成立（文件已被后续修改），"
              f"应用时会被拒绝——建议先驳回它们")


def _check_stale(root: str, proposal: dict) -> str:
    """检查提案的前置条件是否仍成立，返回不一致的路径（空串=仍成立）。"""
    import hashlib
    from pathlib import Path
    for op in proposal.get("ops", []):
        path = Path(root) / op["path"]
        expected = op.get("before_sha")
        if op["op"] == "write":
            if expected is None:
                if path.exists():
                    return f"{op['path']}（应不存在但已存在）"
                continue
            if not path.exists():
                return f"{op['path']}（应存在但已不存在）"
            actual = hashlib.sha256(path.read_text(encoding="utf-8").encode()).hexdigest()
            if actual != expected:
                return f"{op['path']}（内容已变）"
        elif op["op"] == "delete":
            if not path.exists():
                return f"{op['path']}（应存在但已不存在）"
    return ""


def _cmd_apply(service, ids, project, all_flag, dry_run) -> int:
    """批量应用已批准的提案。

    按文档分组顺序应用：同文档的提案有 before_sha 依赖，前一个失败则该文档
    剩余提案不再尝试（多半会 stale）；其他文档不受影响，继续处理。
    """
    if all_flag or (project and not ids):
        listing = service.list_proposals(project, "approved", active_only=False)
        proposals = sorted(listing["proposals"], key=lambda p: p["id"])
    else:
        proposals = _select_proposals(service, ids, project, all_flag, "approved")
    if not proposals:
        print("没有已批准待应用的提案")
        return 0
    groups = _group_by_target(proposals)
    total_ops = sum(len(p.get("ops", [])) for p in proposals)
    print(f"将应用 {len(proposals)} 个提案（{len(groups)} 个文档，{total_ops} 个文件操作）"
          + ("（dry-run，未执行）" if dry_run else ""))
    if dry_run:
        _print_apply_preview(service, groups)
        return 0

    applied = failed = skipped = 0
    for target, ps in groups:
        for idx, p in enumerate(ps):
            pid = p["id"]
            result = service.apply_proposal(pid)
            status = result.get("status")
            if status == "applied":
                applied += 1
                print(f"  #{pid} 已应用 {target}"
                      + (f"（{result.get('ops_applied')} 个操作）"
                         if result.get("ops_applied") else ""))
            else:
                if status != "stale":
                    print(f"  #{pid} 失败：{status} {result.get('message', '')[:60]}")
                failed += 1
                if status == "stale":
                    skipped += len(ps) - idx - 1
                    print(f"  #{pid} stale（项目状态与提案时不一致）"
                          f"→ 该文档剩余 {len(ps) - idx - 1} 个提案跳过")
                    break
    print(f"应用成功 {applied} 个，失败 {failed} 个，因依赖跳过 {skipped} 个")
    if applied:
        target_project = proposals[0].get("project")
        if target_project:
            v = service.kb_validate_project(target_project)
            print(f"落盘后校验（{target_project}）：error={v['summary']['errors']} "
                  f"warning={v['summary']['warnings']}")
    return 0 if failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kb_cli", description="知识库规范工具（人工入口）")
    parser.add_argument("--config", default=None, help="kb_projects.yaml 路径")
    parser.add_argument("--db", default=None, help="meta.db 路径")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("projects", help="列出登记的 KB 项目")

    p_val = sub.add_parser("validate", help="校验项目或单篇文档")
    p_val.add_argument("project")
    p_val.add_argument("--document", default=None, help="只校验这一篇")
    p_val.add_argument("--json", action="store_true")
    p_val.add_argument("--progress", action="store_true", help="显示修复进展（对比历史 error 数）")

    p_search = sub.add_parser("search", help="查重检索")
    p_search.add_argument("project")
    p_search.add_argument("query")
    p_search.add_argument("--limit", type=int, default=5)

    p_prop = sub.add_parser("proposals", help="提案管理")
    prop_sub = p_prop.add_subparsers(dest="prop_cmd", required=True)
    p_list = prop_sub.add_parser("list")
    p_list.add_argument("--project", default=None)
    p_list.add_argument("--status", default=None)
    p_list.add_argument("--all", action="store_true", help="含已应用/已驳回")
    p_show = prop_sub.add_parser("show")
    p_show.add_argument("id", type=int)
    p_show.add_argument("--diff", action="store_true", help="显示前后对比 diff")
    for name in ("approve", "reject"):
        p_dec = prop_sub.add_parser(name)
        p_dec.add_argument("id", type=int, nargs="*", help="提案 id（可多个）")
        p_dec.add_argument("--project", default=None, help="按项目批量")
        p_dec.add_argument("--all", action="store_true", help="所有符合条件的提案")
        p_dec.add_argument("--dry-run", action="store_true", help="只预览不执行")

    p_apply = sub.add_parser("apply", help="应用已批准的提案")
    p_apply.add_argument("id", type=int, nargs="*", help="提案 id（可多个）")
    p_apply.add_argument("--project", default=None, help="按项目批量")
    p_apply.add_argument("--all", action="store_true", help="所有已批准未应用的提案")
    p_apply.add_argument("--dry-run", action="store_true", help="只预览不执行")

    args = parser.parse_args(argv)
    service = KBSpecService(args.config, args.db)

    try:
        if args.cmd == "projects":
            print(json.dumps(service.kb_projects(), ensure_ascii=False, indent=2))
        elif args.cmd == "validate":
            if args.document:
                result = service.kb_validate_document(args.project, path=args.document)
            else:
                result = service.kb_validate_project(args.project)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                _print_validate(result, document_only=bool(args.document), progress=args.progress)
            return 0 if result.get("summary", {}).get("errors", 0) == 0 else 1
        elif args.cmd == "search":
            result = service.kb_search(args.query, project=args.project, limit=args.limit)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.cmd == "proposals":
            if args.prop_cmd == "list":
                result = service.list_proposals(args.project,
                                                args.status,
                                                active_only=not args.all)
                for p in result["proposals"]:
                    _print_proposal(p, full=False)
                if not result["proposals"]:
                    print("（无提案）")
            elif args.prop_cmd == "show":
                result = service.show_proposal(args.id)
                if result["status"] == "not_found":
                    print(f"提案 #{args.id} 不存在")
                    return 1
                _print_proposal(result["proposal"], full=True, diff=args.diff)
            elif args.prop_cmd in ("approve", "reject"):
                ids = args.id or []
                if not ids and not (args.project or args.all):
                    print("请提供提案 id，或用 --project / --all 批量操作")
                    return 1
                return _cmd_approve(service, ids, args.project, args.all,
                                    args.dry_run, args.prop_cmd)
        elif args.cmd == "apply":
            ids = args.id or []
            if not ids and not (args.project or args.all):
                print("请提供提案 id，或用 --project / --all 批量应用")
                return 1
            return _cmd_apply(service, ids, args.project, args.all, args.dry_run)
    except KBSpecError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
