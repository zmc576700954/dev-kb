"""围栏结构修复：保证 opening 带语言标注、closing 为纯围栏。

背景：早期版本的批量标注脚本把「纯围栏 closing」误当成新的 opening，
导致文档出现「```lang 开、```text 闭」的错误配对。本脚本按出现顺序
把围栏两两配对，opening 补语言、closing 去标注，并报告落单的围栏。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

FENCE_RE = re.compile(r"^ {0,3}(```+|~~~+)(.*)$")


def guess_language(block: str) -> str:
    b = block.strip()
    if not b:
        return "text"
    if re.search(r"^\s*(pip |python -m |python3 |npm |npx |yarn |cd |mkdir |export |git |docker |curl |wget |chmod |apt |brew )",
                 b, re.MULTILINE):
        return "bash"
    if re.search(r"^\s*(import |from \w+ import |def |class |print\(|if __name__)", b, re.MULTILINE):
        return "python"
    if b.startswith("{") or b.startswith("["):
        return "json"
    if re.search(r"^\s*[\w-]+:\s", b, re.MULTILINE) and "=" not in b.split(":")[0]:
        return "yaml"
    if re.search(r"^\s*(<\?php|<\w+>)", b):
        return "html"
    return "text"


def fix_fences(content: str) -> tuple[str, list[str]]:
    """修复围栏配对，返回 (修复后内容, 问题说明列表)。"""
    lines = content.split("\n")
    fences: list[int] = []
    for i, ln in enumerate(lines):
        if FENCE_RE.match(ln):
            fences.append(i)

    issues: list[str] = []
    if len(fences) % 2 == 1:
        issues.append(f"围栏数为奇数（{len(fences)}），最后一个围栏无法配对：行 {fences[-1] + 1}")

    out = list(lines)
    for k in range(0, len(fences) - 1, 2):
        oi, ci = fences[k], fences[k + 1]
        om = FENCE_RE.match(out[oi])
        cm = FENCE_RE.match(out[ci])
        if not om or not cm:
            continue
        o_marker, o_info = om.group(1), om.group(2).strip()
        c_marker, c_info = cm.group(1), cm.group(2).strip()
        # 标记不同（``` 与 ~~~ 混用）→ closing 无效
        if o_marker[0] != c_marker[0]:
            issues.append(f"行 {ci + 1}：closing 标记 `{c_marker}` 与 opening `{o_marker}` 不匹配")
            continue
        # opening 缺语言 → 补
        if not o_info:
            block = "\n".join(out[oi + 1:ci])
            out[oi] = f"{o_marker}{guess_language(block)}"
        # closing 带语言 → 去标注
        if c_info:
            out[ci] = c_marker
            issues.append(f"行 {ci + 1}：closing 围栏不应带语言标注 `{c_info}`，已去除")
    return "\n".join(out), issues


def main() -> int:
    if len(sys.argv) < 2:
        print("用法：python scripts/fix_fences.py <项目根目录>")
        return 2
    root = Path(sys.argv[1])
    total_fixed = 0
    for md in sorted(root.rglob("*.md")):
        content = md.read_text(encoding="utf-8")
        fixed, issues = fix_fences(content)
        if fixed != content:
            md.write_text(fixed, encoding="utf-8")
            total_fixed += 1
            print(f"{md.relative_to(root)}：修复 {len(issues)} 处")
            for i in issues[:5]:
                print(f"    {i}")
    print(f"\n共修复 {total_fixed} 个文件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
