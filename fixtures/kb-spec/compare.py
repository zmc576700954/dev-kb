#!/usr/bin/env python3
"""kb-spec 双实现 fixture 比对（kaiwu × rm 合并批次 2，Q8/Q15）。

对 fixtures/kb-spec 下的每个 KB（kaiwu-snapshot、synthetic 的每个项目）：
  1. 跑 kaiwu scripts/check_kb.py 的 check_project（Python 实现）；
  2. 跑 rm 仓库 cargo build 出的 `synapse validate <kb> --json`（Rust 实现）；
  3. 按映射表（mapping.json 的 shared_rules）断言两实现对**同一批文件**给出
     一致的触发/静默结论（判据等价；级别差异见 docs/kb-spec.md 附录 A，不比对）；
  4. 与双黄金基线比对：任一侧输出漂移即失败；
     `--update-baseline` 更新基线，**必须附 `--reason`**（基线冻结纪律，D9）。

确定性：两实现的"今天"都被钉在 mapping.json 的 pinned_date
（check_kb 被 monkeypatch，synapse 走 SYNAPSE_TODAY 环境变量）。
否则 M4/W6 这类时间敏感规则会让冻结基线随真实时间腐烂。

用法：
  python3 fixtures/kb-spec/compare.py \
    --kaiwu-repo ~/kaiwu --rm-repo ~/repository-manage
  python3 fixtures/kb-spec/compare.py ... --update-baseline --reason "..."
"""

from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
KB_ROOTS = ("kaiwu-snapshot", "synthetic")


def load_mapping() -> dict:
    return json.loads((HERE / "mapping.json").read_text(encoding="utf-8"))


def load_check_kb(path: Path):
    """按文件路径加载 check_kb 实现（冻结副本或 kaiwu 仓库脚本均可）。"""
    spec = importlib.util.spec_from_file_location("kb_spec_check_kb", path)
    module = importlib.util.module_from_spec(spec)
    # dataclass 的字符串注解解析依赖 sys.modules 里有本模块——先注册再执行。
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run_check_kb(check_kb, kb_root: Path, project: str, pinned: str) -> list[dict]:

    year, month, day = (int(x) for x in pinned.split("-"))

    class FrozenDate(datetime.date):
        @classmethod
        def today(cls):  # noqa: N805
            return cls(year, month, day)

    check_kb.date = FrozenDate
    findings = check_kb.check_project(kb_root, project)
    return sorted(
        ({"rule_id": f.rule_id, "level": f.level, "path": f.path} for f in findings),
        key=lambda x: (x["path"], x["rule_id"]),
    )


def flat_id_from_rel(rel: str) -> str:
    """镜像 rm entry::flat_id_from_rel：去扩展名、去保留前缀段、`/`→`-`。"""
    no_ext = rel.rsplit(".", 1)[0] if "." in rel.rsplit("/", 1)[-1] else rel
    parts = [p for p in no_ext.split("/") if p]
    kept = list(parts)
    while kept and kept[0].startswith("_"):
        kept.pop(0)
    return "-".join(kept) if kept else (parts[-1] if parts else rel)


def run_synapse(rm_repo: Path, kb_root: Path, pinned: str) -> list[dict]:
    binary = rm_repo / "src-tauri" / "target" / "debug" / "synapse"
    if not binary.exists():
        raise SystemExit(
            f"synapse CLI 不存在: {binary}\n"
            f"先在 rm 仓库执行: cargo build --bin synapse"
        )
    env = dict(os.environ, SYNAPSE_TODAY=pinned)
    proc = subprocess.run(
        [str(binary), "validate", str(kb_root), "--json"],
        capture_output=True,
        text=True,
        env=env,
    )
    report = json.loads(proc.stdout)
    out = []
    for issue in report.get("issues", []):
        if issue.get("ignored"):
            continue
        out.append(
            {
                "rule_id": issue["rule_id"],
                "severity": issue["severity"],
                "entry_id": issue["entry_id"],
                "message": issue["message"],
            }
        )
    return sorted(out, key=lambda x: (x["entry_id"], x["rule_id"]))


def entry_to_file(entries: set[str], entry_id: str) -> str | None:
    """entry_id → KB 内相对路径（多义时取唯一命中；查不到返回 None）。"""
    hits = [e for e in entries if flat_id_from_rel(e) == entry_id]
    if len(hits) == 1:
        return hits[0]
    if entry_id == "README":
        return "README.md"
    return None


