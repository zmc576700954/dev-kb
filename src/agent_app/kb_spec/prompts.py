"""MCP Prompts 模板：六个场景化引导（决策 Q5）。

每个模板是纯文本 prompt，agent 客户端拿到后自行决定是否用 LLM 展开。
模板内容机械生成，不依赖 LLM。
"""

from __future__ import annotations

from .vocab import Finding


def kb_write_guide(topic: str, project: str, category: str | None = None,
                   existing_docs: list[str] | None = None) -> str:
    """写作前引导：分类定位 + 查重 + 骨架建议 + 规范检查清单。"""
    lines = [
        f"# 写作引导：{topic}",
        "",
        f"项目：{project}",
        f"目标分类：{category or '待确定（根据主题关键词自动匹配）'}",
        "",
        "## 第一步：查重",
        "",
    ]
    if existing_docs:
        lines.append("以下既有文档与主题相关，请确认是否需要更新而非新建：")
        for doc in existing_docs:
            lines.append(f"- {doc}")
    else:
        lines.append("未发现同主题文档，可以新建。")
    lines.extend([
        "",
        "## 第二步：确定文档类型",
        "",
        "根据主题选择文档类型，对应不同骨架：",
        "- 技术文档：概念→原理→实现→示例→边界",
        "- 操作指南：前置条件→步骤→验证→常见问题",
        "- 参考手册：概述→详细说明→示例→相关文档",
        "- 问题排查：现象→原因→解决方案→预防措施",
        "- 默认：概念→正文→示例→相关文档",
        "",
        "## 第三步：生成骨架",
        "",
        "调用 `kb_draft_document` 生成合规骨架，或手动按以下结构编写：",
        "",
        "```markdown",
        "---",
        "状态: 草案",
        f"最后更新: {__import__('datetime').date.today().isoformat()}",
        "下次复核: 按需",
        "---",
        "",
        "# 文档标题（包含核心名词）",
        "",
        "> 一句话摘要（30–80字，含1–3个检索关键词）",
        "",
        "## 核心概念",
        "",
        "正文……",
        "",
        "## 相关文档",
        "",
        "- [相关文档标题](./路径.md)",
        "```",
        "",
        "## 规范检查清单",
        "",
        "- [ ] 恰好一个 H1，位于 frontmatter 之后",
        "- [ ] H1 后紧跟 `> ` 摘要（30–80字）",
        "- [ ] 标题层级不跳级，无相邻同级标题",
        "- [ ] 无空章节、无 TODO/占位符（草案除外）",
        "- [ ] 站内链接以 ./ 或 ../ 开头",
        "- [ ] 相关文档段为无序列表，3–8 条",
        "- [ ] 代码块标注语言，无提示符",
        "- [ ] 表格有分隔行，列数一致",
        "",
        "## 第四步：提交前校验",
        "",
        "调用 `kb_validate_document` 预检，消除全部 error 级违规后提交提案。",
    ])
    return "\n".join(lines)


def kb_review_guide(findings: list[dict]) -> str:
    """校验结果解读：按严重级分组 + 修复优先级排序。"""
    errors = [f for f in findings if f["severity"] == "error"]
    warnings = [f for f in findings if f["severity"] == "warning"]
    reviews = [f for f in findings if f["severity"] == "review"]
    lines = [
        "# 校验结果解读",
        "",
        f"发现 {len(errors)} 个必须修复的问题、{len(warnings)} 个建议优化项、{len(reviews)} 个需判断项。",
        "",
    ]
    if errors:
        lines.extend([
            "## 必须修复（error）",
            "",
            "以下问题违反规范硬性要求，修复前不得交付：",
            "",
        ])
        for f in errors:
            lines.append(f"### {f['rule_id']}")
            lines.append(f"- **位置**：{f.get('location', '未知')}")
            lines.append(f"- **问题**：{f['message']}")
            if f.get("fix_hint"):
                lines.append(f"- **修复**：{f['fix_hint']}")
            lines.append("")
    if warnings:
        lines.extend([
            "## 建议优化（warning）",
            "",
            "以下偏离建议做法，不阻断交付但建议处理：",
            "",
        ])
        for f in warnings:
            lines.append(f"- **{f['rule_id']}**：{f['message']}")
            if f.get("fix_hint"):
                lines.append(f"  建议：{f['fix_hint']}")
        lines.append("")
    if reviews:
        lines.extend([
            "## 需判断（review）",
            "",
            "以下条目含主观判断，请人工确认：",
            "",
        ])
        for f in reviews:
            lines.append(f"- **{f['rule_id']}**：{f['message']}")
            if f.get("evidence"):
                lines.append(f"  证据：{f['evidence']}")
        lines.append("")
    lines.extend([
        "## 修复优先级",
        "",
        "1. 先修 error：缺 frontmatter → 空章节 → 断链 → 命名违规 → 其他",
        "2. 再修 warning：链接数量、摘要长度、段落长度",
        "3. 最后处理 review：主题重合、术语一致性、凭据扫描",
    ])
    return "\n".join(lines)


