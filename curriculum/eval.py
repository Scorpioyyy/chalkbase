"""VeriChalk 评测入口：`python -m curriculum.eval`。

运行三层评测（CLAUDE.md 4.2 节）：
  1. 不变量：跑 tests/ 下的 pytest，必须 100% 通过。
  2. 组件指标：按 stage 从 eval/specs/ 与已产出的金标计算，Stage 0 阶段各阶段尚未实现，占位为空。
  3. 下游探针：同上，Stage 0 阶段占位为空。

结果追加写入 reports/eval_history.jsonl，并重新生成 reports/eval.md。
每个 stage 的具体指标函数应在对应 Stage 完成实现后，注册到 STAGE_METRIC_FUNCS 里，
而不是把计算逻辑写在本文件——本文件只负责编排、汇总与报告渲染。
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = ROOT / "reports"
HISTORY_PATH = REPORTS_DIR / "eval_history.jsonl"
EVAL_MD_PATH = REPORTS_DIR / "eval.md"

# 各 stage 的组件指标计算函数：stage 完成实现后在此注册，签名 () -> dict。
# 尚未实现的 stage 保持未注册，报告中标注"未实现"。
STAGE_METRIC_FUNCS: dict[str, Callable[[], dict]] = {}

STAGE_NAMES = [
    "stage1_extraction",
    "stage2_entity_resolution",
    "stage3_archetype_induction",
    "stage4_relation_inference",
    "stage5_reconciliation",
    "stage6_capability_boundary",
    "stage7_query_interface",
]


def run_invariants() -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=no"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    passed = result.returncode == 0
    return {
        "passed": passed,
        "returncode": result.returncode,
        "summary_tail": result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "",
    }


def run_stage_metrics() -> dict:
    metrics = {}
    for stage in STAGE_NAMES:
        func = STAGE_METRIC_FUNCS.get(stage)
        metrics[stage] = func() if func else {"implemented": False}
    return metrics


def run_downstream_probes() -> dict:
    # Stage 0：探针问题清单已起草（eval/probes/retrieval_probes.md），金标与运行逻辑
    # 待 Stage 3/6/7 陆续实现后接入，此处占位。
    return {
        "retrieval_probe": {"implemented": False},
        "boundary_probe": {"implemented": False},
        "generation_probe": {"implemented": False},
    }


def render_report(record: dict) -> str:
    lines = ["# VeriChalk 评测报告", "", f"最近一次运行：{record['timestamp']}", ""]
    lines.append("## 不变量")
    inv = record["invariants"]
    status = "通过" if inv["passed"] else "**未通过**"
    lines.append(f"- pytest 状态：{status}（{inv['summary_tail']}）")
    lines.append("")
    lines.append("## 组件指标")
    lines.append("| Stage | 状态 |")
    lines.append("|---|---|")
    for stage, m in record["stage_metrics"].items():
        state = "未实现" if not m.get("implemented", True) else "已实现"
        lines.append(f"| {stage} | {state} |")
    lines.append("")
    lines.append("## 下游探针")
    lines.append("| 探针 | 状态 |")
    lines.append("|---|---|")
    for name, m in record["downstream_probes"].items():
        state = "未实现" if not m.get("implemented", True) else "已实现"
        lines.append(f"| {name} | {state} |")
    lines.append("")
    lines.append("---")
    lines.append("历史记录见 `reports/eval_history.jsonl`；指标定义变更记录见 `eval/CHANGELOG.md`。")
    return "\n".join(lines) + "\n"


def main() -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "invariants": run_invariants(),
        "stage_metrics": run_stage_metrics(),
        "downstream_probes": run_downstream_probes(),
    }
    with HISTORY_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    EVAL_MD_PATH.write_text(render_report(record), encoding="utf-8")
    print(f"wrote {EVAL_MD_PATH}")
    print(f"appended {HISTORY_PATH}")
    if not record["invariants"]["passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
