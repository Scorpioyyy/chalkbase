"""评测度量工具：Wilson 置信区间、P/R/F1、Cohen's kappa、Krippendorff's alpha（名义）、B-cubed。"""
from __future__ import annotations

import math
from collections import Counter
from typing import Hashable, Iterable, Optional


def wilson(k: int, n: int, z: float = 1.96) -> dict:
    """比例 k/n 的 Wilson 95% 置信区间。"""
    if n == 0:
        return {"k": 0, "n": 0, "p": None, "lo": None, "hi": None}
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return {"k": k, "n": n, "p": round(p, 4), "lo": round(max(0.0, centre - half), 4), "hi": round(min(1.0, centre + half), 4)}


def prf(tp: int, fp: int, fn: int) -> dict:
    p = wilson(tp, tp + fp)
    r = wilson(tp, tp + fn)
    f1 = None
    if p["p"] is not None and r["p"] is not None and (p["p"] + r["p"]) > 0:
        f1 = round(2 * p["p"] * r["p"] / (p["p"] + r["p"]), 4)
    return {"precision": p, "recall": r, "f1": f1, "tp": tp, "fp": fp, "fn": fn}


def per_class_prf(gold: list[Hashable], pred: list[Hashable], labels: Iterable[Hashable]) -> dict:
    out = {}
    f1s = []
    for c in labels:
        tp = sum(1 for g, p in zip(gold, pred) if g == c and p == c)
        fp = sum(1 for g, p in zip(gold, pred) if g != c and p == c)
        fn = sum(1 for g, p in zip(gold, pred) if g == c and p != c)
        out[str(c)] = prf(tp, fp, fn)
        if out[str(c)]["f1"] is not None:
            f1s.append(out[str(c)]["f1"])
    out["macro_f1"] = round(sum(f1s) / len(f1s), 4) if f1s else None
    out["accuracy"] = wilson(sum(1 for g, p in zip(gold, pred) if g == p), len(gold))
    return out


def cohen_kappa(a: list[Hashable], b: list[Hashable]) -> Optional[float]:
    n = len(a)
    if n == 0:
        return None
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb.get(k, 0) for k in ca) / (n * n)
    if pe == 1:
        return 1.0
    return round((po - pe) / (1 - pe), 4)


def krippendorff_alpha_nominal(units: list[list[Hashable]]) -> Optional[float]:
    """units：每个条目的全部评分（可 ≥2 个评分者，缺失值不放入）。名义尺度。"""
    pairs_units = [u for u in units if len(u) >= 2]
    if not pairs_units:
        return None
    coincidence: Counter = Counter()
    for u in pairs_units:
        m = len(u)
        for i, x in enumerate(u):
            for j, y in enumerate(u):
                if i != j:
                    coincidence[(x, y)] += 1 / (m - 1)
    n_c: Counter = Counter()
    for (x, _), v in coincidence.items():
        n_c[x] += v
    n = sum(n_c.values())
    do = sum(v for (x, y), v in coincidence.items() if x != y) / n
    de = sum(n_c[x] * n_c[y] for x in n_c for y in n_c if x != y) / (n * (n - 1))
    if de == 0:
        return 1.0
    return round(1 - do / de, 4)


def bcubed(pred_cluster_of: dict[str, frozenset], gold_cluster_of: dict[str, frozenset], items: Iterable[str]) -> dict:
    """B-cubed precision/recall/F1，在给定条目（如抽样锚点）上平均。

    pred_cluster_of[x] / gold_cluster_of[x]：包含 x 自身的簇成员集合。
    """
    ps, rs = [], []
    for x in items:
        pc, gc = pred_cluster_of[x], gold_cluster_of[x]
        inter = len(pc & gc)
        ps.append(inter / len(pc))
        rs.append(inter / len(gc))
    if not ps:
        return {"precision": None, "recall": None, "f1": None, "n": 0}
    p, r = sum(ps) / len(ps), sum(rs) / len(rs)
    return {
        "precision": round(p, 4),
        "recall": round(r, 4),
        "f1": round(2 * p * r / (p + r), 4) if p + r else 0.0,
        "n": len(ps),
        "items_imperfect_precision": sum(1 for v in ps if v < 1),
        "items_imperfect_recall": sum(1 for v in rs if v < 1),
    }
