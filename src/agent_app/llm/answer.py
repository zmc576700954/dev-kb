"""答案编排：证据包 → LLM 自然语言答案（强制引用证据，不足时明说）。

设计（对照规划）：
- 代码段来自 read_source 实时读取的原文件，LLM 只做选择与归纳。
- 提示词强制：只用证据、逐条编号引用、事实与建议分开、证据不足要明说。
"""

from __future__ import annotations

from ..indexing.metadata_store import MetadataStore
from ..tools.source import read_source

SYSTEM = """你是研发知识库助手。规则：
1. 只能基于下方编号证据回答，禁止编造任何文件、函数、行号或行为。
2. 每个关键结论后用 [证据N] 标注来源。
3. 结构：先一句话结论，再分点展开（位置 / 实现方式 / 调用关系），最后如有推断性建议，单独放在"建议"一节并说明是推断。
4. 证据不足以回答时，明确说"当前索引中证据不足"，并指出缺什么。
5. 用中文回答，代码标识符保留英文原文。"""


def compose_answer(store: MetadataStore, question: str, results: list[dict],
                   max_evidence: int = 6, excerpt_chars: int = 1400) -> str:
    """results 为 HybridSearch 结果；返回 LLM 答案文本（末附来源清单）。"""
    from . import backend

    blocks = []
    sources = []
    for i, r in enumerate(results[:max_evidence], 1):
        src = read_source(store, r["project"], r["path"], r["start_line"], r["end_line"])
        content = src.get("content", r["content"])[:excerpt_chars]
        stale = "（文件在索引后有改动，以下为最新内容）" if src.get("stale_since_index") else ""
        commit = src.get("git_commit", "")
        sym = f" [{r['symbol']}]" if r["symbol"] else ""
        blocks.append(
            f"【证据{i}】{r['project']}/{r['path']}:{r['start_line']}-{r['end_line']}{sym}"
            f"{('@' + commit) if commit else ''}{stale}\n{content}"
        )
        sources.append(f"[证据{i}] `{r['project']}/{r['path']}:{r['start_line']}-{r['end_line']}`"
                       + (f" `{r['symbol']}`" if r["symbol"] else ""))

    user = f"问题：{question}\n\n" + "\n\n".join(blocks)
    answer = backend.chat(SYSTEM, user)
    return answer.strip() + "\n\n—— 来源 ——\n" + "\n".join(sources)
