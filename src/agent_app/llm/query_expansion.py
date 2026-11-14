"""LLM 查询扩展：把中文开发问题改写成英文符号/路径候选，补充跨语言召回。

中文问法与英文代码符号（list_kbs、validate_filename）之间存在词汇鸿沟，
符号/关键词通道挂接不上。用本地 LLM 生成最可能的英文候选词，作为额外召回路。
结果按问题磁盘缓存（data/query_expansion_cache.json），评测可断点预生成。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

CACHE_PATH = Path(__file__).resolve().parents[3] / "data/query_expansion_cache.json"

_SYSTEM = (
    "你是代码检索助手。把用户的中文开发问题改写成最可能命中的英文代码符号、"
    "文件名或目录名候选（驼峰、下划线或路径片段）。只输出 3 个候选，逗号分隔，不要解释。"
)
_TOKEN = re.compile(r"^[A-Za-z_][A-Za-z0-9_/@$.#-]{1,40}$")

_cache: dict | None = None


def _load_cache() -> dict:
    global _cache
    if _cache is None:
        _cache = json.loads(CACHE_PATH.read_text(encoding="utf-8")) if CACHE_PATH.exists() else {}
    return _cache


def _save_cache() -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(_cache, ensure_ascii=False, indent=1), encoding="utf-8")


def expand(question: str, use_llm: bool = True) -> list[str]:
    """返回英文候选词列表。缓存命中直接返回；use_llm=False 时未命中返回空（不调用模型）。"""
    cache = _load_cache()
    if question in cache:
        return cache[question]
    if not use_llm:
        return []
    from . import backend
    raw = backend.chat(_SYSTEM, question, max_tokens=60, temperature=0.1)
    terms = [t.strip() for t in re.split(r"[，,、;；\s]+", raw) if _TOKEN.match(t.strip())][:3]
    cache[question] = terms
    _save_cache()
    return terms
