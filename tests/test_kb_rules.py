"""规则级 golden 测试：每条「必须/禁止」规则至少一个正例（干净基线）+ 一个反例（变异触发）。"""

from __future__ import annotations

from datetime import date

import pytest

from agent_app.kb_spec.checks_doc import DocContext, check_document
from agent_app.kb_spec.checks_project import ProjectContext, check_project
from agent_app.kb_spec.docmodel import parse_document
from agent_app.kb_spec.rules import RULES
from agent_app.kb_spec.vocab import SEVERITIES
from kb_fixture import BODY, P2, P3, base_doc, project_files

TARGET_DOC = "01-基础概念/01-安装与环境配置.md"
TODAY = date(2026, 11, 13)

# 基线中 TARGET_DOC 的相关文档条目（kb_fixture.project_files 生成）
REL_1 = "- [节点连接方式](./02-节点连接方式.md)"
REL_2 = "- [插件管理机制](./03-插件管理机制.md)"
REL_3 = "- [工作流结构解析](../02-核心技术/01-工作流结构解析.md)"
SUMMARY_LINE = ("> 本文档系统说明该主题的核心概念、操作步骤与边界注意事项，"
                "帮助读者快速上手并排查常见问题，所有命令均可直接复制执行。")


def doc_ctx(files: dict[str, str], **kw) -> DocContext:
    return DocContext(path_exists=lambda p: p in set(files), today=TODAY, **kw)


def proj_ctx(files: dict[str, str], extra_paths=(), dirs=None) -> ProjectContext:
    docs = {p: parse_document(p, t) for p, t in files.items()}
    file_paths = set(files) | set(extra_paths)
    all_dirs = {p.rsplit("/", 1)[0] for p in file_paths if "/" in p}
    return ProjectContext(docs=docs, dirs=dirs if dirs is not None else all_dirs,
                          file_paths=file_paths, today=TODAY)


def all_findings(files: dict[str, str], extra_paths=(), dirs=None):
    out = list(check_project(proj_ctx(files, extra_paths, dirs)))
    for rel, text in files.items():
        out.extend(check_document(parse_document(rel, text), doc_ctx(files)))
    return out


def fired(findings, rule_id):
    return [f for f in findings if f.rule_id == rule_id]


# ---------------------------------------------------------------- 基线：干净项目零 error

def test_clean_project_has_zero_errors():
    errors = [f for f in all_findings(project_files()) if f.severity == "error"]
    assert errors == [], [e.to_dict() for e in errors]


def test_registry_severities_are_valid():
    for rule_id, (severity, _desc) in RULES.items():
        assert severity in SEVERITIES, rule_id


# ---------------------------------------------------------------- 文档级变异表

