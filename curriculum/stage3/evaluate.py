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


def metrics(split: str = "val") -> dict:
    if not (DATA_DIR / "archetypes.json").exists():
        return {"implemented": False}
    arch = read_json(DATA_DIR / "archetypes.json")
    ex = read_json(DATA_DIR / "exercises.json")
    groups = [{"instance_ids": a["source_instance_ids"]} for a in arch]
    cur_single = singleton_stats(groups, ex)
    base_groups = group_instances(ex, backoff=False)
    base_single = singleton_stats(base_groups, ex)
    probe = generation_probe(arch)
    gran = granularity(split)
    gran_base = granularity(split, "archetype_granularity_baseline")
    out = {
        "implemented": True,
        "split": split,
        "current": {"singletons": cur_single, "generation_probe": probe, "granularity": gran,
                    "verifiable_types": dict(Counter(a["verifiable_type"] for a in arch)),
                    "difficulty_dist": dict(sorted(Counter(a["difficulty"] for a in arch).items()))},
        "baseline_exact_signature": {"singletons": base_single, "granularity": gran_base},
    }
    out["headline"] = {
        "granularity_ok_rate": ({"value": gran["ok_rate"]["p"], "ci": [gran["ok_rate"]["lo"], gran["ok_rate"]["hi"]], "n": gran["ok_rate"]["n"],
                                 "baseline": gran_base["ok_rate"]["p"] if gran_base else None, "threshold": 0.85, "pass": gran["ok_rate"]["p"] >= 0.85}
                                if gran else {"value": None}),
        "generation_probe_program_rate": {"value": probe["rate"]["p"], "ci": [probe["rate"]["lo"], probe["rate"]["hi"]], "n": probe["rate"]["n"],
                                          "baseline": None, "threshold": 1.0, "pass": probe["rate"]["p"] == 1.0},
        "singleton_ratio": {"value": cur_single["singleton_ratio"], "baseline": base_single["singleton_ratio"], "threshold": 0.15,
                            "pass": cur_single["singleton_ratio"] <= 0.15, "detail": f"数据下限 {cur_single['data_floor_ratio']}"},
        "singleton_excess_over_floor": {"value": cur_single["excess_over_floor"], "baseline": base_single["excess_over_floor"], "threshold": 0.05,
                                        "pass": cur_single["excess_over_floor"] <= 0.05},
        "compression": {"value": cur_single["compression"], "baseline": base_single["compression"], "threshold": None, "pass": None,
                        "detail": f"{len(ex)} 实例 → {len(arch)} 题型"},
    }
    return out
