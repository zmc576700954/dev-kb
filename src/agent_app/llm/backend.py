"""LLM 后端：OpenAI 兼容接口优先，缺省回退本地 MLX 模型（kaiwu 的 Qwen3-4B）。

配置方式（按优先级）：
1. 环境变量 LLM_BASE_URL + LLM_API_KEY + LLM_MODEL（任何 OpenAI 兼容服务）
2. 本地 MLX：LLM_MLX_PATH 或默认 kaiwu/models/Qwen3-4B-Instruct-2507-mlx-4bit
"""

from __future__ import annotations

import os

DEFAULT_MLX_PATH = "/Users/zhumingchen/kaiwu/models/Qwen3-4B-Instruct-2507-mlx-4bit"

_mlx_cache: tuple | None = None


def backend_name() -> str:
    if os.environ.get("LLM_BASE_URL") and os.environ.get("LLM_API_KEY"):
        return f"openai-compatible:{os.environ.get('LLM_MODEL', '?')}"
    path = os.environ.get("LLM_MLX_PATH", DEFAULT_MLX_PATH)
    return f"mlx:{os.path.basename(path)}" if os.path.isdir(path) else "none"


def available() -> bool:
    return backend_name() != "none"


def chat(system: str, user: str, max_tokens: int = 900, temperature: float = 0.2) -> str:
    if os.environ.get("LLM_BASE_URL") and os.environ.get("LLM_API_KEY"):
        return _chat_openai(system, user, max_tokens, temperature)
    return _chat_mlx(system, user, max_tokens, temperature)


def _chat_openai(system: str, user: str, max_tokens: int, temperature: float) -> str:
    import json
    import urllib.request

    base = os.environ["LLM_BASE_URL"].rstrip("/")
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps({
            "model": os.environ.get("LLM_MODEL", ""),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.environ['LLM_API_KEY']}",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"]


def _chat_mlx(system: str, user: str, max_tokens: int, temperature: float) -> str:
    global _mlx_cache
    from mlx_lm import generate, load
    if _mlx_cache is None:
        path = os.environ.get("LLM_MLX_PATH", DEFAULT_MLX_PATH)
        _mlx_cache = load(path)
    model, tokenizer = _mlx_cache
    prompt = tokenizer.apply_chat_template(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        tokenize=False, add_generation_prompt=True,
    )
    return generate(model, tokenizer, prompt=prompt, max_tokens=max_tokens, verbose=False)
