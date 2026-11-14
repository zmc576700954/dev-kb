#!/bin/sh
# README op 校验修复后的完整验证流程
set -e
cd /Users/zhumingchen/dev-kb

echo "=== 1. 新增测试 ==="
PYTHONPATH=src .venv/bin/python -m pytest tests/test_kb_readme_ops.py -q

echo
echo "=== 2. 全量测试 ==="
PYTHONPATH=src .venv/bin/python -m pytest -q

echo
echo "=== 3. 当前提案状态 ==="
PYTHONPATH=src .venv/bin/python -m agent_app.kb_spec.cli proposals list

echo
echo "=== 4. ai-video-kb 全库校验 ==="
PYTHONPATH=src .venv/bin/python -m agent_app.kb_spec.cli validate ai-video-kb --progress
