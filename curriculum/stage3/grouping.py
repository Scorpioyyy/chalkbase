"""Stage 3 · 实例规范化、签名分组（含回退）与参数包络（eval/specs/stage3.md §3）。"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Optional

from curriculum.common import DATA_DIR, book_of, load_work_books, local_key, read_json
from curriculum.models import ExerciseInstance


# ------------------------------------------------------------------ 实例规范化


def canonical_exercises() -> list[dict]:
    """把全部实例的知识点引用改写为规范 ID（主知识点若同时出现在次知识点中，从次知识点中去掉）。"""
    l2c = {local_key(r["book_id"], r["local_id"]): r["canonical_id"] for r in read_json(DATA_DIR / "kp_local_map.json")}
    out = []
    for bid, b in load_work_books().items():
        for e in b["exercises"]:
            row = {k: v for k, v in e.items() if k in ExerciseInstance.model_fields}
            row["primary_knowledge_point_id"] = l2c[local_key(bid, e["primary_knowledge_point_id"])]
            secs = []
            for s in e.get("secondary_knowledge_point_ids") or []:
                c = l2c[local_key(bid, s)]
                if c != row["primary_knowledge_point_id"] and c not in secs:
                    secs.append(c)
            row["secondary_knowledge_point_ids"] = secs
            ExerciseInstance(**row)
            out.append(row)
    return out


# ------------------------------------------------------------------ 签名


def _digits_bucket(d: Optional[int]) -> Optional[str]:
    if d is None:
        return None
    if d <= 1:
        return "≤1"
    if d <= 4:
        return str(d)
    return "5-8" if d <= 8 else "≥9"


def _places_bucket(p: Optional[int]) -> str:
    if not p:
        return "0"
    return str(p) if p <= 2 else "≥3"


def _steps_bucket(s: Optional[int]) -> Optional[str]:
    if s is None:
        return None
    return "≤1" if s <= 1 else ("2" if s == 2 else "≥3")


def operand_bucket(e: dict) -> tuple:
    of = e.get("operand_features") or {}
    return (
        tuple(sorted(of.get("number_types") or [])),
        _digits_bucket(of.get("integer_digits")),
        _places_bucket(of.get("decimal_places")),
        of.get("requires_carry_or_borrow"),
        of.get("requires_exact_division"),
        _steps_bucket(of.get("operation_steps")),
    )


def signature(e: dict, level: int) -> tuple:
    """level 2 = 完整签名；1 = 去掉操作数分桶；0 = (主知识点, 形式)。"""
    base = (e["primary_knowledge_point_id"], e["item_form"])
    if level == 0:
        return base
    sec = tuple(sorted(e["secondary_knowledge_point_ids"]))
    if level == 1:
        return base + (sec,)
    return base + (sec, operand_bucket(e))


def group_instances(exercises: list[dict], backoff: bool = True) -> list[dict]:
    """按签名分组。backoff=True 时对单实例组逐级回退到更粗签名（只在同一主知识点、同一形式内）。

    返回 [{signature, level, instance_ids}]，确定性排序。
    """
    remaining = {e["id"]: e for e in exercises}
    groups = []
    levels = (2, 1, 0) if backoff else (2,)
    for li, level in enumerate(levels):
        buckets: dict[tuple, list[str]] = defaultdict(list)
        for iid, e in remaining.items():
            buckets[signature(e, level)].append(iid)
        last = li == len(levels) - 1
        for sig, ids in buckets.items():
            if len(ids) >= 2 or last:
                groups.append({"signature": sig, "level": level, "instance_ids": sorted(ids)})
                for i in ids:
                    remaining.pop(i)
    if backoff:
        groups = _attach_singletons(groups, {e["id"]: e for e in exercises})
    groups.sort(key=lambda g: (g["signature"][0], g["signature"][1], -len(g["instance_ids"]), g["instance_ids"][0]))
    return groups


def _attach_singletons(groups: list[dict], ex: dict[str, dict]) -> list[dict]:
    """回退后仍为单实例、但同一 (主知识点, 形式) 下已有多实例题型的，并入签名最接近者。

    接近度 = 次知识点集合相同（+1）+ 操作数分桶各维度相同的个数；并列时取实例多的组。
    """
    by_base: dict[tuple, list[dict]] = defaultdict(list)
    for g in groups:
        by_base[g["signature"][:2]].append(g)
    kept = []
    for g in groups:
        if len(g["instance_ids"]) != 1:
            kept.append(g)
            continue
        siblings = [s for s in by_base[g["signature"][:2]] if s is not g and len(s["instance_ids"]) >= 2]
        if not siblings:
            kept.append(g)
            continue
        e = ex[g["instance_ids"][0]]
        sec, ob = tuple(sorted(e["secondary_knowledge_point_ids"])), operand_bucket(e)

        def closeness(s):
            score = 0.0
            for iid in s["instance_ids"]:
                o = ex[iid]
                score += (tuple(sorted(o["secondary_knowledge_point_ids"])) == sec) + sum(a == b for a, b in zip(operand_bucket(o), ob))
            return (score / len(s["instance_ids"]), len(s["instance_ids"]))

        best = max(siblings, key=closeness)
        best["instance_ids"] = sorted(best["instance_ids"] + g["instance_ids"])
        best["attached"] = best.get("attached", []) + g["instance_ids"]
    return kept


# ------------------------------------------------------------------ 参数包络


def envelope(instances: list[dict]) -> dict:
    """参数约束的观测包络：取该组实例观测值的并集 / 最小最大值。"""
    of = [e.get("operand_features") or {} for e in instances]

    def rng(key):
        vals = [o[key] for o in of if o.get(key) is not None]
        return [min(vals), max(vals)] if vals else None

    def vset(key):
        vals = {o.get(key) for o in of if o.get(key) is not None}
        return sorted(vals) if vals else None

    return {
        "number_types": sorted({t for o in of for t in (o.get("number_types") or [])}),
        "integer_digits": rng("integer_digits"),
        "decimal_places": rng("decimal_places"),
        "requires_carry_or_borrow": vset("requires_carry_or_borrow"),
        "requires_exact_division": vset("requires_exact_division"),
        "operation_steps": rng("operation_steps"),
        "answer_forms": dict(Counter(e["answer_form"] for e in instances)),
        "figure_types": sorted({e["figure_type"] for e in instances if e.get("figure_type")}),
        "requires_figure_ratio": round(sum(1 for e in instances if e.get("requires_figure")) / len(instances), 3),
        "grades": sorted({int(book_of(e["id"])[1]) for e in instances}),
        "n_instances": len(instances),
    }


def singleton_stats(groups: list[dict], exercises: list[dict]) -> dict:
    n = len(groups)
    single = sum(1 for g in groups if len(g["instance_ids"]) == 1)
    floor_groups = Counter(signature(e, 0) for e in exercises)
    floor_single = sum(1 for v in floor_groups.values() if v == 1)
    ratio = single / n if n else 0
    # 下限按「个数」定义：(主知识点, 形式) 组只有 1 个实例时，任何不跨知识点/形式合并的方法都必然留下这个单实例题型
    return {
        "n_archetypes": n,
        "n_singletons": single,
        "singleton_ratio": round(ratio, 4),
        "data_floor_singletons": floor_single,
        "data_floor_ratio": round(floor_single / n, 4) if n else 0,
        "excess_over_floor": round((single - floor_single) / n, 4) if n else 0,
        "compression": round(len(exercises) / n, 3) if n else None,
    }
