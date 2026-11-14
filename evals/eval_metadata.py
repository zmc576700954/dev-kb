"""评测运行元数据：无需 Git 即可复现配置、实现、索引与题集版本。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = (
    "evals/run_eval.py",
    "src/agent_app/tools/retrieval.py",
    "src/agent_app/indexing/metadata_store.py",
    "src/agent_app/indexing/embedder.py",
    "src/agent_app/indexing/reranker.py",
    "src/agent_app/indexing/vector_store.py",
    "src/agent_app/llm/query_expansion.py",
    "src/agent_app/llm/backend.py",
)


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def cases_metadata(path: str | Path, cases: list[dict]) -> dict:
    case_path = Path(path)
    try:
        display_path = str(case_path.relative_to(ROOT))
    except ValueError:
        display_path = str(case_path)
    ids = [str(case["id"]) for case in cases]
    return {
        "path": display_path,
        "sha256": file_sha256(case_path),
        "count": len(cases),
        "ids_sha256": hashlib.sha256(
            json.dumps(ids, ensure_ascii=False).encode("utf-8")
        ).hexdigest(),
    }


def implementation_metadata() -> dict:
    """对检索实现做确定性指纹；indexed_at、mtime 与 Git 状态均不参与。"""
    digest = hashlib.sha256()
    for relative in SOURCE_FILES:
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update((ROOT / relative).read_bytes())
        digest.update(b"\0")
    return {"algorithm": "sha256", "sources_sha256": digest.hexdigest()}


def index_metadata(store) -> dict:
    """以项目/路径/内容哈希描述索引快照，避免把同步时间当作内容版本。"""
    rows = [
        [row["project"], row["path"], row["content_hash"] or "", row["status"] or ""]
        for row in store.conn.execute(
            "SELECT project, path, content_hash, status FROM files ORDER BY project, path"
        )
    ]
    signature = hashlib.sha256(
        json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    stats = store.stats()
    return {
        "algorithm": "files-content-sha256-v1",
        "sha256": signature,
        "files": sum(item.get("files", 0) for item in stats.values()),
        "chunks": sum(item.get("chunks", 0) for item in stats.values()),
        "pending_embedding": sum(item.get("pending_embedding", 0) for item in stats.values()),
    }


def retrieval_metadata(args, engine) -> tuple[dict, dict]:
    from agent_app.indexing import embedder, reranker
    from agent_app.llm import backend
    from agent_app.llm.query_expansion import CACHE_PATH
    from agent_app.tools.retrieval import RRF_K

    expand = bool(args.expand)
    rerank = bool(args.rerank)
    config = {
        "rerank": rerank,
        "blend": float(args.blend) if rerank else None,
        "expand": expand,
        "expand_use_llm": bool(engine.expand_use_llm),
        "code_boost": float(engine.code_boost),
        "doc_damp": float(engine.doc_damp),
        "rrf_k": RRF_K,
        "result_limit": 5,
        "query_expansion_cache_sha256": (
            file_sha256(CACHE_PATH) if expand and CACHE_PATH.exists() else None
        ),
    }
    models = {
        "embedding": embedder.MODEL_NAME,
        "reranker": reranker.RERANK_MODEL if rerank else None,
        "query_expansion": backend.backend_name() if expand else None,
    }
    return config, models