def kb_fix_guide(finding: dict) -> str:
    """单个违规的修复引导：具体步骤 + before/after 示例。"""
    rule_id = finding["rule_id"]
    lines = [
        f"# 修复引导：{rule_id}",
        "",
        f"**问题**：{finding['message']}",
        f"**位置**：{finding.get('location', '未知')}",
        "",
    ]
    # 按规则 ID 提供具体修复步骤
    fixes = {
        "KB-3.1-doc-missing-fm": [
            "## 修复步骤",
            "",
            "1. 在文件第一行插入 frontmatter 块",
            "2. 填写必填键：状态、最后更新、下次复核",
            "3. 可选键：优先级（高/中/低）、标签（逗号分隔小写词）",
            "",
            "## 示例",
            "",
            "Before:",
            "```markdown",
            "# 文档标题",
            "```",
            "",
            "After:",
            "```markdown",
            "---",
            "状态: 草案",
            "最后更新: 2026-11-13",
            "下次复核: 按需",
            "---",
            "",
            "# 文档标题",
            "```",
        ],
        "KB-5.2-empty-section": [
            "## 修复步骤",
            "",
            "1. 在标题下添加正文内容",
            "2. 如果标题只是分组占位，考虑删除该标题，把子标题提升一级",
            "3. 如果内容在其他文档中，添加链接引用",
            "",
            "## 示例",
            "",
            "Before:",
            "```markdown",
            "## 市场规模",
            "",
            "### 2.1 规模",
            "```",
            "",
            "After:",
            "```markdown",
            "## 市场规模",
            "",
            "2025–2026 年 AI 视频工具市场呈现爆发式增长，本节梳理关键数据与趋势。",
            "",
            "### 2.1 规模",
            "```",
        ],
        "KB-10-language-tag": [
            "## 修复步骤",
            "",
            "1. 为每个代码块标注语言（```bash、```python、```json 等）",
            "2. 语言标识写在 opening fence 的 ``` 之后",
            "",
            "## 示例",
            "",
            "Before:",
            "```markdown",
            "```",
            "python -m workflow_cli export",
            "```",
            "```",
            "",
            "After:",
            "```markdown",
            "```bash",
            "python -m workflow_cli export",
            "```",
            "```",
        ],
    }
    if rule_id in fixes:
        lines.extend(fixes[rule_id])
    else:
        lines.extend([
            "## 通用修复步骤",
            "",
            "1. 阅读规范对应章节，理解规则意图",
            "2. 根据 fix_hint 调整内容",
            "3. 调用 kb_validate_document 验证修复结果",
        ])
    if finding.get("fix_hint"):
        lines.extend(["", f"**修复提示**：{finding['fix_hint']}"])
    return "\n".join(lines)


def kb_migrate_guide(project: str, stats: dict) -> str:
    """迁移/重构引导：结构分析 + 迁移计划 + 风险点。"""
    lines = [
        f"# 迁移引导：{project}",
        "",
        "## 当前结构分析",
        "",
        f"- 文档总数：{stats.get('documents', '?')}",
        f"- 分类目录：{stats.get('categories', '?')}",
        f"- Error 级违规：{stats.get('errors', '?')}",
        f"- Warning 级偏离：{stats.get('warnings', '?')}",
        "",
        "## 迁移计划",
        "",
        "按以下顺序执行，每步完成后校验：",
        "",
        "1. **补 frontmatter**：为所有文档添加元数据块",
        "2. **修空章节**：为分组标题添加过渡正文",
        "3. **标代码块语言**：为所有 ``` 块添加语言标识",
        "4. **建相关文档段**：为每篇文档添加文末链接段",
        "5. **修断链**：确保所有链接目标存在",
        "6. **统一命名**：消除版本后缀、无信息词、大小写混用",
        "",
        "## 风险点",
        "",
        "- 大量 frontmatter 修改会刷新文件 mtime，注意 `最后更新` 字段的语义（登记日期 ≠ 文件修改时间）",
        "- 移动文档时需同步更新全库入链，使用 kb_propose_move 而非手动 mv",
        "- 删除文档前必须确认无入链，使用 kb_propose_delete 并填写原因",
    ]
    return "\n".join(lines)


