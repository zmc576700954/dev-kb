"""frontmatter 解析与序列化（规范 §3.1/§3.2 严格扁平标量文法）。

文法（全部可机械判定，规范 §3.2）：
- 块由第一行恰为 `---` 开启，由其后第一行恰为 `---` 收束。
- 块内每行恰为一个 `键: 值`；键与值之间一个半角冒号加一个空格。
- 禁止：嵌套结构、列表、多行值（缩进续行）、YAML 锚点与引用、空块、块内空行。
- 禁止重复键（规范 §3.5）。

解析永不抛异常：违规逐条转成 Finding；fm 为 None 表示无有效 frontmatter。
规范 §3.1 的「普通文档必须有 / README 禁止有」属于文档级规则，由 checks_doc 判定。
"""

from __future__ import annotations

import re

from .vocab import Finding, SEVERITY_ERROR

RULE_FENCE = "KB-3.2-fence"            # 块未闭合 / 首行不是 ---
RULE_LINE_FORMAT = "KB-3.2-line-format"  # 某行不是一个合法的「键: 值」
RULE_FLAT = "KB-3.2-flat-scalar"       # 嵌套 / 列表 / 多行值 / 锚点引用
RULE_EMPTY = "KB-3.2-empty-block"      # 空块
RULE_DUP_KEY = "KB-3.5-dup-key"        # 重复键

# 键不含半角/全角冒号、不以空白开头；键值间恰为「: 」
_LINE_RE = re.compile(r"^([^:：\s][^:：]*?): (.*)$")


def parse_frontmatter(text: str) -> tuple[dict | None, list[Finding], int]:
    """解析 frontmatter。

    返回 (键值字典 | None, findings, 正文起始行号 1-based)。
    无块时返回 (None, [], 1)；块存在但非法时 fm 仍尽力收集合法键，返回 findings。
    """
    lines = text.split("\n")
    if not lines or lines[0].rstrip("\r") != "---":
        return None, [], 1

    findings: list[Finding] = []
    fm: dict[str, str] = {}
    close_idx = -1
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r") == "---":
            close_idx = i
            break
    if close_idx == -1:
        findings.append(Finding(
            RULE_FENCE, SEVERITY_ERROR, "frontmatter 块没有收束的 `---` 行",
            "行 1", "在元数据之后补一行 `---` 再开始正文",
        ))
        return None, findings, 2

    body_start = close_idx + 2  # 收束行的下一行即正文（1-based：close_idx+1 行是 ---，其后是正文）
    had_kv = False
    for lineno in range(2, close_idx + 1):  # 1-based；收束行是 close_idx+1
        raw = lines[lineno - 1].rstrip("\r")
        loc = f"行 {lineno}"
        if raw.strip() == "":
            findings.append(Finding(
                RULE_LINE_FORMAT, SEVERITY_ERROR, "frontmatter 块内出现空行", loc,
                "块内每行必须是一个 `键: 值`，删除空行",
            ))
            continue
        if raw != raw.strip():
            findings.append(Finding(
                RULE_LINE_FORMAT, SEVERITY_ERROR,
                f"frontmatter 行首或行尾有空白：`{raw}`", loc,
                "去掉行首尾空白；多行值被禁止",
            ))
        stripped = raw.strip()
        if raw.startswith((" ", "\t")) or stripped.startswith("- ") or stripped.startswith("-\t"):
            findings.append(Finding(
                RULE_FLAT, SEVERITY_ERROR,
                "frontmatter 出现缩进续行或列表项（禁止嵌套结构与列表）", loc,
                "改为单行 `键: 值`；列表写进正文",
            ))
            continue
        if stripped.startswith(("&", "*", "!!", "<<")):
            findings.append(Finding(
                RULE_FLAT, SEVERITY_ERROR,
                f"frontmatter 值疑似 YAML 锚点/引用/标签：`{stripped}`", loc,
                "只允许扁平标量值，去掉锚点语法",
            ))
            continue
        m = _LINE_RE.match(raw)
        if not m:
            findings.append(Finding(
                RULE_LINE_FORMAT, SEVERITY_ERROR,
                f"不是合法的「键: 值」行（键值之间必须恰为一个半角冒号加一个空格）：`{raw}`",
                loc, "改写为 `键: 值`",
            ))
            continue
        key, value = m.group(1), m.group(2)
        if key != key.strip() or not key.strip():
            findings.append(Finding(
                RULE_LINE_FORMAT, SEVERITY_ERROR,
                f"键首尾不得有空白：`{raw}`", loc, "去掉键两端的空白",
            ))
            continue
        if key in fm:
            findings.append(Finding(
                RULE_DUP_KEY, SEVERITY_ERROR,
                f"重复键 `{key}`（规范 §3.5 禁止）", loc, f"合并或删除多余的 `{key}` 行",
            ))
            continue
        fm[key] = value
        had_kv = True

    if not had_kv and not findings:
        findings.append(Finding(
            RULE_EMPTY, SEVERITY_ERROR, "空 frontmatter 块（`---` 之间没有任何键值行）",
            "行 1", "删除空块，或补齐 `状态`/`最后更新`/`下次复核`",
        ))
    return fm, findings, body_start


def serialize_frontmatter(fm: dict[str, str]) -> str:
    """把键值字典序列化为规范格式的 frontmatter 文本（含收束行与换行）。"""
    lines = ["---"]
    for key, value in fm.items():
        lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines) + "\n"
