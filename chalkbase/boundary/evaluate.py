"""Stage 6 组件指标（注册到 chalkbase/eval.py）：不变量类内部一致性、越界探针查准查全、生成探针边界通过率。

越界探针的结论是三值的：in / borderline（只有「同一单元内稍后才引入」的边界附近越界，建议人工复核）/ out。
报告三种口径：strict（borderline 算越界）、lenient（borderline 算在范围内）、decided（丢掉 borderline、只评有明确结论的条目，同时报告覆盖率）。
验收线见 THRESH 与 docs/decisions.md D28。
"""
from __future__ import annotations

from chalkbase.boundary import genprobe
from chalkbase.common import DATA_DIR, ROOT, read_json
from chalkbase.metrics import wilson

# 验收线（D28）：初版「查准查全均 ≥0.95」在当前金标质量（双标注者 κ≈0.64）下不可测，改为分链路设线。
# 越界的代价不对称（超纲题给到学生是验证闭环要防的伤害；误报只触发重写/复核），所以端到端链路优先保查全。
THRESH = {("B_agreed", "recall"): 0.85, ("B_agreed", "precision"): 0.80, ("A_agreed", "precision"): 0.95}


def _h(value, ci=None, n=None, baseline=None, threshold=None, passed=None, detail=None) -> dict:
    return {"value": value, "ci": ci, "n": n, "baseline": baseline, "threshold": threshold, "pass": passed, "detail": detail}


def _pr(block: dict, which: str):
    x = block["overall"][which]
    return (x["p"], [x["lo"], x["hi"]], x["n"]) if x else (None, None, 0)


def metrics(split: str = "val") -> dict:
    if not (DATA_DIR / "boundaries.json").exists():
        return {"implemented": False}
    from chalkbase.boundary import probe

    rep_path = ROOT / "work" / "stage6" / "enrich_report.json"
    enrich = read_json(rep_path) if rep_path.exists() else {}
    headline: dict = {}
    if enrich:
        n = enrich["baseline_draft_instances"]["instances"]
        pre = enrich["semantic_only_instances"]["covered"]
        post = enrich["after_repair_recheck"]["covered"]
        base = enrich["baseline_draft_instances"]["covered"]
        headline["教材实例在所在课时边界内的比例（补全+修复后）"] = _h(round(post / n, 4), [wilson(post, n)["lo"], wilson(post, n)["hi"]], n, round(base / n, 4), 1.0, post == n,
                                                      "基线=仅用 Stage 1 草稿 grants；修复后必须 100%")
        headline["教材实例覆盖率（仅语义补全，未修复）"] = _h(round(pre / n, 4), [wilson(pre, n)["lo"], wilson(pre, n)["hi"]], n, round(base / n, 4), None, None,
                                                 f"修复条数 {enrich['repaired_instances']['repairs']}")
    gp = genprobe.metrics()
    if gp:
        r = gp["pass_rate_latest_lesson"]
        headline["生成探针边界通过率（program 类 × 20 采样）"] = _h(r["p"], [r["lo"], r["hi"]], r["n"], None, genprobe.THRESHOLD, r["p"] >= genprobe.THRESHOLD)
    out: dict = {"implemented": True, "split": split, "headline": headline, "report_lines": []}
    if (ROOT / "eval" / "gold" / split / "boundary_probe.jsonl").exists():
        ev = probe.evaluate(split)
        base = ev["baseline_int_decimal_only"]["strict"]
        bp = base["overall"]["precision"]["p"] if base["overall"]["precision"] else None
        br = base["overall"]["recall"]["p"] if base["overall"]["recall"] else None
        rows = [("B_agreed", "decided", "越界探针·端到端(B)·意图一致子集·decided"),
                ("B_all", "decided", "越界探针·端到端(B)·全部金标·decided"),
                ("B_all", "strict", "越界探针·端到端(B)·全部金标·strict"),
                ("A_agreed", "decided", "越界探针·标准特征(A)·意图一致子集·decided")]
        for key, view, label in rows:
            blk = ev[key][view]
            for w, nm in (("precision", "查准"), ("recall", "查全")):
                v, ci, n = _pr(blk, w)
                if v is None:
                    continue
                th = THRESH.get((key, w)) if view == "decided" else None
                headline[f"{label}{nm}"] = _h(v, ci, n, bp if w == "precision" else br, th, (v >= th) if th else None,
                                              f"覆盖率 {blk['coverage']['p']}" if blk.get("coverage") else None)
        nc = ev.get("A_nonconcept_out_recall")
        if nc:
            headline["越界探针·非概念维度越界查全（标准特征，测边界本身）"] = _h(nc["p"], [nc["lo"], nc["hi"]], nc["n"], None, None, None)
        out["current"] = {"boundary_probe": {k: ev[k] for k in ("n_gold", "gold_label_counts", "verdict_counts_B", "constructor_intent_agreement", "n_human_queue_or_failed")}}
        out["report_lines"] += [
            f"越界探针金标 {ev['n_gold']} 条（{ev['gold_label_counts']}）；构造意图与金标一致率 {ev['constructor_intent_agreement']['p']}；仲裁失败/人工队列 {ev['n_human_queue_or_failed']} 条；链路 B 结论分布 {ev['verdict_counts_B']}。",
            "三值结论（in/borderline/out）与 strict/lenient/decided 三种口径、按维度结果及失败样本见 `work/stage6/probe_eval_%s.json`。" % split,
        ]
    return out
