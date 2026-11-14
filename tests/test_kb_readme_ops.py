"""README op 校验与状态标注测试（接入验证暴露的问题）。"""

from __future__ import annotations

from datetime import date

from agent_app.kb_spec.docmodel import parse_document
from agent_app.kb_spec.operations import (ProjectSnapshot, plan_create, plan_update,
                                          _readme_entry_line, _validate_readme_op)
from kb_fixture import project_files, SUMMARY

TODAY = date(2026, 11, 13)
TARGET_DOC = "01-基础概念/01-安装与环境配置.md"


def snapshot(files=None, today=TODAY) -> ProjectSnapshot:
    f = files if files is not None else project_files()
    snap = ProjectSnapshot(today=today)
    snap.files = dict(f)
    snap.all_paths = set(f)
    snap.dirs = {p.rsplit("/", 1)[0] for p in f if "/" in p}
    return snap


def draft_content(snap) -> str:
    """新文档内容：状态按规范 §14 步骤 4 一律先写「草案」。"""
    return snap.files[TARGET_DOC].replace("# 安装与环境配置", "# 插件安装与依赖管理") \
                               .replace("状态: 稳定", "状态: 草案")


def test_readme_entry_marks_draft_status():
    """草案/已废弃文档的 README 条目须标注状态（规范 §12.1）。"""
    assert _readme_entry_line("标题", "a/b.md", "说明") == "- [标题](./a/b.md) — 说明"
    assert _readme_entry_line("标题", "a/b.md", "说明", "草案") == \
        "- [标题](./a/b.md) — 说明（草案）"
    assert _readme_entry_line("标题", "a/b.md", "说明", "已废弃") == \
        "- [标题](./a/b.md) — 说明（已废弃）"
    # 稳定/需监控 不标注
    assert _readme_entry_line("标题", "a/b.md", "说明", "稳定") == "- [标题](./a/b.md) — 说明"
    assert _readme_entry_line("标题", "a/b.md", "说明", "需监控") == "- [标题](./a/b.md) — 说明"


def test_create_marks_draft_in_readme():
    """新建草案文档 → README 条目必须带「（草案）」。"""
    snap = snapshot()
    content = draft_content(snap)
    plan = plan_create(snap, "t", "01-基础概念/02-插件安装与依赖管理.md", content, TODAY)
    assert not plan.rejected, [f.to_dict() for f in plan.reject_findings]
    readme_op = next(o for o in plan.ops if o.path == "README.md")
    assert "（草案）" in readme_op.after
    assert "02-插件安装与依赖管理.md" in readme_op.after


def test_readme_op_is_validated():
    """README op 自身的 error 级违规必须阻止提案（否则落盘后给全库引入 error）。"""
    snap = snapshot()
    content = draft_content(snap)
    plan = plan_create(snap, "t", "01-基础概念/02-插件安装与依赖管理.md", content, TODAY)
    # 篡改 README op，注入违规内容（链接到不存在的文档）
    readme_op = next(o for o in plan.ops if o.path == "README.md")
    readme_op.after = readme_op.after.replace(
        "- [插件安装与依赖管理](./01-基础概念/02-插件安装与依赖管理.md)",
        "- [坏链接](./99-不存在.md)")
    _validate_readme_op(plan, snap)
    assert plan.rejected
    assert any(f.rule_id == "KB-2.2-broken-link" for f in plan.reject_findings)


def test_new_document_link_in_readme_is_not_broken():
    """新建文档尚未落盘 → README 指向它的链接不得被判为断链。"""
    snap = snapshot()
    content = draft_content(snap)
    plan = plan_create(snap, "t", "01-基础概念/02-插件安装与依赖管理.md", content, TODAY)
    assert not plan.rejected, [f.to_dict() for f in plan.reject_findings]


def test_readme_missing_draft_mark_is_rejected():
    """草案文档的 README 条目缺「（草案）」标注 → 提案必须被拒绝（规范 §12.1）。"""
    snap = snapshot()
    content = draft_content(snap)
    plan = plan_create(snap, "t", "01-基础概念/02-插件安装与依赖管理.md", content, TODAY)
    assert not plan.rejected
    # 去掉状态标注，模拟 agent 手写 README 的情况
    readme_op = next(o for o in plan.ops if o.path == "README.md")
    readme_op.after = readme_op.after.replace("（草案）", "")
    _validate_readme_op(plan, snap)
    assert plan.rejected
    assert any(f.rule_id == "KB-12.1-readme-status-mark"
               for f in plan.reject_findings)


def test_update_status_change_updates_readme_mark():
    """状态改为草案 → README 条目出现「（草案）」标注。"""
    snap = snapshot()
    content = snap.files[TARGET_DOC].replace("状态: 稳定", "状态: 草案")
    plan = plan_update(snap, "t", TARGET_DOC, content, snap.sha_of(TARGET_DOC), TODAY)
    assert not plan.rejected, [f.to_dict() for f in plan.reject_findings]
    readme_ops = [o for o in plan.ops if o.path == "README.md"]
    assert readme_ops, "状态变化应触发 README 条目更新"
    assert "（草案）" in readme_ops[0].after


def test_update_status_change_removes_stale_mark():
    """状态改为稳定 → README 条目中的旧「（草案）」标注应被移除。"""
    files = project_files()
    files[TARGET_DOC] = files[TARGET_DOC].replace("状态: 稳定", "状态: 草案")
    # README 里给 TARGET_DOC 的条目加上草案标注
    lines = files["README.md"].split("\n")
    for i, ln in enumerate(lines):
        if f"](./{TARGET_DOC})" in ln:
            lines[i] = ln + "（草案）"
            break
    files["README.md"] = "\n".join(lines)
    snap = snapshot(files)
    content = snap.files[TARGET_DOC].replace("状态: 草案", "状态: 稳定")
    plan = plan_update(snap, "t", TARGET_DOC, content, snap.sha_of(TARGET_DOC), TODAY)
    assert not plan.rejected, [f.to_dict() for f in plan.reject_findings]
    readme_ops = [o for o in plan.ops if o.path == "README.md"]
    assert readme_ops
    assert "（草案）" not in readme_ops[0].after


def test_dedup_candidates_are_unique():
    """查重候选路径必须去重（检索按块返回，同一文档会多次命中）。"""
    from agent_app.kb_spec.service import KBSpecService
    import tempfile, pathlib, json
    from kb_fixture import write_project
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td) / "kb"
        write_project(root)
        config = pathlib.Path(td) / "kb_projects.yaml"
        config.write_text(json.dumps(
            {"projects": [{"name": "t", "path": str(root)}]}, ensure_ascii=False),
            encoding="utf-8")
        svc = KBSpecService(config, pathlib.Path(td) / "meta.db")
        findings = svc._dedup_findings("t", "安装与环境配置", "x.md")
        for f in findings:
            cands = f.evidence.get("candidates", [])
            assert len(cands) == len(set(cands)), cands
