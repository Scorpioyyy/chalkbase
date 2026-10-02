"""Stage 6 组件指标（注册到 curriculum/eval.py）：不变量类内部一致性、越界探针查准查全、生成探针边界通过率。"""
from __future__ import annotations

from curriculum.boundary import genprobe
from curriculum.common import DATA_DIR, ROOT, read_json
from curriculum.metrics import wilson

THRESH_PR = 0.95


def _h(value, ci=None, n=None, baseline=None, threshold=None, passed=None, detail=None) -> dict:
    return {"value": value, "ci": ci, "n": n, "baseline": baseline, "threshold": threshold, "pass": passed, "detail": detail}


def _pr(block: dict, which: str):
    x = block["overall"][which]
    return (x["p"], [x["lo"], x["hi"]], x["k"] if "k" in x else None, x["n"]) if x else (None, None, None, 0)


def metrics(split: str = "val") -> dict:
    if not (DATA_DIR / "boundaries.json").exists():
        return {"implemented": False}
    from curriculum.boundary import probe

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
    gold_ok = (ROOT / "eval" / "gold" / split / "boundary_probe.jsonl").exists()
    out: dict = {"implemented": True, "split": split, "headline": headline, "report_lines": []}
    if gold_ok:
        ev = probe.evaluate(split)
        base = ev["baseline_int_decimal_only"]["overall"]
        bp = base["precision"]["p"] if base["precision"] else None
        br = base["recall"]["p"] if base["recall"] else None
        for key, label in (("A_oracle_features", "越界探针·链路A（标准特征）"), ("A_oracle_agreed_subset", "越界探针·链路A（构造意图与金标一致的子集）"),
                           ("B_extracted_features", "越界探针·链路B（题面抽特征，端到端）")):
            blk = ev.get(key)
            if not blk:
                continue
            for w, nm in (("precision", "查准"), ("recall", "查全")):
                v, ci, _, n = _pr(blk, w)
                if v is None:
                    continue
                headline[f"{label}{nm}"] = _h(v, ci, n, bp if w == "precision" else br, THRESH_PR, (v >= THRESH_PR) if key != "B_extracted_features" else None)
        out["current"] = {"boundary_probe": {k: ev[k] for k in ("n_gold", "gold_label_counts", "constructor_intent_agreement", "n_human_queue_or_failed")}}
        out["report_lines"] += [
            f"越界探针金标 {ev['n_gold']} 条（{ev['gold_label_counts']}）；构造意图与金标一致率 {ev['constructor_intent_agreement']['p']}；仲裁失败/人工队列 {ev['n_human_queue_or_failed']} 条。",
            "容差口径（紧邻 2 课时内才引入的能力不计为越界误报）与按维度结果见 `work/stage6/probe_eval_%s.json`。" % split,
        ]
    return out
