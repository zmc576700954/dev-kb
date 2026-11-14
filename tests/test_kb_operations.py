"""语义操作规划测试：六个 plan_* 的机械保证（决策 Q2c/Q3/Q15）。"""

from __future__ import annotations

from datetime import date

from agent_app.kb_spec.operations import (ProjectSnapshot, plan_archive, plan_create,
                                          plan_delete, plan_deprecate, plan_move,
                                          plan_update)
from kb_fixture import SUMMARY, project_files

TODAY = date(2026, 11, 13)
TARGET_DOC = "01-基础概念/01-安装与环境配置.md"


def snapshot(files=None, today=TODAY) -> ProjectSnapshot:
    f = files if files is not None else project_files()
    snap = ProjectSnapshot(today=today)
    snap.files = dict(f)
    snap.all_paths = set(f)
    snap.dirs = {p.rsplit("/", 1)[0] for p in f if "/" in p}
    return snap


def sha(snap: ProjectSnapshot, path: str) -> str:
    return snap.sha_of(path)


def test_rel_link_shapes():
    """相对链接计算：同目录 ./x.md，跨目录 ../dir/x.md（不得出现 ./../ 或 ../../）。"""
    from agent_app.kb_spec.operations import _rel_link
    cases = [
        ("04-视频模型", "03-工作流/01-xxx.md", "../03-工作流/01-xxx.md"),
        ("04-视频模型", "04-视频模型/02-yyy.md", "./02-yyy.md"),
        ("01-基础概念", "02-核心技术/01-zzz.md", "../02-核心技术/01-zzz.md"),
        ("", "01-分类/a.md", "./01-分类/a.md"),
        ("99-索引与排序", "02-技术详解/01-ComfyUI.md", "../02-技术详解/01-ComfyUI.md"),
    ]
    for from_dir, target, want in cases:
        got = _rel_link(from_dir, target)
        assert got == want, f"{from_dir} → {target}: {got} != {want}"
        assert not got.startswith("./.."), got
        assert not got.startswith("../../"), got


def test_rel_link_roundtrip_resolves():
    """相对链接必须能被 _resolve 还原成原始目标。"""
    from agent_app.kb_spec.operations import _rel_link
    from agent_app.kb_spec.checks_doc import _resolve
    for from_dir, target in [
        ("04-视频模型", "03-工作流/01-xxx.md"),
        ("04-视频模型", "04-视频模型/02-yyy.md"),
        ("99-索引与排序", "02-技术详解/01-ComfyUI.md"),
    ]:
        link = _rel_link(from_dir, target)
        assert _resolve(from_dir, link) == target, (from_dir, target, link)


def test_create_happy_path_inserts_readme_entry():
    snap = snapshot()
    content = snap.files[TARGET_DOC].replace("# 安装与环境配置", "# 工作流导入导出")
    plan = plan_create(snap, "t", "01-基础概念/02-工作流导入导出.md", content, TODAY)
    assert not plan.rejected, [f.to_dict() for f in plan.reject_findings]
    assert [(o.op, o.path) for o in plan.ops] == [
        ("write", "01-基础概念/02-工作流导入导出.md"), ("write", "README.md")]
    readme_op = plan.ops[1]
    assert "02-工作流导入导出.md" in readme_op.after
    assert f"](./01-基础概念/02-工作流导入导出.md) — {SUMMARY}" in readme_op.after
    assert readme_op.before_sha == sha(snap, "README.md")


def test_create_missing_frontmatter_rejected():
    snap = snapshot()
    content = snap.files[TARGET_DOC].split("---\n", 2)[2]
    plan = plan_create(snap, "t", "01-基础概念/99-坏文档.md", content, TODAY)
    assert plan.rejected
    assert any(f.rule_id == "KB-3.1-doc-missing-fm" for f in plan.reject_findings)


