"""Stage 2 组件指标（eval/specs/stage2.md §4）：对照成对金标与锚点金标。

系统（current）读取 data/ 产物；基线（baseline）为名称完全匹配合并。
"""
from __future__ import annotations

from collections import Counter, defaultdict

import networkx as nx

from chalkbase.common import DATA_DIR, EVAL_DIR, local_kps, read_json, read_jsonl, JUDGMENTS_DIR
from chalkbase.metrics import bcubed, per_class_prf, prf, wilson
from chalkbase.stage2.blocking import generate_candidates, pair_key

LABELS = ("same", "extends", "different")
THRESHOLDS = {"blocking_recall_same": 0.95, "pair_same_f1": 0.85, "extends_as_same_rate": 0.05, "bcubed_f1": 0.85}


def _gold(split: str):
    pairs = [g for g in read_jsonl(EVAL_DIR / "gold" / split / "entity_resolution.jsonl") if g.get("label")]
    anchors = read_jsonl(EVAL_DIR / "gold" / split / "entity_resolution_anchor.jsonl")
    return pairs, anchors


def _clusters_from_map(l2c: dict[str, str]) -> dict[str, frozenset]:
    groups = defaultdict(set)
    for k, c in l2c.items():
        groups[c].add(k)
    return {k: frozenset(groups[c]) for k, c in l2c.items()}


def _system_predictions():
    js = read_jsonl(JUDGMENTS_DIR / "stage2_pairwise.jsonl")
    pair_pred = {(j["a"], j["b"]): (j["label"], j.get("narrower")) for j in js}
    lmap = read_json(DATA_DIR / "kp_local_map.json")
    l2c = {f"{r['book_id']}::{r['local_id']}": r["canonical_id"] for r in lmap}
    return pair_pred, _clusters_from_map(l2c)


def _baseline_predictions():
    kps = local_kps()
    by_name = defaultdict(list)
    for k in kps:
        by_name[k["name"]].append(k["_key"])
    g = nx.Graph()
    g.add_nodes_from(k["_key"] for k in kps)
    pair_pred = {}
    for keys in by_name.values():
        for i, a in enumerate(keys):
            for b in keys[i + 1 :]:
                g.add_edge(a, b)
                pair_pred[pair_key(a, b)] = ("same", None)
    cl = {}
    for comp in nx.connected_components(g):
        fz = frozenset(comp)
        for k in comp:
            cl[k] = fz
    return pair_pred, cl


def _evaluate(pair_pred, cluster_of, candidate_set, pairs, anchors) -> dict:
    # --- 成对（判定级：未进入候选/未判定的对视为 different）
    gold_l = [p["label"] for p in pairs]
    pred_l = [pair_pred.get((p["a"], p["b"]), ("different", None))[0] for p in pairs]
    pc = per_class_prf(gold_l, pred_l, LABELS)
    # --- 聚类后成对（same = 同一规范簇）
    pred_cluster_same = ["same" if p["b"] in cluster_of[p["a"]] else "not_same" for p in pairs]
    gold_same = ["same" if g == "same" else "not_same" for g in gold_l]
    tp = sum(1 for g, q in zip(gold_same, pred_cluster_same) if g == q == "same")
    fp = sum(1 for g, q in zip(gold_same, pred_cluster_same) if g != "same" and q == "same")
    fn = sum(1 for g, q in zip(gold_same, pred_cluster_same) if g == "same" and q != "same")
    ext = [(g, q) for g, q in zip(gold_l, pred_l) if g == "extends"]
    ext_as_same = wilson(sum(1 for _, q in ext if q == "same"), len(ext))
    diff_as_same = wilson(sum(1 for g, q in zip(gold_l, pred_l) if g == "different" and q == "same"), sum(1 for g in gold_l if g == "different"))
    # 方向一致率
    dir_n = dir_k = 0
    for p, q in zip(pairs, pred_l):
        if p["label"] == "extends" and q == "extends":
            g_dir = (p.get("output") or {}).get("narrower")
            s_dir = pair_pred[(p["a"], p["b"])][1]
            if g_dir in ("A", "B") and s_dir in ("A", "B"):
                dir_n += 1
                dir_k += g_dir == s_dir
    # --- 锚点：blocking 召回 + B-cubed
    same_pairs = [pair_key(a["anchor"], x) for a in anchors for x in a["same"]]
    ext_pairs = [pair_key(a["anchor"], x) for a in anchors for x in a["extends"]]
    blk_same = wilson(sum(1 for p in same_pairs if p in candidate_set), len(same_pairs)) if candidate_set is not None else None
    blk_ext = wilson(sum(1 for p in ext_pairs if p in candidate_set), len(ext_pairs)) if candidate_set is not None else None
    gold_cluster = {a["anchor"]: frozenset([a["anchor"], *a["same"]]) for a in anchors}
    bc = bcubed(cluster_of, gold_cluster, [a["anchor"] for a in anchors])
    nontrivial = [a["anchor"] for a in anchors if len(gold_cluster[a["anchor"]]) > 1 or len(cluster_of[a["anchor"]]) > 1]
    bc_nt = bcubed(cluster_of, gold_cluster, nontrivial)
    # --- 失败样本归类
    kps = {k["_key"]: k for k in local_kps()}
    fails = Counter()
    fail_examples = defaultdict(list)
    for p, q in zip(pairs, pred_l):
        g = p["label"]
        if g == q:
            continue
        a, b = kps[p["a"]], kps[p["b"]]
        if "总复习" in a["name"] + b["name"] and q == "same":
            cat = "伞形复习点误并"
        elif g == "extends" and q == "same":
            cat = "扩展误判为同一"
        elif g == "same" and q != "same":
            cat = "漏并（跨主线/领域）" if (a["thread"], a["domain"]) != (b["thread"], b["domain"]) else "漏并（措辞/范围差异）"
        elif g == "different" and q == "same":
            cat = "不同考点误并"
        elif g == "extends" and q == "different":
            cat = "扩展漏判为不同"
        elif g == "different" and q == "extends":
            cat = "不同误判为扩展"
        else:
            cat = f"{g}→{q}"
        fails[cat] += 1
        if len(fail_examples[cat]) < 5:
            fail_examples[cat].append(f"{p['id']}: {a['_book']}「{a['name']}」 vs {b['_book']}「{b['name']}」 gold={g} pred={q}")
    return {
        "n_pairs": len(pairs),
        "n_anchors": len(anchors),
        "gold_label_dist": dict(Counter(gold_l)),
        "pairwise": pc,
        "pair_same_after_clustering": prf(tp, fp, fn),
        "extends_as_same_rate": ext_as_same,
        "different_as_same_rate": diff_as_same,
        "extends_direction_agreement": wilson(dir_k, dir_n),
        "blocking_recall_same": blk_same,
        "blocking_recall_extends": blk_ext,
        "bcubed": bc,
        "bcubed_nontrivial": bc_nt,
        "failure_categories": dict(fails.most_common()),
        "failure_examples": dict(fail_examples),
        "max_cluster_size": max(len(c) for c in cluster_of.values()),
    }


