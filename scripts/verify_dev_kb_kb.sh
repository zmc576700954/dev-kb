#!/bin/sh
# 接入 dev-kb 自身：范围限定修正后的完整验证
cd /Users/zhumingchen/dev-kb

echo "=== 1. kb_paths 范围测试 ==="
PYTHONPATH=src .venv/bin/python -m pytest tests/test_kb_scope.py -q

echo
echo "=== 2. dev-kb 校验（规范化后） ==="
PYTHONPATH=src .venv/bin/python -m agent_app.kb_spec.cli validate dev-kb --progress || true

echo
echo "=== 3. 剩余 error 明细 ==="
PYTHONPATH=src .venv/bin/python -m agent_app.kb_spec.cli validate dev-kb --json 2>/dev/null | python3 -c "
import json, sys
r = json.load(sys.stdin)
errs = [f for f in r['findings'] if f['severity'] == 'error']
print(f'共 {len(errs)} 处 error')
for f in errs[:12]:
    print(f\"  {f['rule_id']}  {f['location']}  {f['message'][:70]}\")
" || true

echo
echo "=== 4. 全量测试 ==="
PYTHONPATH=src .venv/bin/python -m pytest -q
