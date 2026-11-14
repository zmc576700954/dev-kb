#!/bin/sh
# 测试运行包装：便于在受限环境下执行
cd /Users/zhumingchen/dev-kb || exit 1
PYTHONPATH=src exec .venv/bin/python -m pytest "$@"