def kb_style_guide(content: str = "") -> str:
    """风格统一引导：标题、摘要、链接、代码块、表格的检查清单。"""
    lines = [
        "# 风格检查清单",
        "",
        "## 标题",
        "",
        "- [ ] 恰好一个 H1，包含核心名词",
        "- [ ] 标题为名词短语或任务短语，不写疑问句",
        "- [ ] 标题层级连续（## → ###，不跳级）",
        "- [ ] 同一父级下标题文字不重复",
        "- [ ] 标题不以句号结尾",
        "",
        "## 摘要",
        "",
        "- [ ] H1 后紧跟 `> ` 摘要",
        "- [ ] 摘要 30–80 字",
        "- [ ] 摘要含 1–3 个检索关键词",
        "- [ ] 摘要说明结论或用途，不复述标题",
        "",
        "## 链接",
        "",
        "- [ ] 站内链接以 ./ 或 ../ 开头",
        "- [ ] 链接文字描述目标内容（不写「点击这里」「详见」）",
        "- [ ] 相关文档段为无序列表，3–8 条",
        "- [ ] 相关文档描述 ≤30 字，写清为什么相关",
        "",
        "## 代码块",
        "",
        "- [ ] 每个代码块标注语言",
        "- [ ] 命令块可直接复制执行（无 $ 提示符）",
        "- [ ] 密钥/令牌/域名替换为占位符",
        "- [ ] 单块代码 ≤60 行",
        "",
        "## 表格",
        "",
        "- [ ] 表头下一行是分隔行（|---|）",
        "- [ ] 每行列数与表头一致",
        "- [ ] 数值列标注单位",
        "- [ ] 单元格只放一个事实",
        "- [ ] 列数 ≤6，行数 ≤8（叙述性内容改为正文）",
    ]
    return "\n".join(lines)


def kb_onboard_guide(project: str, stats: dict) -> str:
    """新项目接入引导：全库体检 + 违规统计 + 修复路线图。"""
    lines = [
        f"# 项目接入引导：{project}",
        "",
        "## 全库体检结果",
        "",
        f"- 文档总数：{stats.get('documents', '?')}",
        f"- 分类目录：{stats.get('categories', '?')}",
        f"- Error：{stats.get('errors', '?')} | Warning：{stats.get('warnings', '?')} | Review：{stats.get('reviews', '?')}",
        "",
        "## 修复路线图（按工作量排序）",
        "",
        "### 阶段一：基础合规（预计 1–2 小时）",
        "",
        "1. 为所有文档添加 frontmatter（可用脚本批量生成草案状态）",
        "2. 为代码块添加语言标注",
        "3. 修复断链（README 与正文）",
        "",
        "### 阶段二：结构优化（预计 2–4 小时）",
        "",
        "4. 为空章节添加过渡正文",
        "5. 为每篇文档添加相关文档段",
        "6. 统一文件命名（消除版本后缀、无信息词）",
        "",
        "### 阶段三：质量提升（预计 4–8 小时）",
        "",
        "7. 拆分超长文档（>5000字）",
        "8. 合并过短文档（<300字）",
        "9. 统一术语与概念叫法",
        "10. 补充检索关键词到标题与摘要",
        "",
        "## 工具使用",
        "",
        "- 全库校验：`kb_validate_project`",
        "- 单篇校验：`kb_validate_document`",
        "- 查重检索：`kb_search_guided`",
        "- 生成骨架：`kb_draft_document`",
        "- 提交变更：`kb_propose_*`（产出提案，人工审批后落盘）",
    ]
    return "\n".join(lines)


PROMPTS = {
    "kb_write_guide": {
        "name": "kb_write_guide",
        "description": "写作前引导：分类定位、查重、骨架建议、规范检查清单",
        "arguments": [
            {"name": "topic", "description": "写作主题", "required": True},
            {"name": "project", "description": "目标项目", "required": True},
            {"name": "category", "description": "目标分类（可选）", "required": False},
        ],
    },
    "kb_review_guide": {
        "name": "kb_review_guide",
        "description": "校验结果解读：按严重级分组、修复优先级排序",
        "arguments": [
            {"name": "findings", "description": "校验结果 JSON（findings 数组）", "required": True},
        ],
    },
    "kb_fix_guide": {
        "name": "kb_fix_guide",
        "description": "单个违规的修复引导：具体步骤与 before/after 示例",
        "arguments": [
            {"name": "finding", "description": "单个 finding 对象（含 rule_id、message、location）", "required": True},
        ],
    },
    "kb_migrate_guide": {
        "name": "kb_migrate_guide",
        "description": "迁移/重构引导：结构分析、迁移计划、风险点",
        "arguments": [
            {"name": "project", "description": "项目名", "required": True},
            {"name": "stats", "description": "项目统计（documents、categories、errors、warnings）", "required": True},
        ],
    },
    "kb_style_guide": {
        "name": "kb_style_guide",
        "description": "风格统一检查清单：标题、摘要、链接、代码块、表格",
        "arguments": [],
    },
    "kb_onboard_guide": {
        "name": "kb_onboard_guide",
        "description": "新项目接入引导：全库体检、违规统计、修复路线图",
        "arguments": [
            {"name": "project", "description": "项目名", "required": True},
            {"name": "stats", "description": "项目统计", "required": True},
        ],
    },
}
