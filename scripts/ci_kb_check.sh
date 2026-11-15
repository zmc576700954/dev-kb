#!/bin/sh
# 知识库规范 CI 门禁：校验失败即非零退出。
#
# 用法（GitHub Actions）：
#   - name: KB 规范门禁
#     run: sh scripts/ci_kb_check.sh
#
# 行为：
#   1. 对每个登记的 KB 项目跑全库校验
#   2. error > 0 → 退出码 1（阻断合并）
#   3. warning/review → 输出待办清单，但不阻断（可选 --strict 一并阻断）
set -e
cd "$(dirname "$0")/.."

STRICT=0
[ "$1" = "--strict" ] && STRICT=1

PROJECTS=$(PYTHONPATH=src .venv/bin/python - <<'PYEOF' 2>&1
from agent_app.config import KB_PROJECTS_EXAMPLE, default_kb_projects_config, load_config
data = load_config(default_kb_projects_config(), KB_PROJECTS_EXAMPLE, "知识库项目注册表")
for p in data.get("projects", []):
    print(p["name"])
PYEOF
) || { echo "$PROJECTS"; exit 1; }

FAILED=0
for project in $PROJECTS; do
  echo "=== 校验 $project ==="
  if ! PYTHONPATH=src .venv/bin/python -m agent_app.kb_spec.cli validate "$project" --progress; then
    FAILED=1
  fi
  echo
done

if [ "$FAILED" != "0" ]; then
  echo "✗ 知识库规范校验未通过（存在必须修复项）"
  exit 1
fi

echo "✓ 知识库规范校验通过（无必须修复项）"

if [ "$STRICT" = "1" ]; then
  # 严格模式：warning/review 也阻断
  for project in $PROJECTS; do
    PYTHONPATH=src .venv/bin/python scripts/todo_report.py "$project" \
      --output "reports/kb-todo-$project.md" || exit 1
  done
  COUNT=$(PYTHONPATH=src .venv/bin/python - <<'PYEOF' 2>&1
from agent_app.config import KB_PROJECTS_EXAMPLE, default_kb_projects_config, load_config
from agent_app.kb_spec.service import KBSpecService
data = load_config(default_kb_projects_config(), KB_PROJECTS_EXAMPLE, "知识库项目注册表")
svc = KBSpecService()
total = 0
for p in data.get("projects", []):
    r = svc.kb_validate_project(p["name"])
    total += r["summary"]["warnings"] + r["summary"]["reviews"]
print(total)
PYEOF
) || { echo "$COUNT"; exit 1; }
  if [ "$COUNT" != "0" ]; then
    echo "✗ 严格模式：仍有 $COUNT 处建议/待判断项（见 reports/kb-todo-*.md）"
    exit 1
  fi
fi