def metrics(split: str = "val") -> dict:
    pairs, anchors = _gold(split)
    if not pairs:
        return {"implemented": False}
    out = {"implemented": True, "split": split}
    bp, bc = _baseline_predictions()
    out["baseline"] = _evaluate(bp, bc, set(bp), pairs, anchors)
    if (DATA_DIR / "kp_local_map.json").exists():
        sp, sc = _system_predictions()
        out["current"] = _evaluate(sp, sc, set(generate_candidates()), pairs, anchors)
    cur = out.get("current", out["baseline"])
    base = out["baseline"]
    out["headline"] = {
        "blocking_recall_same": _hl(cur["blocking_recall_same"], base["blocking_recall_same"], THRESHOLDS["blocking_recall_same"]),
        "pair_same_f1": {"value": cur["pairwise"]["same"]["f1"], "baseline": base["pairwise"]["same"]["f1"], "threshold": 0.85,
                         "pass": (cur["pairwise"]["same"]["f1"] or 0) >= 0.85},
        "pair_same_precision": _hl(cur["pairwise"]["same"]["precision"], base["pairwise"]["same"]["precision"], None),
        "pair_same_recall": _hl(cur["pairwise"]["same"]["recall"], base["pairwise"]["same"]["recall"], None),
        "extends_as_same_rate": _hl(cur["extends_as_same_rate"], base["extends_as_same_rate"], 0.05, lower_is_better=True),
        "macro_f1_3class": {"value": cur["pairwise"]["macro_f1"], "baseline": base["pairwise"]["macro_f1"], "threshold": None, "pass": None},
        "bcubed_f1": {"value": cur["bcubed"]["f1"], "baseline": base["bcubed"]["f1"], "threshold": 0.85,
                      "pass": (cur["bcubed"]["f1"] or 0) >= 0.85,
                      "detail": f"P={cur['bcubed']['precision']} R={cur['bcubed']['recall']}"},
        "bcubed_f1_nontrivial": {"value": cur["bcubed_nontrivial"]["f1"], "baseline": base["bcubed_nontrivial"]["f1"], "threshold": None, "pass": None,
                                 "detail": f"n={cur['bcubed_nontrivial']['n']} P={cur['bcubed_nontrivial']['precision']} R={cur['bcubed_nontrivial']['recall']}（仅金标簇或预测簇大小≥2 的锚点）"},
    }
    return out


def _hl(cur: dict | None, base: dict | None, thr, lower_is_better: bool = False) -> dict:
    if cur is None:
        return {"value": None}
    v = cur["p"]
    ok = None
    if thr is not None and v is not None:
        ok = v <= thr if lower_is_better else v >= thr
    return {"value": v, "ci": [cur["lo"], cur["hi"]], "n": cur["n"], "baseline": base["p"] if base else None, "threshold": thr, "pass": ok}