def compare_side_equivalence(
    mapping: dict, kb_files: list[str], check_kb_findings: list[dict], synapse_issues: list[dict]
) -> list[str]:
    """按 shared_rules 断言两侧文件集合一致。返回违反清单。"""
    kaiwu_by_rule: dict[str, set[str]] = {}
    for f in check_kb_findings:
        kaiwu_by_rule.setdefault(f["rule_id"], set()).add(f["path"])
    rm_by_rule: dict[str, set[str]] = {}
    file_by_entry = {e: entry_to_file(set(kb_files), e) for e in {i["entry_id"] for i in synapse_issues}}
    for i in synapse_issues:
        path = file_by_entry.get(i["entry_id"])
        if path is None:
            path = f"<unresolved:{i['entry_id']}>"
        rm_by_rule.setdefault(i["rule_id"], set()).add(path)

    violations = []
    for kaiwu_rule, rm_rule in mapping["shared_rules"]:
        kw = kaiwu_by_rule.get(kaiwu_rule, set())
        rm = rm_by_rule.get(rm_rule, set())
        if kw != rm:
            violations.append(
                f"{kaiwu_rule} ↔ {rm_rule} 结论不一致:\n"
                f"    kaiwu-only: {sorted(kw - rm)}\n"
                f"    rm-only:    {sorted(rm - kw)}"
            )
    return violations


def canonical(kaiwu_by_project: dict, synapse_by_project: dict, equivalences: list[str]) -> dict:
    return {
        "check_kb": kaiwu_by_project,
        "synapse": synapse_by_project,
        "equivalence_violations": equivalences,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--kaiwu-repo", type=Path, default=None,
        help="kaiwu 仓库路径（用其 scripts/check_kb.py 实时比对）；缺省用 fixtures 里的冻结副本 frozen_check_kb.py（CI 模式，kaiwu 仓库无远端）",
    )
    parser.add_argument("--rm-repo", type=Path, required=True)
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--reason", default="", help="更新基线的理由（基线冻结纪律）")
    args = parser.parse_args()

    mapping = load_mapping()
    pinned = mapping["pinned_date"]

    # kaiwu 仓库无远端（F12），CI 拉不到——fixtures 里冻结了一份
    # frozen_check_kb.py（纯标准库），与 kaiwu-snapshot 同进退：
    # kaiwu 校验器变更时同步更新冻结副本并附理由重跑基线。
    check_kb_path = (
        args.kaiwu_repo / "scripts" / "check_kb.py"
        if args.kaiwu_repo
        else HERE / "frozen_check_kb.py"
    )
    check_kb = load_check_kb(check_kb_path)

    any_violation = False
    report: dict = {}

    for root_name in KB_ROOTS:
        root = HERE / root_name
        if not root.is_dir():
            continue
        # kaiwu-snapshot 的目录本身就是项目（check_project 需要「kb_root/项目名」
        # 两级），所以它的 kb_root 取 fixtures/kb-spec 本身。
        check_kb_root = HERE if root_name == "kaiwu-snapshot" else root
        projects = (
            [root_name]
            if root_name == "kaiwu-snapshot"
            else sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
        )
        kaiwu_by_project: dict = {}
        synapse_by_project: dict = {}
        violations: list[str] = []
        for project in projects:
            project_dir = root / project
            files = [str(p.relative_to(project_dir)) for p in project_dir.rglob("*.md")]
            kw = run_check_kb(check_kb, check_kb_root, project, pinned)
            rm = run_synapse(args.rm_repo, project_dir, pinned)
            kaiwu_by_project[project] = kw
            synapse_by_project[project] = rm
            for v in compare_side_equivalence(mapping, files, kw, rm):
                violations.append(f"[{project}] {v}")
        report[root_name] = canonical(kaiwu_by_project, synapse_by_project, violations)
        if violations:
            any_violation = True

    # 基线比对 / 更新
    for root_name, data in report.items():
        baseline_path = HERE / "baselines" / f"{root_name}.json"
        old = json.loads(baseline_path.read_text(encoding="utf-8")) if baseline_path.exists() else None
        if args.update_baseline:
            reason = args.reason.strip()
            if not reason:
                raise SystemExit("更新基线必须附 --reason（基线冻结纪律）")
            data["_reason"] = reason
            data["_updated"] = datetime.date.today().isoformat()
            baseline_path.parent.mkdir(parents=True, exist_ok=True)
            baseline_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            print(f"[baseline] {baseline_path} 已更新（{reason}）")
            continue
        if old is None:
            print(f"[baseline] {baseline_path} 不存在——用 --update-baseline --reason 生成")
            any_violation = True
            continue
        for key in ("check_kb", "synapse"):
            if data[key] != old.get(key):
                print(f"[baseline] {root_name}.{key} 输出与黄金基线漂移！")
                for project in sorted(set(data[key]) | set(old.get(key) or {})):
                    if data[key].get(project) != (old.get(key) or {}).get(project):
                        print(f"  [{project}]")
                        print(f"    baseline: {(old.get(key) or {}).get(project)}")
                        print(f"    current:  {data[key].get(project)}")
                any_violation = True
        if data["equivalence_violations"] != old.get("equivalence_violations", []) or data[
            "equivalence_violations"
        ]:
            for v in data["equivalence_violations"]:
                print(f"[equivalence] {v}")
            if data["equivalence_violations"]:
                any_violation = True

    if any_violation:
        print("\nFAIL：等价断言或基线比对未通过")
        return 1
    print("PASS：双实现判据等价，输出与黄金基线一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
