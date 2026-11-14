"""FTS5 中文分词：CJK 字符转二元 bigram，拉丁/数字按词保留。

FTS5 unicode61 分词器不切中文，整段中文会变成一个 token，导致中文查询失效。
统一在写入和查询两侧做同样的预处理，保证索引与查询 token 一致。
"""

from __future__ import annotations

import re

_CJK = re.compile(r"[一-鿿㐀-䶿]")
_WORD = re.compile(r"[A-Za-z0-9_@$.#\-]+")


def fts_tokenize(text: str) -> str:
    """把任意文本转为空格分隔的检索 token 流。"""
    tokens: list[str] = []
    buf: list[str] = []  # 连续 CJK 缓冲

    def flush_cjk():
        if not buf:
            return
        s = "".join(buf)
        buf.clear()
        if len(s) == 1:
            tokens.append(s)
        else:
            tokens.extend(s[i:i + 2] for i in range(len(s) - 1))

    for piece in re.split(r"\s+", text):
        if not piece:
            continue
        i = 0
        while i < len(piece):
            ch = piece[i]
            if _CJK.match(ch):
                buf.append(ch)
                i += 1
            else:
                flush_cjk()
                m = _WORD.match(piece, i)
                if m:
                    tokens.append(m.group(0).lower())
                    i = m.end()
                else:
                    i += 1
        flush_cjk()
    return " ".join(tokens)


def fts_query(text: str) -> str:
    """把用户查询转成 FTS5 MATCH 表达式（token 间 AND，全部加引号转义）。"""
    tokens = fts_tokenize(text).split()
    return " ".join(f'"{t}"' for t in tokens) if tokens else '""'


def fts_query_or(text: str, max_terms: int = 6) -> str:
    """AND 无结果时的 OR 降级：取前若干个 token 做宽松匹配。"""
    tokens = fts_tokenize(text).split()[:max_terms]
    return " OR ".join(f'"{t}"' for t in tokens) if tokens else '""'