DOC_MUTATIONS = [
    # --- §3 frontmatter ---
    ("KB-3.1-doc-missing-fm", lambda c: c.split("---\n", 2)[2], "error"),
    ("KB-3.2-fence", lambda c: c.replace(
        "标签: 知识库\n---", "标签: 知识库"), "error"),
    ("KB-3.2-line-format", lambda c: c.replace("状态: 稳定", "状态:稳定"), "error"),
    ("KB-3.2-flat-scalar", lambda c: c.replace(
        "标签: 知识库\n---", "标签: 知识库\n  - 嵌套项\n---"), "error"),
    ("KB-3.2-empty-block", lambda c: c.replace(
        "状态: 稳定\n最后更新: 2026-01-15\n下次复核: 2027-07-15\n优先级: 中\n标签: 知识库\n",
        ""), "error"),
    ("KB-3.3-missing-status", lambda c: c.replace("状态: 稳定\n", ""), "error"),
    ("KB-3.3-missing-last-updated", lambda c: c.replace("最后更新: 2026-01-15\n", ""), "error"),
    ("KB-3.3-missing-next-review", lambda c: c.replace("下次复核: 2027-07-15\n", ""), "error"),
    ("KB-3.4-priority-value", lambda c: c.replace("优先级: 中", "优先级: 紧急"), "error"),
    ("KB-3.4-tag-format", lambda c: c.replace("标签: 知识库", "标签: ABCDEFGHIJKLMO,大写"),
     "error"),
    ("KB-3.5-status-value", lambda c: c.replace("状态: 稳定", "状态: 完成"), "error"),
    ("KB-3.5-date-format", lambda c: c.replace("最后更新: 2026-01-15", "最后更新: 2026/1/15"),
     "error"),
    ("KB-3.5-review-order", lambda c: c.replace("下次复核: 2027-07-15", "下次复核: 2025-07-15"),
     "error"),
    ("KB-3.5-dup-key", lambda c: c.replace("优先级: 中", "状态: 草案"), "error"),
    # --- §5 标题与骨架 ---
    ("KB-5.1-one-h1", lambda c: c + "\n\n# 第二个一级标题\n", "error"),
    ("KB-5.1-level-jump", lambda c: c.replace("## 概念与结构", "### 概念与结构", 1), "error"),
    ("KB-5.1-adjacent-same-level", lambda c: c.replace(
        "## 概念与结构\n", "## 概念与结构\n\n## 多出来的小节\n"), "error"),
    ("KB-5.1-dup-heading", lambda c: c.replace("## 常见问题", "## 概念与结构"), "error"),
    ("KB-5.1-heading-links", lambda c: c.replace(
        "## 操作步骤", "## [操作步骤](./02-节点连接方式.md)"), "error"),
    ("KB-5.1-heading-punct", lambda c: c.replace("## 操作步骤", "## 操作步骤。"), "error"),
    ("KB-5.2-summary-required", lambda c: c.replace(SUMMARY_LINE, SUMMARY_LINE[2:]), "error"),
    ("KB-5.2-summary-length", lambda c: c.replace(SUMMARY_LINE, "> 太短"), "error"),
    ("KB-5.2-empty-section", lambda c: c.replace(
        "## 常见问题\n\n" + P3, "## 常见问题\n\n"), "error"),
    ("KB-5.2-group-intro", lambda c: c.replace(
        "## 常见问题\n\n", "## 常见问题\n\n### 占位小节\n\n"), "warning"),
    ("KB-5.2-placeholder", lambda c: c + "\nTODO: 补充附录\n", "error"),
    # --- §6 链接 ---
    ("KB-6.1-rel-prefix", lambda c: c.replace(
        "导出工作流时注意保留元数据字段",
        "参见[节点连接](02-节点连接方式.md)。导出工作流时注意保留元数据字段"), "error"),
    ("KB-6.1-abs-link", lambda c: c.replace(
        "导出工作流时注意保留元数据字段",
        "参见[节点连接](/01-基础概念/02-节点连接方式.md)。导出工作流时注意保留元数据字段"),
     "error"),
    ("KB-6.1-wikilink", lambda c: c.replace(
        "第三步是团队共享前的清理。", "第三步是[[节点连接方式]]的清理。"), "error"),
    ("KB-6.1-generic-text", lambda c: c.replace(
        "导出工作流时注意保留元数据字段",
        "详情见[更多](./02-节点连接方式.md)。导出工作流时注意保留元数据字段"), "error"),
    ("KB-6.1-escapes-root", lambda c: c.replace(
        "导出工作流时注意保留元数据字段",
        "参见[外部](../../outside/other.md)。导出工作流时注意保留元数据字段"), "error"),
    ("KB-6.1-broken-target", lambda c: c.replace(
        "导出工作流时注意保留元数据字段",
        "参见[不存在的文档](./99-不存在.md)。导出工作流时注意保留元数据字段"), "error"),
    ("KB-6.2-style-mix", lambda c: c.replace(
        "导出工作流时注意保留元数据字段",
        "参见[甲](./02-节点连接方式.md)与[乙](../01-基础概念/02-节点连接方式.md)。"
        "导出工作流时注意保留元数据字段"), "error"),
    # --- §7 相关文档 ---
    ("KB-7-title-exact", lambda c: c.replace("## 相关文档", "## 相关文档（必读）"), "error"),
    ("KB-7-format", lambda c: c.replace(REL_1, "相关文档包括节点连接方式等三篇。"), "error"),
    ("KB-7-broken-target", lambda c: c.replace(REL_1, "- [节点连接方式](./99-不存在.md)"),
     "error"),
    ("KB-7-non-doc-target", lambda c: c.replace(REL_1, "- [外部资料](https://example.com/doc)"),
     "error"),
    ("KB-7-self-link", lambda c: c.replace(
        REL_1, "- [安装与环境配置](./01-安装与环境配置.md)"), "error"),
    ("KB-7-dup-target", lambda c: c.replace(
        REL_3, "- [重复条目](./02-节点连接方式.md)"), "error"),
    ("KB-7-missing", lambda c: c.split("\n## 相关文档")[0] + "\n", "warning"),
    # --- §8 篇幅 ---
    ("KB-8-over-limit", lambda c: c + "长" * 5200, "error"),
    ("KB-8-under-limit", lambda c: c.replace(BODY, "").replace(P2, "").replace(P3, ""),
     "error"),
    ("KB-8-list-min", lambda c: c.replace(
        "按顺序执行以下步骤，每步完成后核对输出。",
        "要点如下：\n\n- 只有一项"), "error"),
    ("KB-8-list-two", lambda c: c.replace(
        "按顺序执行以下步骤，每步完成后核对输出。",
        "要点如下：\n\n- 第一步导出\n- 第二步校验"), "warning"),
    # --- §9 表格 ---
    ("KB-9-header-sep", lambda c: c.replace(
        "| 项目 | 说明 | 状态 |\n|---|---|---|\n",
        "| 项目 | 说明 | 状态 |\n| 表头两连 | 缺分隔行 | 会被判坏表 |\n"), "error"),
    ("KB-9-column-count", lambda c: c.replace(
        "| 节点图 | 工作流的核心结构 | 稳定 |", "| 节点图 | 工作流的核心结构 |"), "error"),
    ("KB-9-media-in-cell", lambda c: c.replace(
        "| 节点图 | 工作流的核心结构 | 稳定 |", "| 节点图 | 多段落<br>单元格 | 稳定 |"),
     "error"),
    # --- §10 代码块 ---
    ("KB-10-language-tag", lambda c: c.replace("```bash", "```"), "error"),
    ("KB-10-prompt-strip", lambda c: c.replace(
        "```bash\npython -m workflow_cli export --clean",
        "```bash\n$ python -m workflow_cli export --clean"), "error"),
    ("KB-10-image-in-code", lambda c: c.replace(
        "python -m workflow_cli validate ./out",
        "python -m workflow_cli validate ./out\n![截图](../assets/01-流程.png)"), "error"),
    # --- §11 图片 ---
    ("KB-11-external-image", lambda c: c.replace(
        "按顺序执行以下步骤，每步完成后核对输出。",
        "![界面示意](https://cdn.example.com/ui.png)\n\n_图 1：界面布局_\n\n"
        "按顺序执行以下步骤，每步完成后核对输出。"), "error"),
    ("KB-11-asset-location", lambda c: c.replace(
        "按顺序执行以下步骤，每步完成后核对输出。",
        "![界面示意](./01-流程.png)\n\n_图 1：界面布局_\n\n"
        "按顺序执行以下步骤，每步完成后核对输出。"), "error"),
    ("KB-11-alt-text", lambda c: c.replace(
        "按顺序执行以下步骤，每步完成后核对输出。",
        "![](../assets/01-流程.png)\n\n_图 1：界面布局_\n\n"
        "按顺序执行以下步骤，每步完成后核对输出。"), "error"),
    ("KB-11-asset-name", lambda c: c.replace(
        "按顺序执行以下步骤，每步完成后核对输出。",
        "![界面示意](../assets/01-流程.PNG)\n\n_图 1：界面布局_\n\n"
        "按顺序执行以下步骤，每步完成后核对输出。"), "error"),
    ("KB-11-caption", lambda c: c.replace(
        "按顺序执行以下步骤，每步完成后核对输出。",
        "![界面示意](../assets/01-流程.png)\n\n按顺序执行以下步骤，每步完成后核对输出。"),
     "error"),
    ("KB-11-figure-numbering", lambda c: c.replace(
        "按顺序执行以下步骤，每步完成后核对输出。",
        "![界面示意](../assets/01-流程.png)\n\n_图 1：界面布局_\n\n"
        "![导出结果](../assets/02-导出.png)\n\n_图 3：导出结果_\n\n"
        "按顺序执行以下步骤，每步完成后核对输出。"), "error"),
    ("KB-11-fixed-size", lambda c: c.replace(
        "按顺序执行以下步骤，每步完成后核对输出。",
        '<img src="../assets/01-流程.png" width="300">\n\n'
        "按顺序执行以下步骤，每步完成后核对输出。"), "error"),
    # --- §12 生命周期 ---
    ("KB-12.2-deprecation-block", lambda c: c.replace("状态: 稳定", "状态: 已废弃"), "error"),
    ("KB-12.2-replacement-valid", lambda c: c.replace(
        "状态: 稳定", "状态: 已废弃").replace(
        SUMMARY_LINE,
        "> ⚠️ 已废弃：内容过时；替代文档见 [不存在的文档](./99-x.md)。\n\n" + SUMMARY_LINE),
     "error"),
    ("KB-12.2-review-overdue-monitored", lambda c: c.replace(
        "状态: 稳定", "状态: 需监控").replace("下次复核: 2027-07-15", "下次复核: 2026-01-01"),
     "error"),
    # --- §13 检索友好 ---
    ("KB-13-generic-title", lambda c: base_doc("其他", ["./02-节点连接方式.md"]), "error"),
    ("KB-13-stacked-keywords", lambda c: base_doc(
        "工作流入门教程指南", ["./02-节点连接方式.md"]), "error"),
    # --- warning / review 档抽样 ---
    ("KB-5.1-question-heading", lambda c: c.replace("## 操作步骤", "## 如何操作？"),
     "warning"),
    ("KB-5.1-max-depth", lambda c: c.replace("## 常见问题\n", "## 常见问题\n\n##### 深层小节\n"),
     "warning"),
    ("KB-6.2-link-count", lambda c: c.replace(REL_1 + "\n", "").replace(REL_2 + "\n", ""),
     "warning"),
    ("KB-8-length-range", lambda c: c.split("\n## 常见问题")[0] + "\n", "warning"),
    ("KB-12.2-review-overdue", lambda c: c.replace(
        "下次复核: 2027-07-15", "下次复核: 2026-01-01"), "warning"),
    ("KB-3.2-prose-value", lambda c: c.replace(
        "优先级: 中", "优先级: 中；备注" + "备注内容很长" * 12), "review"),
    ("KB-8-summary-heading", lambda c: c.replace("## 相关文档", "## 总结"), "review"),
    ("KB-9-cell-density", lambda c: c.replace(
        "| 节点图 | 工作流的核心结构 | 稳定 |", "| 节点图 | 结构核心；承载逻辑 | 稳定 |"),
     "review"),
    ("KB-9-missing-unit", lambda c: c.replace(
        "| 项目 | 说明 | 状态 |", "| 耗时 | 说明 | 状态 |"), "review"),
    ("KB-10-secret-scan", lambda c: c.replace(
        "python -m workflow_cli validate ./out",
        'api_key = "sk-abcdefghijklmnop1234"'), "review"),
    ("KB-11-alt-quality", lambda c: c.replace(
        "按顺序执行以下步骤，每步完成后核对输出。",
        "![截图](../assets/01-流程.png)\n\n_图 1：界面布局_\n\n"
        "按顺序执行以下步骤，每步完成后核对输出。"), "review"),
    ("KB-13-opaque-title", lambda c: base_doc("AB", ["./02-节点连接方式.md"]), "review"),
    ("KB-13-title-body-mismatch", lambda c: c.replace(
        "## 概念与结构\n",
        "这段文字描述了一些背景情况与阅读前提。\n\n## 概念与结构\n"), "review"),
]


