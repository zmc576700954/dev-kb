"""本地 Embedding：BGE-M3（sentence-transformers，MPS 加速，1024 维）。

- 中文语义精度显著优于 bge-small-zh；稠密向量归一化后走余弦相似度。
- 模型标识写入 Qdrant Collection 名；更换模型 = 新建 Collection 全量重建，不混向量。
- 首次使用自动从 HF_ENDPOINT（默认 hf-mirror）下载约 2.3GB 模型。
"""

from __future__ import annotations

MODEL_NAME = "BAAI/bge-m3"
VECTOR_SIZE = 1024

_model = None


def get_model():
    global _model
    if _model is None:
        import torch
        from sentence_transformers import SentenceTransformer
        device = "mps" if torch.backends.mps.is_available() else "cpu"
        _model = SentenceTransformer(MODEL_NAME, device=device)
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """按文本长度自适应分批：长文本注意力显存随长度平方增长，大批次会 MPS OOM。

    预算制：每批 总字符数 ≤ 48k 且 ≤ 64 条；超长块单条成批。
    """
    model = get_model()
    out: list[list[float] | None] = [None] * len(texts)
    order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
    batch: list[int] = []
    budget = 0
    for i in order:
        n = len(texts[i])
        if batch and (budget + n > 48_000 or len(batch) >= 64):
            _encode_into(model, texts, batch, out)
            batch, budget = [], 0
        batch.append(i)
        budget += n
    if batch:
        _encode_into(model, texts, batch, out)
    return out  # type: ignore[return-value]


def _encode_into(model, texts, idxs, out):
    vecs = model.encode([texts[i] for i in idxs], normalize_embeddings=True,
                        batch_size=len(idxs), show_progress_bar=False)
    for i, v in zip(idxs, vecs.tolist()):
        out[i] = v
