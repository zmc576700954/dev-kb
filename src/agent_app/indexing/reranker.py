"""BGE 重排：CrossEncoder 对 (查询, 候选块) 精排。

bge-reranker-v2-m3 与 BGE-M3 同族，中文语义精度好，MPS 加速。
单例懒加载；截断候选内容控制显存（CrossEncoder max_length=512）。
"""

from __future__ import annotations

import math
import os

import torch

RERANK_MODEL = "BAAI/bge-reranker-v2-m3"
# 候选内容截断长度（字符），超出部分对相关性判定贡献递减
MAX_CHARS = 1200

_model = None


def _get_model():
    global _model
    if _model is None:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")  # 模型已缓存，跳过网络检查
        from sentence_transformers import CrossEncoder
        device = "mps" if torch.backends.mps.is_available() else "cpu"
        _model = CrossEncoder(RERANK_MODEL, device=device, max_length=512)
    return _model


def rerank(query: str, candidates: list[dict], top: int, blend: float = 0.5) -> list[dict]:
    """对候选块按 (query, content) 交叉打分，与融合分加权混合后返回前 top 条。

    blend=1.0 纯重排分，blend=0.0 纯融合分；默认 0.5。
    融合分按最大值归一，重排分经 sigmoid 归一到 (0,1)，两者量纲对齐后加权。
    每条结果附加 rerank_score 字段；原融合分保留在 score。
    """
    if not candidates:
        return candidates
    model = _get_model()
    pairs = [(query, c["content"][:MAX_CHARS]) for c in candidates]
    scores = model.predict(pairs, batch_size=16, show_progress_bar=False)
    max_fusion = max(c["score"] for c in candidates) or 1.0
    for c, s in zip(candidates, scores):
        c["rerank_score"] = round(float(s), 4)
        norm_fusion = c["score"] / max_fusion
        norm_rerank = 1.0 / (1.0 + math.exp(-float(s)))
        c["blended"] = (1 - blend) * norm_fusion + blend * norm_rerank
    return sorted(candidates, key=lambda c: -c["blended"])[:top]
