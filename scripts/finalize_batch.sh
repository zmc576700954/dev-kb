#!/bin/sh
# 批量操作收尾：撤回过时的已批准提案 + 最终校验
cd "$(dirname "$0")/.."

echo "=== 1. 撤回过时的已批准提案（修复内容已被后续变更取代） ==="
PYTHONPATH=src .venv/bin/python -m agent_app.kb_spec.cli proposals reject 24 25 66 || true

echo
echo "=== 2. 剩余活动提案 ==="
PYTHONPATH=src .venv/bin/python -m agent_app.kb_spec.cli proposals list

echo
echo "=== 3. ai-video-kb 全库校验 ==="
PYTHONPATH=src .venv/bin/python -m agent_app.kb_spec.cli validate ai-video-kb --progress

echo
echo "=== 4. 索引同步（补向量） ==="
HF_HUB_OFFLINE=1 PYTHONPATH=src .venv/bin/python -m agent_app.sync_cli --embed-pending 2>&1 | tail -2
