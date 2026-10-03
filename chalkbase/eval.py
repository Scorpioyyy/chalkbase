"""ChalkBase 评测入口：`python -m chalkbase.eval`。

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
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = ROOT / "reports"
HISTORY_PATH = REPORTS_DIR / "eval_history.jsonl"
EVAL_MD_PATH = REPORTS_DIR / "eval.md"

# 各 stage 的组件指标计算函数：stage 完成实现后在此注册，签名 (split) -> dict。
# 尚未实现的 stage 保持未注册，报告中标注"未实现"。
# 返回值约定：{"implemented": True, "split": ..., "headline": {指标: {value, ci?, n?, baseline, threshold, pass}}, ...详细字段}
def _stage2(split: str) -> dict:
    from chalkbase.stage2.evaluate import metrics

    return metrics(split)


def _stage3(split: str) -> dict:
    from chalkbase.stage3.evaluate import metrics

    return metrics(split)


def _stage4(split: str) -> dict:
    from chalkbase.stage4.evaluate import metrics

    return metrics(split)


def _stage5(split: str) -> dict:
    from chalkbase.stage5.evaluate import metrics

    return metrics(split)


def _stage7(split: str) -> dict:
    from chalkbase.query.evaluate import metrics

    return metrics(split)


def _stage6(split: str) -> dict:
    from chalkbase.boundary.evaluate import metrics

    return metrics(split)


def _stage1(split: str) -> dict:
    from chalkbase.stage1_eval import metrics

    return metrics(split)


STAGE_METRIC_FUNCS: dict[str, Callable[[str], dict]] = {
    "stage1_extraction": _stage1,
    "stage2_entity_resolution": _stage2,
    "stage3_archetype_induction": _stage3,
    "stage4_relation_inference": _stage4,
    "stage5_reconciliation": _stage5,
    "stage6_capability_boundary": _stage6,
    "stage7_query_interface": _stage7,
}

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
        encoding="utf-8",  # Windows 默认 gbk，子进程输出含中文/符号时会解码失败
        errors="replace",
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
    )
    passed = result.returncode == 0
    return {
        "passed": passed,
        "returncode": result.returncode,
        "summary_tail": result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "",
    }


def run_stage_metrics(split: str) -> dict:
    metrics = {}
    for stage in STAGE_NAMES:
        func = STAGE_METRIC_FUNCS.get(stage)
        metrics[stage] = func(split) if func else {"implemented": False}
    return metrics


def _retrieval_probe_summary(stage_metrics: dict) -> dict:
    h = (stage_metrics.get("stage7_query_interface") or {}).get("headline")
    if not h:
        return {"implemented": False}
    r, m = h["recall@5（封顶）"], h["MRR"]
    return {"implemented": True, "note": f"recall@5 {r['value']}（基线 {r['baseline']}）、MRR {m['value']}（基线 {m['baseline']}），n={r['n']}，详见 Stage 7 一节"}


def run_downstream_probes(stage_metrics: dict) -> dict:
    # 检索探针：金标已由模型组生成（eval/gold/*/retrieval_probe.jsonl），检索接口待 Stage 7 实现；
    # 越界探针待 Stage 6；生成探针的程序可验证率由 Stage 3 指标计算（边界通过率待 Stage 6 补测）。
    gen = (stage_metrics.get("stage3_archetype_induction") or {}).get("current", {}).get("generation_probe")
    return {
        "retrieval_probe": _retrieval_probe_summary(stage_metrics),
        "boundary_probe": {"implemented": True, "note": "指标见上方 stage6_capability_boundary；生成探针的边界通过率由 `python -m chalkbase.boundary gen-probe` 产出"}
        if (stage_metrics.get("stage6_capability_boundary") or {}).get("implemented") else {"implemented": False},
        "generation_probe": {"implemented": True, "program_rate": gen["rate"], "n_program_archetypes": gen["n_program_archetypes"]}
        if gen else {"implemented": False},
    }


def gold_quality_lines() -> list[str]:
    """金标质量：模型间一致性、各类条目数量、费用、抽样复核准确率。"""
    import glob

    rows = []
    for f in sorted(glob.glob(str(ROOT / "eval" / "annotation" / "*" / "stats*.json"))):
        st = json.loads(Path(f).read_text(encoding="utf-8"))
        name = Path(f).parent.name + ("（新验收集）" if "holdout" in f else "")
        counts = st.get("counts") or st.get("decision_counts") or {}
        kappa = st.get("cohen_kappa", st.get("cohen_kappa_on_union"))
        rows.append(f"| {name} | {st.get('n', st.get('n_probes', st.get('n_anchors')))} | {kappa} | "
                    f"{counts.get('consensus', 0)} / {counts.get('arbitrated', 0)} / {counts.get('human_queue', 0)} / {counts.get('failed', 0)} | {st.get('cost_cny')} |")
    if not rows:
        return []
    out = ["## 金标质量", "", "首轮由 qwen3.8-flash 与 deepseek-v4.1-flash 独立标注，分歧由 qwen3.8-max（思考模式）仲裁，仲裁置信度 < 0.7 的条目进入待复核队列。", "",
           "| 任务 | 条目数 | Cohen κ | 一致 / 仲裁 / 待复核 / 失败 | 费用（元） |", "|---|---|---|---|---|", *rows, ""]
    res = ROOT / "eval" / "gold" / "review_results.json"
    if res.exists():
        r = json.loads(res.read_text(encoding="utf-8"))["by_task"]
        out += ["**金标抽样复核**：对各类金标（每类每层分别抽样，共 79 条）按标注指南独立复核（复核时看不到分层信息、原始标注与金标），"
                "估计金标准确率如下（Wilson 95% CI）。每类 n=8～24，区间下限多数低于 0.95；题型粒度一项针对修订前的分组与指南，"
                "结论用于发现标注漂移并据此修订指南（见 `docs/design.md`），不代表当前粒度金标的准确率。", "",
                "| 金标 | 准确率 | 95% CI | n |", "|---|---|---|---|"]
        names = {"er": "实体消解（成对）", "era": "实体消解（锚点）", "pr": "前置关系", "rp": "检索探针", "ag": "题型粒度（旧指南）"}
        for t, layers in r.items():
            a_ = layers.get("_all", {}).get("accuracy", {})
            out.append(f"| {names.get(t, t)} | {a_.get('p')} | [{a_.get('lo')}, {a_.get('hi')}] | {a_.get('n')} |")
        out.append("")
    return out


def render_report(record: dict) -> str:
    lines = ["# ChalkBase 评测报告", "", f"最近一次运行：{record['timestamp']}", ""]
    lines.append("## 不变量")
    inv = record["invariants"]
    status = "通过" if inv["passed"] else "**未通过**"
    lines.append(f"- pytest 状态：{status}（{inv['summary_tail']}）")
    lines.append("")
    lines.append(f"## 组件指标（金标划分：{record.get('split', 'val')}）")
    lines.append("| Stage | 状态 |")
    lines.append("|---|---|")
    for stage, m in record["stage_metrics"].items():
        state = "未实现" if not m.get("implemented", True) else "已实现"
        lines.append(f"| {stage} | {state} |")
    lines.append("")
    for stage, m in record["stage_metrics"].items():
        if not m.get("headline"):
            continue
        lines.append(f"### {stage}")
        lines.append("| 指标 | 当前 | 95% CI | n | 基线 | 阈值 | 是否达标 |")
        lines.append("|---|---|---|---|---|---|---|")
        for name, h in m["headline"].items():
            ci = h.get("ci")
            ci_s = f"[{ci[0]}, {ci[1]}]" if ci else (h.get("detail") or "")
            passed = {True: "✅", False: "❌", None: "—"}[h.get("pass")]
            lines.append(f"| {name} | {h.get('value')} | {ci_s} | {h.get('n', '')} | {h.get('baseline')} | {h.get('threshold') if h.get('threshold') is not None else '—'} | {passed} |")
        cur = m.get("current") or {}
        if cur.get("failure_categories"):
            lines.append("")
            lines.append("主要失败类别：" + "；".join(f"{k} {v}" for k, v in cur["failure_categories"].items()))
        for extra in m.get("report_lines", []):
            lines.append(extra)
        lines.append("")
    lines.append("## 下游探针")
    lines.append("| 探针 | 状态 |")
    lines.append("|---|---|")
    for name, m in record["downstream_probes"].items():
        state = "未实现" if not m.get("implemented", True) else "已实现"
        if m.get("program_rate"):
            r = m["program_rate"]
            state += f"：program 类可验证率 {r['p']} [{r['lo']}, {r['hi']}]（{m['n_program_archetypes']} 个题型 × 20 次采样）"
        elif m.get("note"):
            state += f"（{m['note']}）"
        lines.append(f"| {name} | {state} |")
    lines.append("")
    lines += gold_quality_lines()
    limits = ROOT / "eval" / "known_limitations.md"
    if limits.exists():
        lines += [limits.read_text(encoding="utf-8").rstrip(), ""]
    lines.append("---")
    lines.append("历史记录见 `reports/eval_history.jsonl`；指标定义变更记录见 `eval/CHANGELOG.md`。")
    return "\n".join(lines) + "\n"


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="val", choices=["val", "test"], help="金标划分：开发用 val，阶段验收用 test")
    args = ap.parse_args()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "split": args.split,
        "invariants": run_invariants(),
        "stage_metrics": run_stage_metrics(args.split),
    }
    record["downstream_probes"] = run_downstream_probes(record["stage_metrics"])
    with HISTORY_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    EVAL_MD_PATH.write_text(render_report(record), encoding="utf-8")
    print(f"wrote {EVAL_MD_PATH}")
    print(f"appended {HISTORY_PATH}")
    if not record["invariants"]["passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