@pytest.mark.parametrize("rule_id,mutate,severity", DOC_MUTATIONS,
                         ids=[m[0] for m in DOC_MUTATIONS])
def test_doc_mutation_fires_rule(rule_id, mutate, severity):
    files = project_files()
    findings = check_document(parse_document(TARGET_DOC, mutate(files[TARGET_DOC])),
                              doc_ctx(files))
    hits = fired(findings, rule_id)
    assert hits, f"期望触发 {rule_id}，实际触发：{sorted({f.rule_id for f in findings})}"
    assert all(f.severity == severity for f in hits), \
        [(f.rule_id, f.severity) for f in hits]


def test_update_semantics_review_needs_mtime():
    files = project_files()
    ctx = doc_ctx(files, mtime_date=lambda p: date(2027, 1, 1))
    findings = check_document(parse_document(TARGET_DOC, files[TARGET_DOC]), ctx)
    assert fired(findings, "KB-3.5-update-semantics")


def test_missing_frontmatter_is_rejected_not_skipped():
    from agent_app.kb_spec.frontmatter import parse_frontmatter
    fm, findings, body = parse_frontmatter("正文没有 frontmatter")
    assert fm is None and findings == [] and body == 1


# ---------------------------------------------------------------- 项目级变异表

PROJ_MUTATIONS = [
    ("KB-1-root-only-md", lambda f: {**f, "notes.md": "# 根目录散落文档\n\n> 说明。\n"}),
    ("KB-1-has-category", lambda f: {"README.md": f["README.md"]}),
    ("KB-1-nested-category", lambda f: {
        **f, "01-基础概念/子目录/01-x.md": f[TARGET_DOC]}),
    ("KB-1-category-size", lambda f: {
        k: v for k, v in f.items() if not k.startswith("03-应用实践/02")
        and not k.startswith("03-应用实践/03")}),
    ("KB-2.1-h1", lambda f: {
        k: v for k, v in f.items() if k != "README.md"}),
    ("KB-2.1-intro-quote", lambda f: {
        **f, "README.md": f["README.md"].replace(
            "> 覆盖工作流从安装到发布全流程的知识库项目，供自动化校验测试使用。\n", "")}),
    ("KB-2.1-grouped-list", lambda f: {
        **f, "README.md": f["README.md"].replace("## 02-核心技术\n", "")}),
    ("KB-2.1-entry-format", lambda f: {
        **f, "README.md": f["README.md"].replace(
            "](./01-基础概念/01-安装与环境配置.md) — 一句话说明",
            "](./01-基础概念/01-安装与环境配置.md)")}),
    ("KB-2.2-coverage", lambda f: {
        **f, "README.md": f["README.md"].replace(
            "- [插件管理机制](./01-基础概念/03-插件管理机制.md) — 一句话说明\n", "")}),
    ("KB-2.2-broken-link", lambda f: {
        **f, "README.md": f["README.md"].replace(
            "./01-基础概念/02-节点连接方式.md", "./01-基础概念/99-不存在.md")}),
    ("KB-3.1-readme-frontmatter", lambda f: {
        **f, "README.md": "---\n状态: 稳定\n---\n\n" + f["README.md"]}),
    ("KB-2.2-length", lambda f: {
        **f, "README.md": f["README.md"] + "\n".join(f"行{i}" for i in range(160))}),
    ("KB-2.2-no-body", lambda f: {
        **f, "README.md": f["README.md"] + "\n```\ncode\n```\n"}),
    ("KB-4.1-charset", lambda f: {**f, "01-基础概念/坏 名字.md": f[TARGET_DOC]}),
    ("KB-4.1-dot-tilde-prefix", lambda f: {**f, "~backup.md": f[TARGET_DOC]}),
    ("KB-4.2-name-length", lambda f: {**f, "01-基础概念/" + "长" * 61 + ".md": f[TARGET_DOC]}),
    ("KB-4.2-version-suffix", lambda f: {**f, "01-基础概念/01-安装-v2.md": f[TARGET_DOC]}),
    ("KB-4.2-uninformative", lambda f: {**f, "01-基础概念/01-未命名.md": f[TARGET_DOC]}),
    ("KB-4.2-doc-numbering", lambda f: {**f, "01-基础概念/安装说明.md": f[TARGET_DOC]}),
    ("KB-4.2-lowercase", lambda f: {**f, "01-基础概念/02-Note.md": f[TARGET_DOC]}),
    ("KB-6.2-orphan", lambda f: {
        **f,
        "README.md": f["README.md"].replace(
            "- [质量抽检流程](./03-应用实践/02-质量抽检流程.md) — 一句话说明\n", ""),
        "03-应用实践/01-素材批量生产.md": f["03-应用实践/01-素材批量生产.md"].replace(
            "- [质量抽检流程](./02-质量抽检流程.md)\n", ""),
        "03-应用实践/03-发布与归档方式.md": f["03-应用实践/03-发布与归档方式.md"].replace(
            "- [质量抽检流程](./02-质量抽检流程.md)\n", ""),
    }),
    ("KB-12.1-readme-status-mark", lambda f: {
        **f, "03-应用实践/02-质量抽检流程.md": f[
            "03-应用实践/02-质量抽检流程.md"].replace("状态: 稳定", "状态: 草案")}),
    ("KB-12.2-archive-group", lambda f: {
        **f, "03-应用实践/02-质量抽检流程.md": f[
            "03-应用实践/02-质量抽检流程.md"].replace("状态: 稳定", "状态: 已归档")}),
    ("KB-13-section-name-mismatch", lambda f: {
        **f, "README.md": f["README.md"].replace("## 01-基础概念", "## 基础概念")}),
    ("KB-8-topic-overlap", lambda f: {
        **f, "02-核心技术/02-变量传递原理.md": f[
            "02-核心技术/02-变量传递原理.md"].replace(
            "# 变量传递原理", "# 批处理调度方案")}),
    ("KB-13-naming-consistency", lambda f: {
        **f, "02-核心技术/02-变量传递原理.md": f[
            "02-核心技术/02-变量传递原理.md"].replace(
            "# 变量传递原理", "# 工作流结构解析方法")}),
]

