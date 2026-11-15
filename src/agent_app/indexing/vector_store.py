"""Qdrant 向量库（本地文件模式，免 Docker）。

- Collection 名携带 Embedding 模型标识；换模型 = 新 Collection，不混向量。
- point id 直接使用 SQLite chunk id；payload 只存标识与定位信息（project/path/symbol/
  行号/source_type），检索后按 chunk id 回查 SQLite 取契约字段——契约字段的唯一
  事实源是 SQLite，不在向量库里复制一份。
"""

from __future__ import annotations

from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from . import embedder

COLLECTION = f"chunks_{embedder.MODEL_NAME.split('/')[-1].replace('.', '_').replace('-', '_')}"


class VectorStore:
    """优先连接 Docker 服务模式（localhost:6333），连不上回退本地文件模式。"""

    def __init__(self, path: str | Path, url: str = "http://localhost:6333"):
        Path(path).mkdir(parents=True, exist_ok=True)
        try:
            import urllib.request
            urllib.request.urlopen(f"{url}/healthz", timeout=2)
            self.client = QdrantClient(url=url)
            self.mode = f"server({url})"
        except Exception:
            self.client = QdrantClient(path=str(path))
            self.mode = f"local({path})"
        existing = {c.name for c in self.client.get_collections().collections}
        if COLLECTION not in existing:
            self.client.create_collection(
                collection_name=COLLECTION,
                vectors_config=VectorParams(size=embedder.VECTOR_SIZE, distance=Distance.COSINE),
            )

    def upsert(self, chunk_ids: list[int], vectors: list[list[float]], payloads: list[dict]):
        points = [
            PointStruct(id=cid, vector=vec, payload=pl)
            for cid, vec, pl in zip(chunk_ids, vectors, payloads)
        ]
        self.client.upsert(collection_name=COLLECTION, points=points)

    def delete(self, chunk_ids: list[int]):
        if chunk_ids:
            self.client.delete(collection_name=COLLECTION, points_selector=chunk_ids)

    def search(self, query_vector: list[float], project: str | None = None, limit: int = 10):
        flt = None
        if project:
            from qdrant_client.models import FieldCondition, Filter, MatchValue
            flt = Filter(must=[FieldCondition(key="project", match=MatchValue(value=project))])
        return self.client.query_points(
            collection_name=COLLECTION, query=query_vector, query_filter=flt, limit=limit
        ).points

    def count(self) -> int:
        return self.client.count(collection_name=COLLECTION).count
