"""M3 验收题库：24 个真实开发问题，检查证据是否命中预期文件/符号。

判定：Top-5 证据中至少一条 path 或 symbol 包含任一预期关键词即为命中。
输出命中率与逐题结果，供人工抽查证据质量。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_app.indexing.metadata_store import MetadataStore
from agent_app.indexing.vector_store import VectorStore
from agent_app.tools.retrieval import HybridSearch

QUESTIONS = [
    # ---- new-bargain-laravel：代码定位 ----
    ("new-bargain-laravel", "AgentApiController 的 getAgentInfo 方法在哪", ["AgentApiController@getAgentInfo", "AgentApiController.php"]),
    ("new-bargain-laravel", "商品规格 GoodsSpecification 模型定义在哪里", ["GoodsSpecification"]),
    ("new-bargain-laravel", "用户登录认证的控制器在哪个文件", ["Auth", "Login", "login"]),
    ("new-bargain-laravel", "优惠券相关逻辑在哪个类处理", ["Coupon", "coupon"]),
    ("new-bargain-laravel", "订单创建的逻辑在哪里", ["Order", "order"]),
    ("new-bargain-laravel", "退款申请的处理逻辑在哪", ["refund", "Refund", "退款"]),
    ("new-bargain-laravel", "API 路由定义在哪个目录", ["routes/"]),
    # ---- tiangong：代码定位 + 语义 ----
    ("tiangong", "状态机 TRANSITION_RULES 在哪定义", ["state-machine"]),
    ("tiangong", "canTransition 函数在哪里", ["canTransition", "state-machine"]),
    ("tiangong", "validateNodeTypeTransition 的实现在哪", ["validateNodeTypeTransition"]),
    ("tiangong", "NodeContextMenu 组件在哪个文件", ["NodeContextMenu"]),
    ("tiangong", "graphStore 里 updateNode 是干什么的", ["updateNode", "graphStore"]),
    ("tiangong", "wiki 链接解析的逻辑在哪里", ["wiki", "Wiki"]),
    # ---- kaiwu ----
    ("kaiwu", "BM25 检索类在哪实现", ["BM25"]),
    ("kaiwu", "load_corpus 函数怎么加载语料", ["load_corpus"]),
    ("kaiwu", "RAG 评估是怎么做的", ["evaluate", "eval", "评估"]),
    # ---- repository-manage ----
    ("repository-manage", "搜索功能在哪个模块实现", ["search", "Search", "搜索"]),
    ("repository-manage", "synapse 相关的命令行入口在哪", ["synapse", "Cli", "Commands"]),
    ("repository-manage", "markdown 渲染在哪个目录", ["markdown", "Markdown"]),
    # ---- ai-video-kb：纯文档语义 ----
    ("ai-video-kb", "视频生成有哪些合规要求", ["合规", "08"]),
    ("ai-video-kb", "视频模型选型的文档在哪", ["模型", "04"]),
    # ---- 跨项目（不限定）----
    (None, "用户余额支付的接口逻辑", ["余额", "Balance", "balance", "Pay", "pay"]),
    (None, "i18n 国际化是怎么实现的", ["i18n", "I18n", "国际化", "locale"]),
    (None, "知识库文档的标准和规范", ["standards", "kb-standards", "标准"]),
]


def main():
    store = MetadataStore("data/meta.db")
    vs = VectorStore("data/qdrant")
    engine = HybridSearch(store, vs)

    hit = 0
    failures = []
    for project, q, expects in QUESTIONS:
        results = engine.search(q, project=project, limit=5)
        found = None
        for r in results:
            hay = f"{r['path']}|{r['symbol']}"
            if any(e in hay for e in expects):
                found = r
                break
        if found:
            hit += 1
            print(f"✅ [{project or '跨项目'}] {q}")
            print(f"   → {found['path']}:{found['start_line']}-{found['end_line']} [{found['symbol'] or '-'}] ({'+'.join(found['matched_by'])})")
        else:
            top = results[0] if results else None
            failures.append((project, q, top))
            print(f"❌ [{project or '跨项目'}] {q}")
            if top:
                print(f"   实际 Top1: {top['path']} [{top['symbol'] or '-'}]")
            else:
                print("   无任何证据返回")

    print(f"\n===== 命中率：{hit}/{len(QUESTIONS)} =====")
    return 0 if hit >= len(QUESTIONS) * 0.8 else 1


if __name__ == "__main__":
    raise SystemExit(main())