def test_create_auto_fills_required_keys():
    snap = snapshot()
    content = snap.files[TARGET_DOC].replace(
        "最后更新: 2026-01-15\n", "").replace("下次复核: 2027-07-15\n", "")
    plan = plan_create(snap, "t", "01-基础概念/99-补键文档.md", content, TODAY)
    assert not plan.rejected
    final = plan.ops[0].after
    assert f"最后更新: {TODAY.isoformat()}" in final
    assert "下次复核: 按需" in final
    assert "状态: 稳定" in final   # 已存在的键不动


def test_create_existing_path_conflict():
    snap = snapshot()
    plan = plan_create(snap, "t", TARGET_DOC, snap.files[TARGET_DOC], TODAY)
    assert plan.rejected
    assert any(f.rule_id == "KB-OP-conflict" for f in plan.reject_findings)


def test_create_bad_version_suffix_rejected():
    snap = snapshot()
    content = snap.files[TARGET_DOC]
    plan = plan_create(snap, "t", "01-基础概念/01-安装-v2.md", content, TODAY)
    assert plan.rejected
    assert any(f.rule_id == "KB-4.2-version-suffix" for f in plan.reject_findings)


def test_update_status_change_forces_last_updated():
    snap = snapshot()
    content = snap.files[TARGET_DOC].replace("状态: 稳定", "状态: 需监控")
    plan = plan_update(snap, "t", TARGET_DOC, content, sha(snap, TARGET_DOC), TODAY)
    assert not plan.rejected, [f.to_dict() for f in plan.reject_findings]
    assert f"最后更新: {TODAY.isoformat()}" in plan.ops[0].after
    assert "状态: 需监控" in plan.ops[0].after


def test_update_title_change_syncs_readme():
    snap = snapshot()
    content = snap.files[TARGET_DOC].replace("# 安装与环境配置", "# 安装与运行环境")
    plan = plan_update(snap, "t", TARGET_DOC, content, sha(snap, TARGET_DOC), TODAY)
    assert not plan.rejected
    readme_ops = [o for o in plan.ops if o.path == "README.md"]
    assert len(readme_ops) == 1
    assert "安装与运行环境" in readme_ops[0].after
    assert "安装与环境配置]" not in readme_ops[0].after


def test_update_sha_conflict_rejected():
    snap = snapshot()
    plan = plan_update(snap, "t", TARGET_DOC, snap.files[TARGET_DOC], "deadbeef", TODAY)
    assert plan.rejected
    assert any(f.rule_id == "KB-OP-sha-conflict" for f in plan.reject_findings)


def test_move_rewrites_backlinks_and_readme():
    snap = snapshot()
    old, new = "03-应用实践/02-质量抽检流程.md", "03-应用实践/02-质量抽查流程.md"
    plan = plan_move(snap, "t", old, new, TODAY)
    assert not plan.rejected, [f.to_dict() for f in plan.reject_findings]
    targets = [(o.op, o.path) for o in plan.ops]
    assert ("write", new) in targets and ("delete", old) in targets
    readme_op = next(o for o in plan.ops if o.path == "README.md")
    assert f"](./{new})" in readme_op.after and f"](./{old})" not in readme_op.after
    # 兄弟文档的相关文档段入链被改写
    sibling = next(o for o in plan.ops if o.path == "03-应用实践/01-素材批量生产.md")
    assert f"](./02-质量抽查流程.md)" in sibling.after
    # 移动的文档内容保持不变（搬运不得刷新最后更新，规范 §3.5）
    moved_content = next(o for o in plan.ops if o.path == new).after
    assert "最后更新: 2026-01-15" in moved_content


def test_move_to_existing_target_rejected():
    snap = snapshot()
    plan = plan_move(snap, "t", TARGET_DOC, "01-基础概念/02-节点连接方式.md", TODAY)
    assert plan.rejected
    assert any(f.rule_id == "KB-OP-conflict" for f in plan.reject_findings)


