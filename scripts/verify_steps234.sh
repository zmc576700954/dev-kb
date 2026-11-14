#!/bin/sh
# 第 2–4 步验证：待办清单 + CI 门禁 + MCP client
cd /Users/zhumingchen/dev-kb

echo "=== 1. 待办清单（ai-video-kb，前 25 行） ==="
PYTHONPATH=src .venv/bin/python scripts/todo_report.py ai-video-kb | head -25

echo
echo "=== 2. 待办清单写入文件 + JSON 模式 ==="
PYTHONPATH=src .venv/bin/python scripts/todo_report.py ai-video-kb --output reports/kb-todo-ai-video-kb.md
PYTHONPATH=src .venv/bin/python scripts/todo_report.py dev-kb --json | head -12

echo
echo "=== 3. CI 门禁 ==="
sh scripts/ci_kb_check.sh

echo
echo "=== 4. MCP client（agent_app 作为 client 调 kb_spec） ==="
PYTHONPATH=src .venv/bin/python - <<'PYEOF'
from agent_app.tools.kb_client import KbSpecClient
with KbSpecClient() as kb:
    print("登记项目：", [p["name"] for p in kb.projects()])
    r = kb.validate_project("ai-video-kb")
    print("ai-video-kb 校验：", r["summary"])
    g = kb.search_guided("我想写一篇 ComfyUI 视频化工作流的文档", project="ai-video-kb")
    print("引导查询 intent:", g["intent"], "关键词:", g["keywords"][:3])
    d = kb.draft_document("ai-video-kb", "ComfyUI 视频化参数调优")
    print("骨架生成：", d["status"], d["doc_type"], d["path"])
    prompt = kb.get_prompt("kb_write_guide", topic="ComfyUI 视频化参数调优", project="ai-video-kb")
    print("prompt 模板长度：", len(prompt), "字符")
PYEOF

echo
echo "=== 5. 全量测试 ==="
PYTHONPATH=src .venv/bin/python -m pytest -q
