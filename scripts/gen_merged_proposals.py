"""合并提案生成器：每个文档一个提案，包含全部格式修复。

避免同一文档多个提案之间的 before_sha 依赖冲突（stale）。

用法：
  PYTHONPATH=src .venv/bin/python scripts/gen_merged_proposals.py ai-video-kb
  PYTHONPATH=src .venv/bin/python scripts/gen_merged_proposals.py ai-video-kb --apply-all
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent_app.kb_spec.docmodel import parse_document
from agent_app.kb_spec.service import KBSpecService

LANG_TAGS = ("```bash", "```text", "```python", "```json", "```yaml", "```js", "```html")


def guess_language(block: str) -> str:
    """按内容推断代码块语言。"""
    b = block.strip()
    if not b:
        return "text"
    if re.search(r"^\s*(pip |python -m |python3 |npm |npx |yarn |cd |mkdir |export |git |docker |curl |wget |chmod )",
                 b, re.MULTILINE):
        return "bash"
    if re.search(r"^\s*(import |from \w+ import |def |class |print\(|if __name__)", b, re.MULTILINE):
        return "python"
    if b.startswith("{") or b.startswith("["):
        return "json"
    if re.search(r"^\s*[\w-]+:\s", b, re.MULTILINE) and ":" in b and "=" not in b.split(":")[0]:
        return "yaml"
    return "text"


def tag_code_blocks(content: str) -> str:
    """为无语言标注的围栏代码块补语言标识。

    用状态机逐行扫描（不依赖围栏配对）——文档中若存在不成对的围栏，
    配对式实现会错位并漏标。带语言标注的 opening fence 原样保留。
    """
    lines = content.split("\n")
    out: list[str] = []
    in_block = False
    needs_tag = False
    block: list[str] = []
    for ln in lines:
        if not in_block:
            # opening fence：纯围栏（需标注）或带语言标注（原样保留）
            m = re.match(r"^ {0,3}(```+)(.*)$", ln)
            if m:
                in_block = True
                needs_tag = m.group(2).strip() == ""
                block = []
                out.append(ln)
                continue
            out.append(ln)
            continue
        # 块内
        if re.match(r"^ {0,3}```+\s*$", ln):
            if needs_tag:
                out[-1] = f"```{guess_language(chr(10).join(block))}"
            out.append(ln)
            in_block = False
            block = []
            continue
        block.append(ln)
        out.append(ln)
    return "\n".join(out)


def add_related_section(content: str, path: str, snap) -> str:
    """补「相关文档」段：链接同分类的真实文档。"""
    if "## 相关文档" in content:
        return content
    cat = path.split("/")[0]
    siblings = [p for p in snap.files
                if p.startswith(cat + "/") and p != path and p != "README.md"]
    if not siblings:
        return content
    lines = ["\n## 相关文档", ""]
    for sib in siblings[:3]:
        sib_doc = parse_document(sib, snap.files[sib])
        title = sib_doc.title or sib.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        lines.append(f"- [{title}](./{sib.rsplit('/', 1)[-1]})")
    return content.rstrip("\n") + "\n" + "\n".join(lines) + "\n"


def merge_fixes(content: str, path: str, snap) -> str:
    """把所有格式修复合并进内容。"""
    out = content
    if not out.startswith("---"):
        from datetime import date
        today = date.today().isoformat()
        out = (f"---\n状态: 草案\n最后更新: {today}\n下次复核: 按需\n---\n\n" + out)
    out = tag_code_blocks(out)
    out = add_related_section(out, path, snap)
    return out


def describe(new_content: str) -> str:
    parts = []
    if "状态: 草案" in new_content:
        parts.append("补 frontmatter")
    if any(t in new_content for t in LANG_TAGS):
        parts.append("代码块语言标注")
    if "## 相关文档" in new_content:
        parts.append("补相关文档段")
    return " + ".join(parts)


def main():
    parser = argparse.ArgumentParser(description="生成合并格式修复提案")
    parser.add_argument("project")
    parser.add_argument("--apply-all", action="store_true", help="生成后立即批准并应用")
    args = parser.parse_args()

    svc = KBSpecService()
    _, snap = svc.build_snapshot(args.project)

    created = []
    skipped = []
    for path, content in sorted(snap.files.items()):
        if path == "README.md":
            continue
        new_content = merge_fixes(content, path, snap)
        if new_content == content:
            skipped.append(path)
            continue
        result = svc.propose_update(args.project, path, new_content, snap.sha_of(path))
        if result["status"] == "proposal_created":
            created.append((result["proposal_id"], path, describe(new_content)))
        else:
            skipped.append(f"{path}（{result.get('message', '')[:40]}）")

    print(f"项目 {args.project}：生成 {len(created)} 个合并提案，{len(skipped)} 个无需修复")
    for pid, path, desc in created:
        print(f"  #{pid} {path}: {desc}")

    if args.apply_all:
        print("\n--- 应用 ---")
        ok = 0
        for pid, path, desc in created:
            svc.proposals.decide(pid, "approved")
            r = svc.apply_proposal(pid)
            if r["status"] == "applied":
                ok += 1
                print(f"  #{pid} {path}: 已应用")
            else:
                print(f"  #{pid} {path}: {r['status']} {r.get('message', '')[:50]}")
        print(f"\n应用成功：{ok}/{len(created)}")
        v = svc.kb_validate_project(args.project)
        print(f"最终：error={v['summary']['errors']} warning={v['summary']['warnings']}")


if __name__ == "__main__":
    main()
