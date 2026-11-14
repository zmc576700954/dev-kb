#!/bin/sh
# 恢复 dev-kb 被破坏的围栏结构 + 定位剩余 error
cd /Users/zhumingchen/dev-kb

echo "=== 1. 恢复预览（不改动文件） ==="
PYTHONPATH=src .venv/bin/python scripts/recover_dev_kb.py

echo
echo "=== 2. 恢复前：围栏结构检查 ==="
PYTHONPATH=src .venv/bin/python - <<'PYEOF'
import re, pathlib
for f in ["README.md", "docs/architecture.md", "docs/decisions.md",
          "docs/knowledge-map.md", "evals/README.md"]:
    lines = pathlib.Path(f).read_text(encoding="utf-8").split("\n")
    fences = [(i+1, ln) for i, ln in enumerate(lines)
              if re.match(r"^ {0,3}(```|~~~)", ln)]
    bad = [x for x in fences if re.match(r"^ {0,3}```+\S", x[1])]
    print(f"{f}: {len(fences)} 个围栏，{len(bad)} 个带标注")
PYEOF

echo
echo "=== 3. 应用恢复 ==="
PYTHONPATH=src .venv/bin/python scripts/recover_dev_kb.py --apply

echo
echo "=== 4. 恢复后：围栏结构检查 ==="
PYTHONPATH=src .venv/bin/python - <<'PYEOF'
import re, pathlib
for f in ["README.md", "docs/architecture.md", "docs/decisions.md",
          "docs/knowledge-map.md", "evals/README.md"]:
    lines = pathlib.Path(f).read_text(encoding="utf-8").split("\n")
    fences = [(i+1, ln) for i, ln in enumerate(lines)
              if re.match(r"^ {0,3}(```|~~~)", ln)]
    print(f"{f}: {len(fences)} 个围栏")
PYEOF

echo
echo "=== 5. 剩余 error 明细 ==="
PYTHONPATH=src .venv/bin/python -m agent_app.kb_spec.cli validate dev-kb --json 2>/dev/null | python3 -c "
import json, sys
r = json.load(sys.stdin)
errs = [f for f in r['findings'] if f['severity'] == 'error']
print(f'共 {len(errs)} 处 error')
for f in errs[:20]:
    print(f\"  {f['rule_id']}  {f['location']}  {f['message'][:70]}\")
" || true

echo
echo "=== 6. 全量测试 ==="
PYTHONPATH=src .venv/bin/python -m pytest -q
