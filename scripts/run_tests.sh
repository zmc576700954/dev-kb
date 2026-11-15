#!/bin/sh
# 测试运行包装：便于在受限环境下执行。
# CI 可用 DEV_KB_PYTHON=python3 指定解释器（不创建本机 .venv）。
cd "$(dirname "$0")/.." || exit 1

if [ -n "$DEV_KB_PYTHON" ]; then
  PYTHON="$DEV_KB_PYTHON"
elif [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="python3"
fi

PYTHONPATH=src exec "$PYTHON" -m pytest "$@"
