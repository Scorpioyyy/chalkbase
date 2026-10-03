"""Stage 3 组件指标与生成探针（eval/specs/stage3.md §4）。"""
from __future__ import annotations

from collections import Counter

from curriculum.common import DATA_DIR, EVAL_DIR, read_json, read_jsonl
from curriculum.metrics import per_class_prf, wilson
from curriculum.stage3.generate import probe_card
from curriculum.stage3.grouping import group_instances, singleton_stats

GRANULARITY_LABELS = ("too_coarse", "ok", "too_fine")


def generation_probe(archetypes: list[dict]) -> dict:
    total = ok = 0
    failures = []
    for i, a in enumerate(archetypes):
        if a["verifiable_type"] != "program":
            continue
        r = probe_card({"verifiable_type": "program", "slots": a["parameter_constraints"]["slots"],
                        "constraints": a["parameter_constraints"]["constraints"], "solver": a["solver_program"]}, seed=777 + i)
        total += r["n"]
        ok += r["ok"]
        if r["ok"] < r["n"]:
            failures.append({"id": a["id"], "ok": r["ok"], "n": r["n"], "error": r.get("error")})
    return {"rate": wilson(ok, total), "n_program_archetypes": sum(1 for a in archetypes if a["verifiable_type"] == "program"), "failures": failures[:20],
            "n_failing_archetypes": len(failures)}


def granularity(split: str, gold_name: str = "archetype_granularity") -> dict | None:
    rows = [g for g in read_jsonl(EVAL_DIR / "gold" / split / f"{gold_name}.jsonl") if g.get("label")]
    if not rows:
        return None
    labels = [r["label"] for r in rows]
    return {
        "ok_rate": wilson(sum(1 for x in labels if x == "ok"), len(labels)),
        "dist": dict(Counter(labels)),
        "by_stratum": {s: dict(Counter(r["label"] for r in rows if r.get("stratum") == s)) for s in sorted({r.get("stratum") for r in rows})},
        "verifiable_type_agreement": _vt_agreement(rows),
    }


def _vt_agreement(rows: list[dict]) -> dict | None:
    arch = {a["id"]: a for a in read_json(DATA_DIR / "archetypes.json")}
    pairs = [((r.get("output") or {}).get("verifiable_type"), arch[r["archetype_id"]]["verifiable_type"]) for r in rows if r.get("archetype_id") in arch]
    pairs = [(g, p) for g, p in pairs if g]
    if not pairs:
        return None
    return per_class_prf([g for g, _ in pairs], [p for _, p in pairs], ("program", "rule", "human"))["accuracy"]


def _singleton_too_fine(split: str) -> dict:
    """门槛口径（D26）：粒度金标中单实例题型被判「过细」的比例 ≤ 0.10。"""
    rows = [g for g in read_jsonl(EVAL_DIR / "gold" / split / "archetype_granularity.jsonl") if g.get("label") and g.get("n_instances") == 1]
    if not rows:
        return {"value": None}
    k = sum(1 for r in rows if r["label"] == "too_fine")
    w = wilson(k, len(rows))
    return {"value": w["p"], "ci": [w["lo"], w["hi"]], "n": w["n"], "baseline": None, "threshold": 0.10, "pass": w["p"] <= 0.10,
            "detail": "单实例题型中被标注为过细的比例（含数据下限的单实例）"}


def metrics(split: str = "val") -> dict:
    if not (DATA_DIR / "archetypes.json").exists():
        return {"implemented": False}
    arch = read_json(DATA_DIR / "archetypes.json")
    ex = read_json(DATA_DIR / "exercises.json")
    groups = [{"instance_ids": a["source_instance_ids"]} for a in arch if a["provenance"] == "textbook"]  # reconciled 缺口题型无源实例，不计入压缩率/单实例
    cur_single = singleton_stats(groups, ex)
    base_groups = group_instances(ex, backoff=False)
    base_single = singleton_stats(base_groups, ex)
    probe = generation_probe(arch)
    gran = granularity(split)
    gran_base = granularity(split, "archetype_granularity_baseline")  # 基线 B：签名完全相同、无回退（指南 v2 重标）
    gran_v1 = granularity(split, "archetype_granularity_oldscheme")  # v1 方案（签名分桶 + 回退），指南 v2 重标
    out = {
        "implemented": True,
        "split": split,
        "current": {"singletons": cur_single, "generation_probe": probe, "granularity": gran,
                    "verifiable_types": dict(Counter(a["verifiable_type"] for a in arch)),
                    "difficulty_dist": dict(sorted(Counter(a["difficulty"] for a in arch).items()))},
        "baseline_exact_signature": {"singletons": base_single, "granularity": gran_base},
        "baseline_v1_signature_backoff": {"granularity": gran_v1},
    }
    n_g = gran["dist"] if gran else {}
    tot_g = sum(n_g.values())
    out["headline"] = {
        "granularity_ok_rate": ({"value": gran["ok_rate"]["p"], "ci": [gran["ok_rate"]["lo"], gran["ok_rate"]["hi"]], "n": gran["ok_rate"]["n"],
                                 "baseline": gran_v1["ok_rate"]["p"] if gran_v1 else None, "threshold": 0.80, "pass": gran["ok_rate"]["p"] >= 0.80,
                                 "detail": f"基线=v1 方案（指南 v2 重标）；签名完全相同基线 B={gran_base['ok_rate']['p'] if gran_base else None}"}
                                if gran else {"value": None}),
        "granularity_too_fine_rate": ({"value": round(n_g.get("too_fine", 0) / tot_g, 4), "baseline": round(gran_v1["dist"].get("too_fine", 0) / sum(gran_v1["dist"].values()), 4) if gran_v1 else None,
                                       "threshold": None, "pass": None, "detail": "报告：过细占比"} if gran else {"value": None}),
        "granularity_too_coarse_rate": ({"value": round(n_g.get("too_coarse", 0) / tot_g, 4), "baseline": round(gran_v1["dist"].get("too_coarse", 0) / sum(gran_v1["dist"].values()), 4) if gran_v1 else None,
                                         "threshold": None, "pass": None, "detail": "报告：过粗占比"} if gran else {"value": None}),
        "generation_probe_program_rate": {"value": probe["rate"]["p"], "ci": [probe["rate"]["lo"], probe["rate"]["hi"]], "n": probe["rate"]["n"],
                                          "baseline": None, "threshold": 1.0, "pass": probe["rate"]["p"] == 1.0},
        "singleton_ratio": {"value": cur_single["singleton_ratio"], "baseline": base_single["singleton_ratio"], "threshold": None,
                            "pass": None, "detail": f"原口径仅报告（原阈值 0.15）；数据下限 {cur_single['data_floor_ratio']}"},
        "singleton_excess_over_floor": {"value": cur_single["excess_over_floor"], "baseline": base_single["excess_over_floor"], "threshold": None,
                                        "pass": None, "detail": "报告：超出数据下限的单实例占比（D26：不再作门槛）"},
        "singleton_too_fine_rate": _singleton_too_fine(split),
        "compression": {"value": cur_single["compression"], "baseline": base_single["compression"], "threshold": 2.0, "pass": cur_single["compression"] >= 2.0,
                        "detail": f"{len(ex)} 实例 → {len(groups)} 教材题型（另有 {len(arch) - len(groups)} 个无教材实例的 reconciled 题型：缺口知识点 + 无主习题的知识点）"},
    }
    return out
