"""kb_spec：知识库写作与管理规范的机械化执行层。

规范源：spec/kb-agent-spec.md（vendor 副本 + sha256，见 SPEC_VERSION.json）。
模块：frontmatter/docmodel（解析）→ checks_doc/checks_project（校验）→
operations（语义操作规划）→ proposals（提案存储）→ service（编排）→
cli/mcp_server（两个人口）。
"""

from .vocab import Finding, summarize  # noqa: F401
