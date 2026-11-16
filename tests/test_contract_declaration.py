"""知识源接入契约：声明模型。"""

from __future__ import annotations

from agent_app.contract.declaration import (
    OPTIONAL_FIELDS,
    SourceDeclaration,
    aliases_in_content,
    category_of,
    load_declarations,
)


def test_category_is_first_path_segment():
    """分类对所有知识源通用：代码库得 src/app，文档库得 01-业务概览。"""
    assert category_of("src/app/Pay.php") == "src"
    assert category_of("01-业务概览/01-背景.md") == "01-业务概览"
    assert category_of("README.md") == ""


def test_content_boundary_membership(tmp_path):
    decl = SourceDeclaration.from_entry("x", {
        "path": str(tmp_path), "content_roots": ["kb", "docs"]})
    assert decl.in_content("kb/a.md")
    assert decl.in_content("docs/b.md")
    assert not decl.in_content("src/c.md")
    assert not decl.in_content("kbx/d.md")


def test_content_boundary_dot_means_everything(tmp_path):
    decl = SourceDeclaration.from_entry("x", {"path": str(tmp_path), "content_roots": ["."]})
    assert decl.in_content("anything/at/all.md")


def test_no_content_roots_means_nothing_is_in_content(tmp_path):
    decl = SourceDeclaration.from_entry("x", {"path": str(tmp_path)})
    assert not decl.in_content("a.md")


def test_role_matching_prefers_longest_pattern(tmp_path):
    decl = SourceDeclaration.from_entry("x", {
        "path": str(tmp_path),
        "roles": {"*": "reference", "03-工作流/*": "guide", "03-工作流/05-*": "faq"},
    })
    assert decl.role_of("99-索引/01-重要程度排序.md") == "reference"
    assert decl.role_of("03-工作流/04-自动化生产与脚本.md") == "guide"
    assert decl.role_of("03-工作流/05-ComfyUI视频化工作流的参数调优.md") == "faq"


def test_terms_loaded_from_file(tmp_path):
    (tmp_path / "t.terms.yaml").write_text(
        "aliases:\n  ComfyUI: [comfy, comfyui]\n  LoRA: [lora]\n", encoding="utf-8")
    decl = SourceDeclaration.from_entry("x", {
        "path": str(tmp_path), "terms": ["t.terms.yaml"]})
    assert decl.terms()["ComfyUI"] == ["comfy", "comfyui"]
    assert "LoRA" in decl.alias_surfaces()


def test_tuning_file_merges_intent_and_roles(tmp_path):
    (tmp_path / "tuning.yaml").write_text(
        "intent_map:\n  部署: ['03-运维']\nroles:\n  'README.md': index\n",
        encoding="utf-8")
    decl = SourceDeclaration.from_entry("x", {
        "path": str(tmp_path), "tuning": ["tuning.yaml"]})
    assert decl.intent_map["部署"] == ("03-运维",)
    assert decl.roles["README.md"] == "index"


def test_inline_overrides_tuning_file(tmp_path):
    (tmp_path / "tuning.yaml").write_text(
        "intent_map:\n  部署: ['03-运维']\n", encoding="utf-8")
    decl = SourceDeclaration.from_entry("x", {
        "path": str(tmp_path), "tuning": ["tuning.yaml"],
        "intent_map": {"部署": ["09-部署"]}})
    assert decl.intent_map["部署"] == ("09-部署",)


def test_optional_fields_are_the_contract_field_set():
    assert OPTIONAL_FIELDS == ("heading_path", "category", "doc_role", "aliases")


def test_aliases_in_content_only_for_present_terms():
    terms = {"LoRA": ["lora", "低秩适配"], "SDXL": ["sdxl"]}
    got = aliases_in_content("本文介绍 LoRA 的用法", terms)
    assert got == ["LoRA"]
    assert aliases_in_content("本文介绍低秩适配", terms) == ["低秩适配"]
    assert "SDXL" not in got


def test_load_declarations_reads_registry(tmp_path):
    import yaml
    cfg = tmp_path / "projects.yaml"
    cfg.write_text(yaml.safe_dump({"projects": [
        {"name": "a", "path": str(tmp_path), "provides": ["category"], "content_roots": ["."]},
    ]}, allow_unicode=True), encoding="utf-8")
    decls = load_declarations(cfg)
    assert set(decls) == {"a"}
    assert decls["a"].provides == ("category",)
