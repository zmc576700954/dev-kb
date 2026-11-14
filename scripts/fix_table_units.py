"""表格数值列单位补全：基于表内实际数据推断单位。

原则：只在数据中明确出现单位时才补（如「500-800 元」→ 单位「元」）；
数据是纯数字且无单位线索时不猜——猜错比不写更糟。

用法：
  PYTHONPATH=src .venv/bin/python scripts/fix_table_units.py ai-video-kb --dry-run
  PYTHONPATH=src .venv/bin/python scripts/fix_table_units.py ai-video-kb --apply
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent_app.kb_spec.docmodel import parse_document
from agent_app.kb_spec.service import KBSpecService

# 数据中的单位线索 → 单位名（按出现顺序）
UNIT_PATTERNS = [
    (r"元/|美元/|USD/|/秒|/千次", None),          # 复合单位，单独处理
    (r"\d[\d.\-~]*\s*万元", "万元"),
    (r"\d[\d.\-~]*\s*元", "元"),
    (r"\d[\d.\-~]*\s*美元", "元"),
    (r"\d[\d.\-~]*\s*\$", "美元"),
    (r"\d[\d.\-~]*\s*(?:分钟|min\b)", "分钟"),
    (r"\d[\d.\-~]*\s*(?:小时|h\b)", "小时"),
    (r"\d[\d.\-~]*\s*(?:秒|s\b)(?!\w)", "秒"),
    (r"\d[\d.\-~]*\s*GB\b", "GB"),
    (r"\d[\d.\-~]*\s*MB\b", "MB"),
    (r"\d[\d.\-~]*\s*TB\b", "TB"),
    (r"\d[\d.\-~]*\s*fps\b", "fps"),
    (r"\d[\d.\-~]*\s*帧", "帧"),
]

# 定性值（含这些词说明该列不是数值列，不补单位）
QUALITATIVE = re.compile(
    r"(低|高|中|免费|付费|订阅|一次性|按需|视情况|慢|快|一般|较高|较低|免费\+付费)")

# 日期形态（不是时长/数值）
DATE_LIKE = re.compile(r"\d{4}[-/]\d{1,2}(?:[-/]\d{1,2})?")

# 纯数字（无单位线索）
BARE_NUMBER = re.compile(r"^\d[\d.\-~]*$")


def extract_unit(cell: str) -> str | None:
    """从单个单元格提取单位；无单位返回 None。"""
    c = cell.strip().strip("*`")
    if not c or DATE_LIKE.search(c):
        return None
    # 复合单位优先（元/秒、元/千次、元/年）——规范 §9 的示例即此类
    m = re.search(r"\d[\d.\-~]*\s*([一-鿿A-Za-z]+\s*/\s*[一-鿿A-Za-z]+)", c)
    if m:
        return m.group(1).replace(" ", "")
    for pattern, unit in UNIT_PATTERNS:
        if unit and re.search(pattern, c, re.IGNORECASE):
            return unit
    return None


def guess_unit(col: str, cells: list[str]) -> str | None:
    """推断单位：所有非空单元格单位一致才补；否则不猜。"""
    if "（" in col or "(" in col:
        return None          # 列名已标注
    meaningful = [c for c in cells if c.strip() and c.strip() not in ("-", "—", "无", "N/A")]
    if not meaningful:
        return None
    if any(QUALITATIVE.search(c) for c in meaningful):
        return None          # 定性列
    units = []
    bare = 0
    for c in meaningful:
        u = extract_unit(c)
        if u:
            units.append(u)
        elif BARE_NUMBER.match(c.strip().strip("*`")):
            bare += 1
    if not units:
        return None          # 全无单位线索 → 不猜
    if len(set(units)) > 1:
        return None          # 单位不一致 → 不猜
    if bare:
        # 部分单元格裸数字：仅当有单位线索的单元格占多数时采用，且列名需含数值语义
        if bare > len(units):
            return None
    return units[0]


def collect_tables(content: str, rel_path: str):
    """找出所有「列名疑似数值且数据未标单位」的表格。"""
    doc = parse_document(rel_path, content)
    lines = content.split("\n")
    out = []
    for tb in doc.tables:
        if not tb.rows:
            continue
        for idx, col in enumerate(tb.header):
            if not re.search(r"(成本|价格|时间|大小|内存|长度|耗时|费用|单价|数量|规模)", col):
                continue
            cells = [r[1][idx] for r in tb.rows if idx < len(r[1])]
            if any("（" in c and re.search(r"[（(]\s*[A-Za-z一-鿿/%·\s]+\s*[)）]", c) for c in cells):
                continue   # 数据里已带单位
            unit = guess_unit(col, cells)
            if unit:
                out.append((tb.start_line, idx, col, unit, cells[:3]))
    return out


def apply_units(content: str, tables) -> str:
    """在表头列名后加「（单位）」。"""
    lines = content.split("\n")
    # 按表头行分组（同一表头行可能有多个待改列）
    by_header: dict[int, list[tuple[int, str]]] = {}
    for start_line, idx, col, unit, _cells in tables:
        by_header.setdefault(start_line, []).append((idx, unit))
    for header_line, cols in sorted(by_header.items(), reverse=True):
        row = lines[header_line - 1]
        if not row.strip().startswith("|"):
            continue
        cells = row.strip().strip("|").split("|")
        for idx, unit in cols:
            if idx < len(cells) and "（" not in cells[idx]:
                cells[idx] = f"{cells[idx].strip()}（{unit}）"
        lines[header_line - 1] = "| " + " | ".join(c.strip() for c in cells) + " |"
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="表格单位补全")
    parser.add_argument("project")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    svc = KBSpecService()
    _, snap = svc.build_snapshot(args.project)

    total = 0
    plans = []
    for path, content in sorted(snap.files.items()):
        if path == "README.md":
            continue
        tables = collect_tables(content, path)
        if not tables:
            continue
        total += len(tables)
        plans.append((path, tables))
        print(f"{path}（{len(tables)} 列）")
        for start_line, idx, col, unit, cells in tables:
            print(f"    行{start_line} 列「{col}」→ 「{col}（{unit}）」  样例数据：{cells}")

    print(f"\n合计 {total} 处")
    if args.apply:
        ok = 0
        for path, tables in plans:
            new = apply_units(snap.files[path], tables)
            if new == snap.files[path]:
                continue
            r = svc.propose_update(args.project, path, new, snap.sha_of(path))
            if r["status"] != "proposal_created":
                print(f"  {path}: {r['status']} {r.get('message', '')[:50]}")
                continue
            pid = r["proposal_id"]
            svc.proposals.decide(pid, "approved")
            res = svc.apply_proposal(pid)
            if res["status"] == "applied":
                ok += 1
            else:
                print(f"  {path}: {res['status']}")
        print(f"应用成功 {ok}/{len(plans)} 篇")
        v = svc.kb_validate_project(args.project)
        print(f"最终：error={v['summary']['errors']} warning={v['summary']['warnings']} "
              f"review={v['summary']['reviews']}")
    else:
        print("\n（dry-run：未生成提案）")


if __name__ == "__main__":
    main()
