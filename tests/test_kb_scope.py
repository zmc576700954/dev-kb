"""kb_paths（知识库内容范围）测试。"""

from __future__ import annotations

import json

import pytest

from agent_app.kb_spec.service import KBSpecService
from kb_fixture import write_project

PROJECT = "fixture-kb"


def _make_service(tmp_path, kb_paths=None):
    root = tmp_path / "kb"
    write_project(root)
    # 干扰文件：根目录的非 README + 深层目录
    (root / "CHANGELOG.md").write_text("# 变更日志\n", encoding="utf-8")
    thirdparty = root / "src" / "thirdparty"
    thirdparty.mkdir(parents=True)
    (thirdparty / "spec.md").write_text("# 规范副本\n", encoding="utf-8")
    entry = {"name": PROJECT, "path": str(root)}
    if kb_paths is not None:
        entry["kb_paths"] = kb_paths
    config = tmp_path / "kb_projects.yaml"
    config.write_text(json.dumps({"projects": [entry]}, ensure_ascii=False), encoding="utf-8")
    return KBSpecService(config, tmp_path / "meta.db"), root


def test_no_scope_includes_all_markdown(tmp_path):
    """未配置 kb_paths → 项目内所有 .md 都纳入校验（规范会报出根目录多 md 的违规）。"""
    svc, root = _make_service(tmp_path)
    _, snap = svc.build_snapshot(PROJECT)
    assert "CHANGELOG.md" in snap.files
    assert "src/thirdparty/spec.md" in snap.files
    assert snap.scope == set()


def test_scope_limits_to_listed_dirs(tmp_path):
    """配置 kb_paths → 只有这些目录下的文档参与；根目录只保留 README.md。"""
    svc, root = _make_service(tmp_path, kb_paths=["01-基础概念"])
    _, snap = svc.build_snapshot(PROJECT)
    # 参与校验的文档
    assert "README.md" in snap.files
    assert "01-基础概念/01-安装与环境配置.md" in snap.files
    assert "02-核心技术/01-工作流结构解析.md" not in snap.files
    assert "CHANGELOG.md" not in snap.files
    # 存在集合：全部文件都算，断链检查基于真实文件系统
    assert "CHANGELOG.md" in snap.all_paths
    assert "src/thirdparty/spec.md" in snap.all_paths


def test_scope_blocks_create_outside_range(tmp_path):
    """范围限定时，新建文档必须落在范围内。"""
    svc, root = _make_service(tmp_path, kb_paths=["01-基础概念"])
    content = svc.build_snapshot(PROJECT)[1].files["01-基础概念/01-安装与环境配置.md"]
    r = svc.propose_create(PROJECT, "99-范围外/新文档.md", content)
    assert r["status"] == "rejected"
    r2 = svc.propose_create(PROJECT, "01-基础概念/99-范围内.md", content)
    assert r2["status"] == "proposal_created", r2.get("reject_findings")


def test_kb_projects_reports_scope(tmp_path):
    svc, root = _make_service(tmp_path, kb_paths=["01-基础概念"])
    result = svc.kb_projects()
    entry = next(p for p in result["projects"] if p["name"] == PROJECT)
    assert entry["kb_paths"] == ["01-基础概念"]
    # 范围内只有 01-基础概念 的 3 篇（夹具每分类 3 篇），其他 6 篇不计
    assert entry["documents"] == 3
    assert entry["categories"] == 1
