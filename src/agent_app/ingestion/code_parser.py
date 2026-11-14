"""代码符号切块：tree-sitter 解析 php / ts / tsx / python / rust，按符号切块。

- 优先提取函数、方法、类、接口、trait、impl 等符号，符号限定名带类前缀（如 UserController@store）。
- 模块级代码（imports、常量、脚本语句）单独成块，symbol="__module__"。
- 解析失败或一个符号都没有时，降级为 80 行行窗口切块并标记 fallback。
"""

from __future__ import annotations

import tree_sitter_php
import tree_sitter_python
import tree_sitter_rust
import tree_sitter_typescript
from tree_sitter import Language, Parser

from .document_parser import Chunk

MAX_SYMBOL_LINES = 220   # 超大符号按行窗口再切
FALLBACK_WINDOW = 80
FALLBACK_OVERLAP = 10

_LANGUAGES: dict[str, Language] = {
    "php": Language(tree_sitter_php.language_php()),
    "python": Language(tree_sitter_python.language()),
    "typescript": Language(tree_sitter_typescript.language_typescript()),
    "tsx": Language(tree_sitter_typescript.language_tsx()),
    "rust": Language(tree_sitter_rust.language()),
}

_CONTAINER_TYPES = {
    "php": {"class_declaration", "interface_declaration", "trait_declaration", "enum_declaration", "namespace_definition"},
    "python": {"class_definition"},
    "typescript": {"class_declaration", "interface_declaration", "enum_declaration", "module"},
    "tsx": {"class_declaration", "interface_declaration", "enum_declaration", "module"},
    "rust": {"impl_item", "trait_item", "mod_item"},
}
_LEAF_TYPES = {
    "php": {"function_definition", "method_declaration"},
    "python": {"function_definition", "class_definition"},
    "typescript": {"function_declaration", "method_definition", "method_signature", "class_declaration",
                   "interface_declaration", "type_alias_declaration", "enum_declaration", "generator_function_declaration"},
    "tsx": {"function_declaration", "method_definition", "method_signature", "class_declaration",
            "interface_declaration", "type_alias_declaration", "enum_declaration", "generator_function_declaration"},
    "rust": {"function_item", "struct_item", "enum_item", "trait_item", "mod_item", "impl_item"},
}


_CONTAINERS_ALL = _CONTAINER_TYPES


def _parser_for(language: str) -> Parser | None:
    lang = _LANGUAGES.get(language)
    return Parser(lang) if lang else None


def _node_name(node, source: bytes) -> str:
    name_node = node.child_by_field_name("name")
    if name_node:
        return source[name_node.start_byte:name_node.end_byte].decode("utf-8", "replace")
    return ""


def _const_decl_name(node, source: bytes) -> str:
    """TS/JS: const foo = ... —— 箭头函数、函数表达式、对象/数组字面量、调用表达式都视为命名定义。"""
    if node.type != "lexical_declaration":
        return ""
    for child in node.children:
        if child.type == "variable_declarator":
            val = child.child_by_field_name("value")
            if val is not None and val.type in (
                "arrow_function", "function_expression", "object", "array", "call_expression",
            ):
                return _node_name(child, source)
    return ""


def _pair_function_name(node, source: bytes) -> str:
    """TS/JS 对象字面量方法：{ updateNode: (id) => {...} } —— zustand store 等模式。"""
    if node.type != "pair":
        return ""
    val = node.child_by_field_name("value")
    if val is not None and val.type in ("arrow_function", "function_expression"):
        key = node.child_by_field_name("key")
        if key is not None:
            return source[key.start_byte:key.end_byte].decode("utf-8", "replace").strip("\"'")
    return ""


# 只下行不整体成块的容器（包/命名空间几乎包住整个文件，整体成块会与方法块大面积重复）
_TRANSPARENT_CONTAINERS = {
    "php": {"namespace_definition"},
    "typescript": {"module"},
    "tsx": {"module"},
}


def _collect_symbols(node, source: bytes, language: str, prefix: str, out: list):
    containers = _CONTAINERS_ALL[language]
    leaves = _LEAF_TYPES[language]
    transparent = _TRANSPARENT_CONTAINERS.get(language, set())

    for child in node.children:
        ctype = child.type
        if language == "python" and ctype == "decorated_definition":
            for sub in child.children:
                if sub.type in leaves or sub.type in containers:
                    child = sub
                    ctype = sub.type
                    break
        if ctype in transparent:
            name = _node_name(child, source)
            qual = f"{prefix}::{name}" if prefix and name else (name or prefix)
            _collect_symbols(child, source, language, qual, out)
        elif ctype in containers:
            name = _node_name(child, source)
            qual = f"{prefix}::{name}" if prefix and name else (name or prefix)
            _emit(out, child, source, qual or "__container__", language)
            _collect_symbols(child, source, language, qual, out)
        elif ctype in leaves:
            name = _node_name(child, source)
            sep = "@" if language == "php" and prefix else ("." if prefix else "")
            qual = f"{prefix}{sep}{name}" if prefix and name else (name or "__anonymous__")
            _emit(out, child, source, qual, language)
        elif (name := _const_decl_name(child, source)):
            qual = f"{prefix}.{name}" if prefix else name
            _emit(out, child, source, qual, language)
            # 继续下行：对象字面量内的方法（zustand store 等）仍需捕获
            _collect_symbols(child, source, language, prefix, out)
        elif (name := _pair_function_name(child, source)):
            qual = f"{prefix}.{name}" if prefix else name
            _emit(out, child, source, qual, language)
        else:
            # 穿透 block / declaration_list / export_statement 等中间节点继续找符号
            _collect_symbols(child, source, language, prefix, out)


def _emit(out: list, node, source: bytes, symbol: str, language: str):
    text = source[node.start_byte:node.end_byte].decode("utf-8", "replace")
    start_line = node.start_point[0] + 1
    end_line = node.end_point[0] + 1
    if end_line - start_line + 1 <= MAX_SYMBOL_LINES:
        out.append(Chunk(text, "code", language=language, symbol=symbol,
                         start_line=start_line, end_line=end_line))
        return
    # 超大符号按行窗口切分
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        j = min(i + FALLBACK_WINDOW, len(lines))
        out.append(Chunk("\n".join(lines[i:j]), "code", language=language,
                         symbol=f"{symbol}#part{i // FALLBACK_WINDOW + 1}",
                         start_line=start_line + i, end_line=start_line + j - 1))
        i = j - FALLBACK_OVERLAP if j < len(lines) else j


def chunk_code(text: str, language: str) -> tuple[list[Chunk], bool]:
    """返回 (chunks, used_fallback)。"""
    parser = _parser_for(language)
    if parser is None:
        return _fallback(text, language), True
    try:
        tree = parser.parse(text.encode("utf-8"))
    except Exception:
        return _fallback(text, language), True

    source = text.encode("utf-8")
    chunks: list[Chunk] = []
    _collect_symbols(tree.root_node, source, language, "", chunks)

    if not chunks:
        return _fallback(text, language), True
    return chunks, False


def _fallback(text: str, language: str) -> list[Chunk]:
    lines = text.splitlines()
    out: list[Chunk] = []
    i = 0
    while i < len(lines):
        j = min(i + FALLBACK_WINDOW, len(lines))
        out.append(Chunk("\n".join(lines[i:j]), "code", language=language,
                         symbol="__fallback__", start_line=i + 1, end_line=j))
        i = j - FALLBACK_OVERLAP if j < len(lines) else j
    return out
