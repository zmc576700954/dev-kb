#!/bin/sh
# 批量操作 + dry-run stale 预检的完整验证
set -e
cd /Users/zhumingchen/dev-kb

echo "=== 1. 批量操作测试 ==="
PYTHONPATH=src .venv/bin/python -m pytest tests/test_kb_cli_batch.py -q

echo
echo "=== 2. 全量测试 ==="
PYTHONPATH=src .venv/bin/python -m pytest -q

echo
echo "=== 3. dry-run 预检（ai-video-kb 的遗留提案应被标为 stale） ==="
PYTHONPATH=src .venv/bin/python -m agent_app.kb_spec.cli apply --project ai-video-kb --dry-run
