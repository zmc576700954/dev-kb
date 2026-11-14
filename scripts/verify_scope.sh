#!/bin/sh
# 接入 dev-kb 自身作为 KB 项目的验证
cd /Users/zhumingchen/dev-kb

echo "=== 1. kb_paths 范围测试 ==="
PYTHONPATH=src .venv/bin/python -m pytest tests/test_kb_scope.py -q

echo
echo "=== 2. 全量测试 ==="
PYTHONPATH=src .venv/bin/python -m pytest -q

echo
echo "=== 3. 项目列表（应含两个项目） ==="
PYTHONPATH=src .venv/bin/python -m agent_app.kb_spec.cli projects

echo
echo "=== 4. dev-kb 全库校验 ==="
PYTHONPATH=src .venv/bin/python -m agent_app.kb_spec.cli validate dev-kb --progress
