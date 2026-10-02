"""Stage 5 评测（eval/specs/stage5.md）：不变量检查（供 tests 与 curriculum.eval 共用）+ 组件指标。"""
from __future__ import annotations

import networkx as nx

from curriculum.common import DATA_DIR, EVAL_DIR, lesson_order, read_json
from curriculum.metrics import wilson


def load():
    kps = {k["id"]: k for k in read_json(DATA_DIR / "knowledge_points.json")}
    lessons = read_json(DATA_DIR / "lessons.json")
    edges = read_json(DATA_DIR / "edges_relations.json")
    return kps, lessons, edges


def prereq_graph(kps, edges, direct_only: bool = False) -> nx.DiGraph:
    g = nx.DiGraph()
    g.add_nodes_from(kps)
    g.add_edges_from((e["from_knowledge_point_id"], e["to_knowledge_point_id"]) for e in edges if e["type"] == "prerequisite" and (e["is_direct"] or not direct_only))
    return g


def check_invariants() -> dict[str, list]:
    """返回 {不变量名: 违反项列表}，全部为空才算通过。"""
    kps, lessons, edges = load()
    order = lesson_order()
    pos = {k: order[v["first_introduced_lesson_id"]] for k, v in kps.items()}
    out: dict[str, list] = {}
    out["order_consistency"] = [e["id"] for e in edges if e["type"] == "prerequisite" and pos[e["from_knowledge_point_id"]] > pos[e["to_knowledge_point_id"]]]
    out["dangling_edge_refs"] = [e["id"] for e in edges if e["from_knowledge_point_id"] not in kps or e["to_knowledge_point_id"] not in kps]
    refs = set()
    for l in lessons:
        refs.update(l["intro_knowledge_point_ids"] + l["practice_knowledge_point_ids"])
    out["dangling_lesson_refs"] = sorted(refs - set(kps))
    out["no_intro_position"] = [k for k, v in kps.items() if v["first_introduced_lesson_id"] not in order]
    # 课时 intro 与 first_introduced 双向一致
    intro_of = {}
    bad = []
    for l in lessons:
        for k in l["intro_knowledge_point_ids"]:
            if kps[k]["first_introduced_lesson_id"] != l["id"]:
                bad.append((l["id"], k))
            intro_of[k] = l["id"]
        if set(l["intro_knowledge_point_ids"]) & set(l["practice_knowledge_point_ids"]):
            bad.append((l["id"], "intro/practice 重叠"))
    out["lesson_intro_mismatch"] = bad
    out["kp_without_intro_lesson_listing"] = [k for k, v in kps.items() if intro_of.get(k) != v["first_introduced_lesson_id"] and v["provenance"] == "reconciled"]
    out["review_before_intro"] = [k for k, v in kps.items() if any(order[l] < pos[k] for l in v["review_lesson_ids"])]
    g = prereq_graph(kps, edges)
    out["prerequisite_cycle"] = [] if nx.is_directed_acyclic_graph(g) else [str(nx.find_cycle(g))]
    # 约简保持可达性：仅用直接边的闭包 = 全部前置边的闭包；每条隐含边有替代路径
    if not out["prerequisite_cycle"]:
        gd = prereq_graph(kps, edges, direct_only=True)
        diff = [e["id"] for e in edges if e["type"] == "prerequisite" and not e["is_direct"] and not nx.has_path(gd, e["from_knowledge_point_id"], e["to_knowledge_point_id"])]
        extra = nx.transitive_closure(gd).number_of_edges() - nx.transitive_closure(g).number_of_edges()
        out["reduction_reachability"] = diff + ([f"闭包边数差 {extra}"] if extra else [])
        anc = {n: nx.ancestors(gd, n) for n in gd}
        # 直接边 a→b 不可被其他路径取代：a 的其他直接后继 c 不能到达 b
        out["reduction_minimal"] = [e["id"] for e in edges if e["type"] == "prerequisite" and e["is_direct"]
                                    and any(c != e["to_knowledge_point_id"] and c in anc[e["to_knowledge_point_id"]] for c in gd.successors(e["from_knowledge_point_id"]))]
    # 最小性：每个被前移的知识点，新位置恰等于某条出边终点的引入课时
    mins = []
    for k, v in kps.items():
        if v["provenance"] == "reconciled" and "顺序冲突修复" in (v.get("provenance_note") or ""):
            ends = [pos[e["to_knowledge_point_id"]] for e in edges if e["type"] == "prerequisite" and e["from_knowledge_point_id"] == k]
            if pos[k] not in ends:
                mins.append(k)
    out["move_minimality"] = mins
    out["reconciled_have_notes"] = [k for k, v in kps.items() if v["provenance"] == "reconciled" and not v.get("provenance_note")] + \
                                   [e["id"] for e in edges if e["provenance"] == "reconciled" and not e.get("provenance_note")]
    cov = DATA_DIR / "curriculum_coverage.json"
    if cov.exists():
        c = read_json(cov)
        std_n = len(__import__("curriculum.stage5.standard", fromlist=["x"]).load_standard())
        out["coverage_complete"] = [i["item_id"] for i in c["items"] if i["status"] == "uncovered"] + ([] if len(c["items"]) == std_n else ["条目数不一致"])
        out["coverage_refs_valid"] = [i["item_id"] for i in c["items"] if i["status"] == "covered" and (not i["kp_ids"] or not set(i["kp_ids"]) <= set(kps))] + \
                                     [i["item_id"] for i in c["items"] if i["status"] == "not_applicable" and not i.get("reason")]
    else:
        out["coverage_complete"] = ["data/curriculum_coverage.json 不存在"]
    return out


