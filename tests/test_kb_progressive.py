"""渐进式修复与批量修复测试。"""

from __future__ import annotations

from datetime import date

from agent_app.kb_spec.operations import ProjectSnapshot, plan_update
from kb_fixture import project_files

TODAY = date(2026, 11, 13)
TARGET_DOC = "01-基础概念/01-安装与环境配置.md"


def snapshot(files=None, today=TODAY) -> ProjectSnapshot:
    f = files if files is not None else project_files()
    snap = ProjectSnapshot(today=today)
    snap.files = dict(f)
    snap.all_paths = set(f)
    snap.dirs = {p.rsplit("/", 1)[0] for p in f if "/" in p}
    return snap


def test_progressive_fix_allows_partial_repair():
    """渐进式修复：只修一类违规，error 数不增加即可入库。"""
    snap = snapshot()
    # 造一个有多类违规的文档：缺 frontmatter + 空章节 + 代码块无语言
    files = project_files()
    bad_content = files[TARGET_DOC]
    # 移除 frontmatter
    bad_content = bad_content.split("---\n", 2)[2]
    # 加一个空章节
    bad_content = bad_content.replace("## 概念与结构", "## 概念与结构\n\n## 空章节\n\n## 操作步骤")
    # 代码块去语言
    bad_content = bad_content.replace("```bash", "```")
    files[TARGET_DOC] = bad_content
    snap = snapshot(files)

    # 只修 frontmatter（仍有空章节和代码块无语言）
    new_content = f"""---
状态: 草案
最后更新: {TODAY.isoformat()}
下次复核: 按需
---

{bad_content}"""
    plan = plan_update(snap, "t", TARGET_DOC, new_content, snap.sha_of(TARGET_DOC), TODAY)
    # 应该允许：error 数从 N 降到 N-1（缺 frontmatter 被修复）
    assert not plan.rejected, [f.to_dict() for f in plan.reject_findings]


def test_progressive_fix_rejects_regression():
    """渐进式修复：error 数增加则拒绝。"""
    snap = snapshot()
    # 原文档合规，新内容引入违规（如加空章节）
    original = snap.files[TARGET_DOC]
    new_content = original.replace("## 概念与结构", "## 概念与结构\n\n## 空章节\n\n## 操作步骤")
    plan = plan_update(snap, "t", TARGET_DOC, new_content, snap.sha_of(TARGET_DOC), TODAY)
    assert plan.rejected
    assert any(f.rule_id == "KB-OP-regression" for f in plan.reject_findings)


def test_batch_fix_frontmatter():
    """批量修复 frontmatter：为所有缺元数据的文档生成提案。"""
    import tempfile, pathlib, json
    from agent_app.kb_spec.service import KBSpecService
    from kb_fixture import write_project
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td) / "kb"
        write_project(root)
        # 移除两篇文档的 frontmatter
        for p in ("01-基础概念/01-安装与环境配置.md", "01-基础概念/02-节点连接方式.md"):
            content = (root / p).read_text(encoding="utf-8")
            if content.startswith("---"):
                (root / p).write_text(content.split("---\n", 2)[2], encoding="utf-8")
        config = pathlib.Path(td) / "kb_projects.yaml"
        config.write_text(json.dumps({"projects": [{"name": "t", "path": str(root)}]}, ensure_ascii=False), encoding="utf-8")
        svc = KBSpecService(config, pathlib.Path(td) / "meta.db")
        result = svc.kb_batch_fix("t", "KB-3.1-doc-missing-fm")
        assert result["status"] == "ok"
        assert result["fixed_count"] == 2
        assert result["skipped_count"] == 0


def test_batch_fix_language_tag():
    """批量修复代码块语言标注。"""
    import tempfile, pathlib, json
    from agent_app.kb_spec.service import KBSpecService
    from kb_fixture import write_project
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td) / "kb"
        write_project(root)
        # 把一篇文档的代码块语言去掉
        p = root / "01-基础概念/01-安装与环境配置.md"
        content = p.read_text(encoding="utf-8")
        p.write_text(content.replace("```bash", "```"), encoding="utf-8")
        config = pathlib.Path(td) / "kb_projects.yaml"
        config.write_text(json.dumps({"projects": [{"name": "t", "path": str(root)}]}, ensure_ascii=False), encoding="utf-8")
        svc = KBSpecService(config, pathlib.Path(td) / "meta.db")
        result = svc.kb_batch_fix("t", "KB-10-language-tag")
        assert result["status"] == "ok"
        assert result["fixed_count"] >= 1
