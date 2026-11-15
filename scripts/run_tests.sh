#!/bin/sh
# 测试运行包装：便于在受限环境下执行
cd "$(dirname "$0")/.." || exit 1
PYTHONPATH=src exec .venv/bin/python -m pytest "$@"
