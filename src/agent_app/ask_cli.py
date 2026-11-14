"""M3 问答入口：混合检索 + 读取原文件返回真实代码段。

默认要求唯一确定项目；确需跨项目检索时使用 --all-projects 显式选择。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from agent_app.indexing.metadata_store import MetadataStore
from agent_app.tools.projects import load_registry
from agent_app.tools.retrieval import HybridSearch
from agent_app.tools.routing import matching_projects, route_project
from agent_app.tools.source import read_source

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def detect_project(question: str, registry: dict) -> str | None:
    """保留旧调用接口：只有唯一项目名匹配时才返回项目。"""
    matches = matching_projects(question, registry)
    return matches[0] if len(matches) == 1 else None


def main() -> int:
    parser = argparse.ArgumentParser(description="研发知识库问答（M3）")
    parser.add_argument("question")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--project", help="限定项目（默认只在唯一识别项目名时自动限定）")
    scope.add_argument("--all-projects", action="store_true", help="显式搜索全部登记项目")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--no-source", action="store_true", help="不回读原文件（仅用索引内容）")
    parser.add_argument("--no-llm", action="store_true", help="只输出证据包，不做 LLM 归纳")
    parser.add_argument("--no-rerank", action="store_true", help="关闭 bge-reranker 精排（默认开启，blend=0.3）")
    parser.add_argument("--no-expand", action="store_true", help="关闭 LLM 查询扩展（默认开启，仅代码定位意图时生效）")
    args = parser.parse_args()

    registry = load_registry()
    route = route_project(args.question, registry, args.project, args.all_projects)
    if route["status"] != "ok":
        print(route["message"])
        print("候选项目：" + "、".join(route["candidates"]))
        print("请使用 --project <项目名>，或显式使用 --all-projects。")
        return 2
    project = route["project"]

    store = MetadataStore(DATA_DIR / "meta.db")
    from agent_app.indexing.vector_store import VectorStore
    vs = VectorStore(DATA_DIR / "qdrant")
    engine = HybridSearch(store, vs, rerank=not args.no_rerank, blend=0.3,
                          expand=not args.no_expand)

    results = engine.search(args.question, project=project, limit=args.limit)
    if not results:
        scope_name = f"项目 {project}" if project else "全部项目"
        print(f"⚠️ 在{scope_name}中未找到充分证据，无法回答。")
        return 1

    # LLM 归纳模式：证据包 → 自然语言答案
    from agent_app.llm import backend
    if not args.no_llm and backend.available():
        from agent_app.llm.answer import compose_answer
        print(f"问题：{args.question}")
        print(f"（检索 {len(results)} 条证据，由 {backend.backend_name()} 归纳；"
              f"代码段均来自原文件实时读取）\n")
        print(compose_answer(store, args.question, results))
        return 0

    print(f"问题：{args.question}")
    print(f"检索范围：{project or '全部项目（显式选择）'} | 命中 {len(results)} 条证据\n")
    print("=" * 70)

    for i, r in enumerate(results, 1):
        loc = f"{r['project']}/{r['path']}:{r['start_line']}-{r['end_line']}"
        sym = f" [{r['symbol']}]" if r["symbol"] else ""
        via = "+".join(r["matched_by"])
        print(f"\n── 证据 {i}（{via}，相关度 {r['score']}）──")
        print(f"📄 {loc}{sym}")

        if args.no_source:
            print(r["content"][:800])
            continue

        src = read_source(store, r["project"], r["path"], r["start_line"], r["end_line"])
        if "error" in src:
            print(f"⚠️ 原文件读取失败：{src['error']}（以下展示索引副本，可能滞后）")
            print(r["content"][:800])
        else:
            if src["stale_since_index"]:
                print("⚠️ 文件在索引后有改动，以下为原文件最新内容：")
            commit = f" @ {src['git_commit']}" if src["git_commit"] else ""
            print(f"（原文件共 {src['total_lines']} 行{commit}）")
            print(src["content"][:2000])

    print("\n" + "=" * 70)
    print("以上代码段均来自原文件实时读取，可凭路径与行号复核。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
