"""Stage 6 · 补测 Stage 3 生成探针的边界通过率。

对 data/archetypes.json 中全部 `program` 类题型，在槽位约束内用固定种子采样 20 组参数，把参数（及程序算出的答案）
转成 `ItemFeatures`，用 `check_item` 判断是否在题型所在课时的边界内。题型所在课时 = 其源实例所在课时中最晚的一个
（题型的参数包络取自全部源实例，最晚课时是该包络完整出现的位置）；另报告取最早课时时的通过率作参考。

可重复运行：`python -m curriculum.boundary gen-probe`（archetypes.json 变化后重跑）。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from fractions import Fraction

from curriculum.boundary.check import check_item, default_store
from curriculum.boundary.fold import lesson_key
from curriculum.boundary.vocab import fraction_type_of, parse_num
from curriculum.common import DATA_DIR, ROOT, read_json, write_json
from curriculum.metrics import wilson
from curriculum.models import ItemFeatures
from curriculum.stage3.generate import slot_types
from curriculum.stage3.sandbox import RUNNER


def run_solver(code: str, slot_types: dict, param_sets, constraints=None, timeout: float = 30.0, probe: dict | None = None) -> dict:
    """与 curriculum.stage3.sandbox.run_solver 等价，但全程 UTF-8 字节收发（Windows 下 gbk 区域编码会在题面含 ✓ 等字符时崩溃）。"""
    import json
    import subprocess
    import sys

    req = {"code": code, "slot_types": slot_types, "constraints": constraints or [], "probe": probe}
    try:
        p = subprocess.run([sys.executable, "-I", "-X", "utf8", "-c", RUNNER], input=json.dumps(req).encode("utf-8"),
                           capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    out = p.stdout.decode("utf-8", "replace").strip()
    if p.returncode != 0 or not out:
        return {"ok": False, "error": f"runner crashed: {p.stderr.decode('utf-8', 'replace')[-300:]}"}
    return json.loads(out.splitlines()[-1])

N_SAMPLES = 20
SEED_BASE = 4242
THRESHOLD = 0.98


def params_features(params: dict, slots: dict, result=None) -> ItemFeatures:
    ints, places, ftypes = [], 0, set()
    vals = [(slots.get(k, {}).get("type"), v) for k, v in params.items()]
    if result is not None:
        vals += [(None, x) for x in (result if isinstance(result, list) else [result])]
    for t, v in vals:
        if t == "choice" or isinstance(v, bool):
            continue
        n = parse_num(str(v))
        if n is None:
            continue
        if n.kind == "int":
            ints.append(int(n.value))
        elif n.kind == "dec":
            ints.append(int(n.value))
            places = max(places, n.places)
        elif n.kind == "frac":
            if n.den == 1:
                ints.append(n.num)
            else:
                ft = fraction_type_of(n)
                if ft:
                    ftypes.add(ft)
    return ItemFeatures(integer_max=max(ints, default=None), decimal_places=places or None, fraction_types=ftypes)


def _probe_one(a: dict, idx: int, lesson_of: dict[str, str], kp_intro: dict[str, str]) -> dict:
    lessons = sorted({lesson_of[i] for i in a["source_instance_ids"] if i in lesson_of}, key=lesson_key)
    if not lessons and a["primary_knowledge_point_id"] in kp_intro:  # Stage 5 补全的缺口题型没有教材源实例，取主知识点的引入课时
        lessons = [kp_intro[a["primary_knowledge_point_id"]]]
    pc = a["parameter_constraints"]
    types = slot_types(pc["slots"])
    r = run_solver(a["solver_program"], types, None, pc.get("constraints") or [],
                   probe={"slots": pc["slots"], "n": N_SAMPLES, "seed": SEED_BASE + idx})
    out = {"id": a["id"], "lesson_latest": lessons[-1] if lessons else None, "lesson_earliest": lessons[0] if lessons else None,
           "n": 0, "ok_latest": 0, "ok_earliest": 0, "dims": {}}
    if not r.get("ok") or not lessons:
        out["error"] = r.get("error", "no lesson")
        return out
    st = default_store()
    for item in r["results"]:
        if not item.get("ok"):
            continue
        f = params_features(item["params"], pc["slots"])  # 题目参数（答案不计：答案是求解结果，教材同类题的答案本来就比参数大）
        out["n"] += 1
        rep = check_item(f, lessons[-1], st)
        out["ok_latest"] += rep.in_bounds
        for v in rep.violations:
            out["dims"][v.dimension] = out["dims"].get(v.dimension, 0) + 1
        out["ok_earliest"] += check_item(f, lessons[0], st).in_bounds
    return out


def run(workers: int = 8) -> dict:
    arch = [a for a in read_json(DATA_DIR / "archetypes.json") if a["verifiable_type"] == "program"]
    lesson_of = {e["id"]: e["lesson_id"] for e in read_json(DATA_DIR / "exercises.json")}
    kp_intro = {k["id"]: k["first_introduced_lesson_id"] for k in read_json(DATA_DIR / "knowledge_points.json")}
    with ThreadPoolExecutor(workers) as ex:
        rows = list(ex.map(lambda t: _probe_one(t[1], t[0], lesson_of, kp_intro), enumerate(arch)))
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