def seed_probe() -> dict:
    """D14 种子缺口：长方形/正方形面积、面积单位 必须出现在补全的知识点里。"""
    kps, _, _ = load()
    rec = [v for v in kps.values() if v["provenance"] == "reconciled"]
    area = any("长方形" in v["name"] and "面积" in v["name"] for v in rec)
    unit = any("面积单位" in v["name"] for v in rec)
    return {"rectangle_area": area, "area_units": unit}


def metrics(split: str = "val") -> dict:
    if not (DATA_DIR / "stage5_reconciliation.json").exists():
        return {"implemented": False}
    kps, lessons, edges = load()
    log = read_json(DATA_DIR / "stage5_reconciliation.json")
    inv = check_invariants()
    hard = {k: v for k, v in inv.items() if v}
    headline = {
        "order_violations": {"value": len(inv["order_consistency"]), "baseline": log.get("initial_order_conflicts"), "threshold": 0, "pass": not inv["order_consistency"]},
        "prerequisite_cycles": {"value": len(inv["prerequisite_cycle"]), "baseline": None, "threshold": 0, "pass": not inv["prerequisite_cycle"]},
        "invariants_violated": {"value": len(hard), "baseline": None, "threshold": 0, "pass": not hard, "detail": ", ".join(hard) if hard else ""},
    }
    cov = DATA_DIR / "curriculum_coverage.json"
    if cov.exists():
        c = read_json(cov)
        n = c["n_items"]
        ok = sum(1 for i in c["items"] if i["status"] in ("covered", "not_applicable"))
        w = wilson(ok, n)
        headline["standard_coverage"] = {"value": w["p"], "ci": [w["lo"], w["hi"]], "n": n, "baseline": log.get("coverage_before", {}).get("rate"), "threshold": 1.0, "pass": ok == n,
                                         "detail": f"其中不适用 {sum(1 for i in c['items'] if i['status'] == 'not_applicable')} 条"}
    rv = EVAL_DIR / "annotation" / "reconciliation" / "stats.json"
    if rv.exists():
        st = read_json(rv)["by_kind"]["_all"]
        pr = st["pass_rate"]
        r1 = EVAL_DIR / "annotation" / "reconciliation" / "stats_round1.json"
        first = read_json(r1)["by_kind"]["_all"]["pass_rate"] if r1.exists() else None
        headline["reconciled_review_pass_rate"] = {"value": pr["p"], "ci": [pr["lo"], pr["hi"]], "n": st["n"], "baseline": first["p"] if first else None, "threshold": 0.90, "pass": pr["p"] >= 0.90,
                                                   "detail": "基线=首轮审阅（修复前）通过率；当前=否决项按类型修正、从原始状态重建后的再次审阅"}
    seed = seed_probe()
    headline["d14_seed_gaps_found"] = {"value": sum(seed.values()), "baseline": 0, "threshold": 2, "pass": all(seed.values())}
    headline["moved_introductions"] = {"value": len(log["moves"]), "baseline": None, "threshold": None, "pass": None}
    headline["gap_kps_added"] = {"value": len(log["gaps"]), "baseline": 0, "threshold": None, "pass": None}
    out = {"implemented": True, "split": split, "headline": headline, "invariants": inv, "log_summary": {k: (len(v) if isinstance(v, list) else v) for k, v in log.items() if k != "implied"}}
    # Stage 4 指标在最终图上重算（闭包 P/R/F1；金标不变）
    from curriculum.stage4.evaluate import metrics as s4

    m = s4(split)
    after = m["headline"]
    before = (log.get("stage4_before") or {}).get(split)
    for name in ("closure_f1", "closure_precision", "closure_recall"):
        h = dict(after[name])
        h["baseline"] = (before or {}).get(name)
        if name == "closure_f1" and before:
            h["threshold"] = round(before["closure_f1"] - 0.02, 4)
            h["pass"] = (h["value"] or 0) >= h["threshold"]
        headline[f"stage4_final_{name}"] = h
    out["stage4_final"] = {"candidate_recall": after["candidate_recall"], "anchor_closure_recall": after["anchor_closure_recall"], "before": before}
    return out
