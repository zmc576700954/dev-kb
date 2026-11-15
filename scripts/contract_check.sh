#!/bin/sh
# 知识源接入契约门禁：校验失败即非零退出。
#
# 用法：
#   sh scripts/contract_check.sh                    # 用默认 projects.yaml 校验全部源
#   sh scripts/contract_check.sh --config path.yaml # 指定注册表
#   sh scripts/contract_check.sh --strict           # warning 也阻断
#
# 行为：
#   1. 对注册表里的每个知识源跑契约校验
#   2. error > 0 → 退出码 1（阻断合并）
#   3. warning → 输出但不阻断（--strict 时一并阻断）
#
# 索引级规则（R-02 / R-05 / R-06）需要 meta.db；没有索引时它们不适用，
# 声明级规则照常执行。
set -e
cd "$(dirname "$0")/.."

STRICT=0
CONFIG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --strict) STRICT=1 ;;
    --config) shift; CONFIG="$1" ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
  shift
done

CONFIG_ARG=""
[ -n "$CONFIG" ] && CONFIG_ARG="--config $CONFIG"

FAILED=0
for project in $(PYTHONPATH=src .venv/bin/python - $CONFIG_ARG <<'PYEOF' 2>&1
import sys
from agent_app.config import PROJECTS_EXAMPLE, default_projects_config, load_config
cfg = default_projects_config()
args = sys.argv[1:]
if "--config" in args:
    cfg = args[args.index("--config") + 1]
data = load_config(cfg, PROJECTS_EXAMPLE, "知识源注册表")
for p in data.get("projects", []):
    print(p["name"])
PYEOF
); do
  echo "=== 校验 $project ==="
  if ! PYTHONPATH=src .venv/bin/python -m agent_app.contract.cli $CONFIG_ARG validate "$project"; then
    FAILED=1
  fi
  echo
done

if [ "$FAILED" != "0" ]; then
  echo "✗ 知识源接入契约校验未通过（存在必须修复项）"
  exit 1
fi

if [ "$STRICT" = "1" ]; then
  WARNINGS=$(PYTHONPATH=src .venv/bin/python - $CONFIG_ARG <<'PYEOF' 2>&1
import sys
from agent_app.config import PROJECTS_EXAMPLE, default_projects_config, load_config
from agent_app.contract.service import ContractService
cfg = default_projects_config()
args = sys.argv[1:]
if "--config" in args:
    cfg = args[args.index("--config") + 1]
data = load_config(cfg, PROJECTS_EXAMPLE, "知识源注册表")
svc = ContractService(config=cfg)
total = 0
for p in data.get("projects", []):
    r = svc.validate(p["name"])
    total += r["summary"]["warnings"] + r["summary"]["reviews"]
print(total)
PYEOF
) || { echo "$WARNINGS"; exit 1; }
  if [ "$WARNINGS" != "0" ]; then
    echo "✗ 严格模式：仍有 $WARNINGS 处建议项"
    exit 1
  fi
fi

echo "✓ 知识源接入契约校验通过"
