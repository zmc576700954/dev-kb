#!/usr/bin/env python3
"""诊断：dev-kb 的 evals/baselines 为何仍进入检索索引。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent_app.ingestion.scanner import scan_project
from agent_app.tools.projects import load_registry

ROOT = Path(__file__).resolve().parent.parent
dev = load_registry()["dev-kb"]
extra = dev.get("extra_exclude_dirs")
print("projects.yaml 的 extra_exclude_dirs:", extra)

report = scan_project("dev-kb", str(ROOT), extra)
paths = [f.path for f in report.files]
print(f"扫描到 {len(paths)} 个文件")
baselines = [p for p in paths if "baselines" in p or "results" in p]
print(f"其中含 baselines/results 的：{len(baselines)}")
for p in baselines[:8]:
    print("   ", p)
print("excluded_dirs 里相关项：",
      {k: v for k, v in report.excluded_dirs.items()
       if "baseline" in k or "result" in k})
