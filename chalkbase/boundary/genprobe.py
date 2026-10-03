"""Stage 6 · 补测 Stage 3 生成探针的边界通过率。

对 data/archetypes.json 中全部 `program` 类题型，在槽位约束内用固定种子采样 20 组参数，把参数（及程序算出的答案）
转成 `ItemFeatures`，用 `check_item` 判断是否在题型所在课时的边界内。题型所在课时 = 其源实例所在课时中最晚的一个
（题型的参数包络取自全部源实例，最晚课时是该包络完整出现的位置）；另报告取最早课时时的通过率作参考。

可重复运行：`python -m chalkbase.boundary gen-probe`（archetypes.json 变化后重跑）。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from chalkbase.boundary.check import check_item, default_store
from chalkbase.common import ROOT, write_json, read_json
from chalkbase.metrics import wilson
from chalkbase.models import ItemArchetype
from chalkbase.query.store import Curriculum
from chalkbase.runtime import InstantiationError, params_features, slot_types  # noqa: F401  （params_features 为旧导入路径兼容）
from chalkbase.runtime.instantiate import sample_candidates

N_SAMPLES = 20
SEED_BASE = 4242
THRESHOLD = 0.98


def _probe_one(cur: Curriculum, a: ItemArchetype, idx: int) -> dict:
    lessons = cur.archetype_lessons(a.id)
    slots = a.parameter_constraints["slots"]
    out = {"id": a.id, "lesson_latest": lessons[-1] if lessons else None, "lesson_earliest": lessons[0] if lessons else None,
           "n": 0, "ok_latest": 0, "ok_earliest": 0, "dims": {}}
    try:
        cands = sample_candidates(a, [SEED_BASE + idx], N_SAMPLES, timeout=60.0)[0]
    except InstantiationError as e:
        out["error"] = str(e)
        return out
    if any(c.get("error") == "constraints_unsatisfiable" for c in cands) or not lessons:
        out["error"] = "constraints_unsatisfiable" if lessons else "no lesson"
        return out
    st = default_store()
    for item in cands:
        if not item.get("ok"):
            continue
        f = params_features(item["params"], slots)  # 题目参数（答案不计：答案是求解结果，教材同类题的答案本来就比参数大）
        out["n"] += 1
        rep = check_item(f, lessons[-1], st)
        out["ok_latest"] += rep.in_bounds
        for v in rep.violations:
            out["dims"][v.dimension] = out["dims"].get(v.dimension, 0) + 1
        out["ok_earliest"] += check_item(f, lessons[0], st).in_bounds
    return out


def run(workers: int = 8) -> dict:
    cur = Curriculum()
    arch = [a for a in cur.archetypes_by_id.values() if a.verifiable_type.value == "program"]
    with ThreadPoolExecutor(workers) as ex:
        rows = list(ex.map(lambda t: _probe_one(cur, t[1], t[0]), enumerate(arch)))
    n = sum(r["n"] for r in rows)
    ok = sum(r["ok_latest"] for r in rows)
    ok_early = sum(r["ok_earliest"] for r in rows)
    fails = [r for r in rows if r["ok_latest"] < r["n"] or r.get("error")]
    dims: dict[str, int] = {}
    for r in rows:
        for d, c in r["dims"].items():
            dims[d] = dims.get(d, 0) + c
    rep = {"n_archetypes": len(arch), "n_samples": n, "pass_rate_latest_lesson": wilson(ok, n), "pass_rate_earliest_lesson": wilson(ok_early, n),
           "threshold": THRESHOLD, "violating_dimension_counts": dims, "n_failing_archetypes": len(fails),
           "failing_examples": [{k: r[k] for k in ("id", "lesson_latest", "n", "ok_latest", "dims", "error") if k in r} for r in fails[:20]]}
    write_json(ROOT / "work" / "stage6" / "gen_probe.json", {**rep, "failing_all": fails})
    return rep


def metrics() -> dict:
    p = ROOT / "work" / "stage6" / "gen_probe.json"
    return read_json(p) if p.exists() else {}
