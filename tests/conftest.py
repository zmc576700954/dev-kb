import sys
import textwrap
from pathlib import Path

import pytest
import yaml

from agent_app.contract.declaration import SourceDeclaration

sys.path.insert(0, str(Path(__file__).parent))


# ---- 知识源接入契约的夹具 ----


@pytest.fixture()
def doc_source(tmp_path) -> SourceDeclaration:
    """文档型知识源：3 个分类目录 + README + frontmatter 文档 + 术语清单。"""
    root = tmp_path / "doc-kb"
    (root / "01-概览").mkdir(parents=True)
    (root / "02-技术").mkdir(parents=True)
    (root / "03-运维").mkdir(parents=True)

    (root / "README.md").write_text(textwrap.dedent("""\
        # 夹具知识库

        > 一个用于契约测试的小型知识库。

        ## 01-概览

        - [项目背景](./01-概览/01-背景.md) — 项目背景与目标
        - [术语表](./01-概览/02-术语.md) — 术语与缩写

        ## 02-技术

        - [核心架构](./02-技术/01-架构.md) — 核心架构说明

        ## 03-运维

        - [部署手册](./03-运维/01-部署.md) — 部署步骤
        """), encoding="utf-8")

    (root / "01-概览" / "01-背景.md").write_text(textwrap.dedent("""\
        ---
        状态: 稳定
        ---

        # 项目背景

        ## 目标

        说明项目要解决的问题。
        """), encoding="utf-8")

    (root / "01-概览" / "02-术语.md").write_text(textwrap.dedent("""\
        # 术语表

        ## 定义

        ComfyUI 是一个节点式生成界面。
        """), encoding="utf-8")

    (root / "02-技术" / "01-架构.md").write_text(textwrap.dedent("""\
        # 核心架构

        ## 模块

        架构分三层。
        """), encoding="utf-8")

    (root / "03-运维" / "01-部署.md").write_text(textwrap.dedent("""\
        # 部署手册

        ## 步骤

        按顺序部署。
        """), encoding="utf-8")

    (root / "doc-kb.terms.yaml").write_text(yaml.safe_dump({
        "aliases": {"ComfyUI": ["comfy", "comfyui"]},
    }, allow_unicode=True), encoding="utf-8")

    return SourceDeclaration.from_entry("doc-kb", {
        "path": str(root),
        "provides": ["heading_path", "category", "doc_role", "aliases"],
        "content_roots": ["."],
        "entry_docs": ["README.md"],
        "terms": ["doc-kb.terms.yaml"],
        "roles": {"README.md": "index", "01-概览/*": "guide"},
    })


@pytest.fixture()
def code_source(tmp_path) -> SourceDeclaration:
    """代码型知识源：src/ + tests/ + README，没有文档分类。"""
    root = tmp_path / "code-lib"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir(parents=True)
    (root / "src" / "pay.py").write_text("def refund():\n    return 1\n", encoding="utf-8")
    (root / "tests" / "test_pay.py").write_text("def test_refund():\n    pass\n", encoding="utf-8")
    (root / "README.md").write_text("# 夹具代码库\n\n支付模块。\n", encoding="utf-8")
    return SourceDeclaration.from_entry("code-lib", {
        "path": str(root),
        "provides": ["category"],
        "content_roots": ["."],
        "entry_docs": ["README.md"],
    })


@pytest.fixture()
def registry(tmp_path, doc_source, code_source):
    """写一份 projects.yaml 供服务层加载。"""
    path = tmp_path / "projects.yaml"
    path.write_text(yaml.safe_dump({
        "projects": [
            {"name": doc_source.name, "path": doc_source.root,
             "provides": list(doc_source.provides),
             "content_roots": list(doc_source.content_roots),
             "entry_docs": list(doc_source.entry_docs),
             "terms": list(doc_source.terms_files),
             "roles": dict(doc_source.roles)},
            {"name": code_source.name, "path": code_source.root,
             "provides": list(code_source.provides),
             "content_roots": list(code_source.content_roots),
             "entry_docs": list(code_source.entry_docs)},
        ],
    }, allow_unicode=True), encoding="utf-8")
    return path
