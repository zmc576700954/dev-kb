"""打印指定提案的完整内容（供人工审查）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_app.kb_spec.service import KBSpecService

pid = int(sys.argv[1])
svc = KBSpecService()
p = svc.show_proposal(pid)["proposal"]
print(f"提案 #{p['id']} [{p['status']}] {p['kind']} {p['target']}")
print(f"标题：{p.get('title')}")
print(f"findings：{p.get('findings')}")
for op in p["ops"]:
    after = op.get("after") or ""
    print("=" * 66)
    print(f"op={op['op']} path={op['path']} 行数={len(after.splitlines())}")
    print("=" * 66)
    print(after)