def test_deprecate_inserts_block_and_readme_mark():
    snap = snapshot()
    plan = plan_deprecate(snap, "t", "01-基础概念/02-节点连接方式.md",
                          "已被新方案取代", "01-基础概念/03-插件管理机制.md", TODAY)
    assert not plan.rejected, [f.to_dict() for f in plan.reject_findings]
    final = plan.ops[0].after
    assert "状态: 已废弃" in final
    assert f"最后更新: {TODAY.isoformat()}" in final
    dep_line = next(l for l in final.split("\n") if l.startswith("> ⚠️ 已废弃"))
    assert "已被新方案取代" in dep_line and "替代文档见 [" in dep_line
    assert "](./03-插件管理机制.md)" in dep_line
    readme_op = next(o for o in plan.ops if o.path == "README.md")
    assert "已废弃" in readme_op.after


def test_deprecate_missing_replacement_rejected():
    snap = snapshot()
    plan = plan_deprecate(snap, "t", "01-基础概念/02-节点连接方式.md",
                          "原因", "01-基础概念/99-不存在.md", TODAY)
    assert plan.rejected
    assert any(f.rule_id == "KB-12.2-replacement-valid" for f in plan.reject_findings)


def test_deprecate_without_replacement_writes_none():
    snap = snapshot()
    plan = plan_deprecate(snap, "t", "01-基础概念/02-节点连接方式.md", "原因", None, TODAY)
    assert not plan.rejected
    assert "；无替代。" in plan.ops[0].after


def test_archive_moves_into_archive_dir_with_group():
    snap = snapshot()
    old = "03-应用实践/03-发布与归档方式.md"
    plan = plan_archive(snap, "t", old, TODAY)
    assert not plan.rejected, [f.to_dict() for f in plan.reject_findings]
    targets = [(o.op, o.path) for o in plan.ops]
    assert ("write", f"archive/03-应用实践/03-发布与归档方式.md") in targets
    assert ("delete", old) in targets
    readme_op = next(o for o in plan.ops if o.path == "README.md")
    assert "## 归档" in readme_op.after
    assert "## archive" not in readme_op.after
    assert "状态: 已归档" in next(o for o in plan.ops if o.op == "write"
                                 and o.path.startswith("archive/")).after


def test_delete_requires_reason():
    snap = snapshot()
    plan = plan_delete(snap, "t", "01-基础概念/02-节点连接方式.md", "", TODAY)
    assert plan.rejected
    assert any(f.rule_id == "KB-12.2-no-silent-delete" for f in plan.reject_findings)


def test_delete_removes_entry_and_related_links():
    snap = snapshot()
    target = "01-基础概念/02-节点连接方式.md"
    plan = plan_delete(snap, "t", target, "主题并入插件管理", TODAY)
    assert not plan.rejected, [f.to_dict() for f in plan.reject_findings]
    targets = [o.path for o in plan.ops]
    assert target in targets and "README.md" in targets
    sibling = next(o for o in plan.ops if o.path == "01-基础概念/03-插件管理机制.md")
    assert "- [节点连接方式](./02-节点连接方式.md)" not in sibling.after
    readme_op = next(o for o in plan.ops if o.path == "README.md")
    assert "02-节点连接方式.md" not in readme_op.after


def test_delete_unwraps_inline_links():
    snap = snapshot()
    target = "01-基础概念/02-节点连接方式.md"
    files = project_files()
    files[target] = files[target].replace(
        "导出工作流时注意保留元数据字段",
        "参见[节点连接方式](./02-节点连接方式.md)。导出工作流时注意保留元数据字段")
    # 在另一篇文档的正文中加入内联链接
    other = "01-基础概念/03-插件管理机制.md"
    files[other] = files[other].replace(
        "本节先给出整体图景，再逐层展开细节。",
        "节点连接详见[节点连接方式](./02-节点连接方式.md)。本节先给出整体图景，再逐层展开细节。")
    snap = snapshot(files)
    plan = plan_delete(snap, "t", target, "并入插件管理", TODAY)
    assert not plan.rejected
    other_op = next(o for o in plan.ops if o.path == other)
    assert "节点连接详见节点连接方式。" in other_op.after   # 解包为纯文字
