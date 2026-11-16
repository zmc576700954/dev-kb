"""M2 同步命令：全量/增量索引五个项目。

用法：
    python -m agent_app.sync_cli                     # 全量+增量同步全部项目（含向量）
    python -m agent_app.sync_cli --project tiangong  # 只同步一个项目
    python -m agent_app.sync_cli --no-embed          # 只建结构索引，不生成向量
    python -m agent_app.sync_cli --embed-pending     # 补挂未完成向量（可反复执行）
    python -m agent_app.sync_cli --backfill          # 按声明回填契约字段（不重扫、不重算向量）
    python -m agent_app.sync_cli --rebuild-fts       # 重建全文/标题链索引（不重算向量）
    python -m agent_app.sync_cli --stats             # 查看索引统计
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from agent_app.config import PROJECTS_EXAMPLE, default_projects_config, load_config
from agent_app.contract.declaration import aliases_in_content, category_of, load_declarations
from agent_app.indexing.metadata_store import MetadataStore
from agent_app.ingestion.sync import SyncEngine

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def main() -> int:
    parser = argparse.ArgumentParser(description="M2 索引同步")
    parser.add_argument("--config", default=str(default_projects_config()))
    parser.add_argument("--project", help="只同步指定项目")
    parser.add_argument("--no-embed", action="store_true", help="跳过向量生成")
    parser.add_argument("--embed-pending", action="store_true", help="只补挂未完成向量")
    parser.add_argument("--backfill", action="store_true",
                        help="按知识源声明回填契约字段（category/doc_role/aliases），不重扫不重算向量")
    parser.add_argument("--rebuild-fts", action="store_true",
                        help="重建全文索引与标题链索引，不重算向量")
    parser.add_argument("--stats", action="store_true")
    args = parser.parse_args()

    store = MetadataStore(DATA_DIR / "meta.db")

    if args.stats:
        for name, s in store.stats().items():
            print(f"{name}: files={s.get('files', 0)} chunks={s.get('chunks', 0)} "
                  f"待向量化={s.get('pending_embedding', 0)} 错误文件={s.get('error_files', 0)}")
        return 0

    vs = None
    if not args.no_embed:
        from agent_app.indexing.vector_store import VectorStore
        vs = VectorStore(DATA_DIR / "qdrant")

    declarations = load_declarations(args.config)

    if args.rebuild_fts:
        n = store.rebuild_fts()
        print(f"已重建全文/标题链索引：{n} 块")
        return 0

    if args.backfill:
        for name, decl in declarations.items():
            if args.project and name != args.project:
                continue
            terms = decl.terms()
            n = store.backfill_structure(
                name,
                category_of,
                decl.role_of,
                lambda rel, content: aliases_in_content(content, terms),
            )
            print(f"[{name}] 已回填契约字段：{n} 块")
        return 0

    engine = SyncEngine(store, vs, embed=not args.no_embed)

    if args.embed_pending:
        total = 0
        while True:
            n = engine.embed_pending(args.project)
            total += n
            print(f"补挂向量 {n} 块（累计 {total}）")
            if n == 0:
                break
        return 0

    projects = load_config(args.config, PROJECTS_EXAMPLE, "项目注册表")["projects"]
    for p in projects:
        if args.project and p["name"] != args.project:
            continue
        t0 = time.time()
        r = engine.sync_project(
            p["name"], p["path"], p.get("extra_exclude_dirs"),
            decl=declarations.get(p["name"]),
        )
        print(f"[{r['project']}] 新增={r['new']} 变更={r['changed']} 未变={r['unchanged']} "
              f"删除={r['deleted']} 错误={r['errors']} 块数={r['chunks']} "
              f"降级切块={r['fallback_files']} 耗时={time.time() - t0:.1f}s")

    if vs:
        print(f"向量库总量：{vs.count()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