CATEGORY_RULES = {
    "KB-1-empty-category": lambda dirs: dirs | {"99-空分类"},
    "KB-4.2-category-naming": lambda dirs: (dirs - {"03-应用实践"}) | {"应用实践"},
}


@pytest.mark.parametrize("rule_id,mutate", PROJ_MUTATIONS,
                         ids=[m[0] for m in PROJ_MUTATIONS])
def test_project_mutation_fires_rule(rule_id, mutate):
    files = mutate(project_files())
    findings = all_findings(files)
    hits = fired(findings, rule_id)
    assert hits, f"期望触发 {rule_id}，实际触发：{sorted({f.rule_id for f in findings})}"


@pytest.mark.parametrize("rule_id,dirs_fn", list(CATEGORY_RULES.items()),
                         ids=list(CATEGORY_RULES))
def test_category_level_rules(rule_id, dirs_fn):
    files = project_files()
    dirs = {p.rsplit("/", 1)[0] for p in files if "/" in p}
    findings = all_findings(files, dirs=dirs_fn(dirs))
    assert fired(findings, rule_id)


def test_internal_dir_linked_is_error():
    files = project_files()
    files["README.md"] = files["README.md"].replace(
        "](./01-基础概念/01-安装与环境配置.md)",
        "](./_drafts/01-草稿.md)")
    findings = all_findings(files, extra_paths={"_drafts/01-草稿.md"})
    assert fired(findings, "KB-4.1-internal-linked")


def test_case_collide_is_error():
    files = project_files()
    findings = all_findings(
        files,
        extra_paths={"01-基础概念/01-安装与环境配置2.md", "01-基础概念/01-安装与环境配置2.MD"})
    hits = fired(findings, "KB-4.2-case-collide")
    assert hits and any("01-安装与环境配置2.MD" in f.message for f in hits)
