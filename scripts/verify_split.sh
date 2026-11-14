#!/bin/sh
# dev-kb 收尾：拆分 decisions.md + 全量验证
cd /Users/zhumingchen/dev-kb

echo "=== 1. 应用拆分 ==="
PYTHONPATH=src .venv/bin/python scripts/split_decisions.py --apply

echo
echo "=== 2. 校验结果 ==="
PYTHONPATH=src .venv/bin/python -m agent_app.kb_spec.cli validate dev-kb --progress || true

echo
echo "=== 3. 剩余 error 明细 ==="
PYTHONPATH=src .venv/bin/python -m agent_app.kb_spec.cli validate dev-kb --json 2>/dev/null | python3 -c "
import json, sys
r = json.load(sys.stdin)
errs = [f for f in r['findings'] if f['severity'] == 'error']
print(f'共 {len(errs)} 处 error')
for f in errs[:15]:
    print(f\"  {f['rule_id']}  {f['location']}  {f['message'][:70]}\")
" || true

echo
echo "=== 4. 文件确认 ==="
ls -1 docs/*.md 2>/dev/null
wc -l docs/0*.md docs/decisions.md 2>/dev/null

echo
echo "=== 5. 重建索引 ==="
HF_HUB_OFFLINE=1 PYTHONPATH=src .venv/bin/python -m agent_app.sync_cli --project dev-kb 2>&1 | tail -2
HF_HUB_OFFLINE=1 PYTHONPATH=src .venv/bin/python -m agent_app.sync_cli --embed-pending 2>&1 | tail -1

echo
echo "=== 6. 索引纯净性检查 ==="
PYTHONPATH=src .venv/bin/python - <<'PYEOF'
from agent_app.indexing.metadata_store import MetadataStore
store = MetadataStore("data/meta.db")
rows = store.conn.execute(
    "SELECT path FROM files WHERE project='dev-kb' AND ("
    "path LIKE '%.pytest_cache%' OR path LIKE '%/baselines/%' "
    "OR path LIKE '%cases_v1.jsonl')").fetchall()
print(f"混入的评测/缓存文件：{len(rows)} 个")
PYEOF

echo
echo "=== 7. 全量测试 ==="
PYTHONPATH=src .venv/bin/python -m pytest -q
