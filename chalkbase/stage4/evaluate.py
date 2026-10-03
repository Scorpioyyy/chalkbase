"""Stage 4 组件指标（eval/specs/stage4.md §4）：闭包 P/R/F1、候选召回、判定级五分类。"""
from __future__ import annotations

from collections import Counter, defaultdict

import networkx as nx

from chalkbase.common import DATA_DIR, EVAL_DIR, read_json, read_jsonl, JUDGMENTS_DIR
from chalkbase.metrics import per_class_prf, prf, wilson
from chalkbase.stage4.build import LABELS
from chalkbase.stage4.candidates import generate_candidates, load_canonical


def _ancestors(edges: list[tuple[str, str]], nodes) -> dict[str, set[str]]:
    g = nx.DiGraph()
    g.add_nodes_from(nodes)
    g.add_edges_from(edges)
    return {n: nx.ancestors(g, n) for n in g.nodes}


def baseline_edges() -> list[tuple[str, str]]:
    kps, pos, _ = load_canonical()
    by_thread = defaultdict(list)
    for k, v in kps.items():
        by_thread[(v["domain"], v["thread"])].append(k)
    out = []
    for ks in by_thread.values():
        ks.sort(key=lambda k: (pos[k], k))
        out += list(zip(ks, ks[1:]))
    return out


def system_edges() -> tuple[list[tuple[str, str]], dict[tuple[str, str], str]]:
    js = read_jsonl(JUDGMENTS_DIR / "stage4_relations.jsonl")
    all_edges = read_json(DATA_DIR / "edges_relations.json")
    typed = {(e["from_knowledge_point_id"], e["to_knowledge_point_id"]): e["type"] for e in all_edges}
    labels = {(j["a"], j["b"]): typed.get((j["a"], j["b"]), "none") for j in js}  # 判定级指标按最终边类型（含置信度降级）
    edges = [k for k, t in typed.items() if t == "prerequisite"]
    return edges, labels


def _evaluate(edges, labels, cands, pairs, anchors) -> dict:
    kps, _, _ = load_canonical()
    anc = _ancestors(edges, kps)
    gold = [p["label"] == "prerequisite" for p in pairs]
    pred = [p["a"] in anc[p["b"]] for p in pairs]
    tp = sum(1 for g, q in zip(gold, pred) if g and q)
    fp = sum(1 for g, q in zip(gold, pred) if not g and q)
    fn = sum(1 for g, q in zip(gold, pred) if g and not q)
    by_stratum = {}
    for s in sorted({p["stratum"] for p in pairs}):
        idx = [i for i, p in enumerate(pairs) if p["stratum"] == s]
        by_stratum[s] = {"n": len(idx), "gold_pos": sum(gold[i] for i in idx), "pred_pos": sum(pred[i] for i in idx),
                         "agree": sum(gold[i] == pred[i] for i in idx)}
    out = {"closure": prf(tp, fp, fn), "by_stratum": by_stratum}
    if labels is not None:
        cp = [p for p in pairs if (p["a"], p["b"]) in labels]
        out["judgment_5class"] = per_class_prf([p["label"] for p in cp], [labels[(p["a"], p["b"])] for p in cp], LABELS)
    gold_pairs = [(k, a["anchor"]) for a in anchors for k in a["prerequisites"]]
    if cands is not None:
        out["candidate_recall"] = wilson(sum(1 for p in gold_pairs if p in cands), len(gold_pairs))
    out["anchor_closure_recall"] = wilson(sum(1 for a, b in gold_pairs if a in anc[b]), len(gold_pairs))
    # 失败样本归类
    fails = Counter()
    examples = defaultdict(list)
    for p, g, q in zip(pairs, gold, pred):
        if g == q:
            continue
        ka, kb = kps[p["a"]], kps[p["b"]]
        if q and not g:
            cat = {"builds_on": "builds_on/prerequisite 边界", "related": "并列/相关误判为前置", "confusable": "易混淆误判为前置"}.get(p["label"], "无关误判为前置（闭包放大或直接误判）")
        else:
            cat = "跨主线前置漏判" if ka["thread"] != kb["thread"] else "同主线前置漏判"
            if p["stratum"] == "N":
                cat = "候选集之外的前置（漏召回）"
        fails[cat] += 1
        if len(examples[cat]) < 5:
            examples[cat].append(f"{p['id']}: 「{ka['name']}」→「{kb['name']}」 gold={p['label']} pred_reachable={q}")
    out["failure_categories"] = dict(fails.most_common())
    out["failure_examples"] = dict(examples)
    out["n_prerequisite_edges"] = len(edges)
    return out


def metrics(split: str = "val") -> dict:
    pairs = [g for g in read_jsonl(EVAL_DIR / "gold" / split / "prerequisite.jsonl") if g.get("label")]
    anchors = read_jsonl(EVAL_DIR / "gold" / split / "prerequisite_anchor.jsonl")
    if not pairs:
        return {"implemented": False}
    out = {"implemented": True, "split": split, "baseline": _evaluate(baseline_edges(), None, None, pairs, anchors)}
    if (DATA_DIR / "edges_relations.json").exists():
        edges, labels = system_edges()
        out["current"] = _evaluate(edges, labels, set(generate_candidates()), pairs, anchors)
        out["current"]["order_conflict_prerequisite_edges"] = sum(
            1 for e in read_json(DATA_DIR / "edges_relations.json") if e["type"] == "prerequisite" and e["evidence"]["order_conflict"])
    cur, base = out.get("current", out["baseline"]), out["baseline"]
    c, b = cur["closure"], base["closure"]
    out["headline"] = {
        "candidate_recall": {"value": cur.get("candidate_recall", {}).get("p"), "ci": [cur.get("candidate_recall", {}).get("lo"), cur.get("candidate_recall", {}).get("hi")],
                             "n": cur.get("candidate_recall", {}).get("n"), "baseline": None, "threshold": 0.95,
                             "pass": (cur.get("candidate_recall", {}).get("p") or 0) >= 0.95},
        "closure_f1": {"value": c["f1"], "baseline": b["f1"], "threshold": 0.80, "pass": (c["f1"] or 0) >= 0.80},
        "closure_precision": {"value": c["precision"]["p"], "ci": [c["precision"]["lo"], c["precision"]["hi"]], "n": c["precision"]["n"], "baseline": b["precision"]["p"], "threshold": None, "pass": None},
        "closure_recall": {"value": c["recall"]["p"], "ci": [c["recall"]["lo"], c["recall"]["hi"]], "n": c["recall"]["n"], "baseline": b["recall"]["p"], "threshold": None, "pass": None},
        "anchor_closure_recall": {"value": cur["anchor_closure_recall"]["p"], "ci": [cur["anchor_closure_recall"]["lo"], cur["anchor_closure_recall"]["hi"]],
                                  "n": cur["anchor_closure_recall"]["n"], "baseline": base["anchor_closure_recall"]["p"], "threshold": None, "pass": None},
    }
    if "judgment_5class" in cur:
        out["headline"]["judgment_macro_f1_5class"] = {"value": cur["judgment_5class"]["macro_f1"], "baseline": None, "threshold": None, "pass": None}
    return out
