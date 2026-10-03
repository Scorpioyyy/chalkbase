"""Stage 6 · 单调折叠与产物生成。

`build()`：读最新的 data/（知识点引入位置、课时顺序）与 work/stage6/grants_enriched.json，折叠出 data/boundaries.json。
引入位置一律以 data/knowledge_points.json 的 first_introduced_lesson_id 为准，所以 Stage 5 改动之后重跑本命令即可。
`apply_to_knowledge_points()`：把补全后的 grants 写回 data/knowledge_points.json 的 `grants` 字段（Stage 5 完成后执行）。
"""
from __future__ import annotations

import json
from collections import defaultdict

from chalkbase.boundary.enrich import GRANTS_PATH, effective_grant, load_enriched
from chalkbase.boundary.fold import fold_boundaries, lesson_key, order_lessons
from chalkbase.common import DATA_DIR, read_json, write_json
from chalkbase.models import CapabilityGrant, KnowledgePoint

BOUNDARIES_PATH = DATA_DIR / "boundaries.json"


def collect_grants(kps: list[dict], lessons: list[str]) -> tuple[dict[str, list[CapabilityGrant]], dict]:
    """每个课时引入的 grants（含课时残差）。缺少补全结果的知识点回退到 Stage 1 草稿并计数。"""
    if GRANTS_PATH.exists():
        enriched, residual = load_enriched()
    else:
        enriched, residual = {}, {}
    grants_at: dict[str, list[CapabilityGrant]] = defaultdict(list)
    missing = []
    lesson_set = set(lessons)
    for kp in kps:
        lid = kp["first_introduced_lesson_id"]
        if lid not in lesson_set:
            raise ValueError(f"知识点 {kp['id']} 的引入课时 {lid} 不在课时表中")
        g = enriched.get(kp["id"])
        if g is None:
            missing.append(kp["id"])
            g = CapabilityGrant(**(kp.get("grants") or {}))
        grants_at[lid].append(effective_grant(kp, g))
    for lid, g in residual.items():
        if lid in lesson_set:
            grants_at[lid].append(g)
    return grants_at, {"missing_enrichment": missing, "n_residual": len(residual)}


def build() -> dict:
    kps = read_json(DATA_DIR / "knowledge_points.json")
    lessons = order_lessons(l["id"] for l in read_json(DATA_DIR / "lessons.json"))
    grants_at, info = collect_grants(kps, lessons)
    boundaries, intro = fold_boundaries(lessons, grants_at)
    by_lesson = defaultdict(list)
    for kp in kps:
        by_lesson[kp["first_introduced_lesson_id"]].append(kp["id"])
    kp_intro = [[lid, sorted(by_lesson[lid])] for lid in lessons if lid in by_lesson]
    # 为控制体积，集合型维度按课时存增量（相对前一课时），BoundaryStore 读取时逐课时累加还原；标量维度每课时存全值
    out = {"lessons": lessons, "format": "delta_v1", "boundaries": {}, "introduced_at": intro, "kp_intro": kp_intro}
    prev = None
    for lid in lessons:
        b = boundaries[lid]
        row = {"integer_domain_max": b.integer_domain_max, "decimal_max_places": b.decimal_max_places, "add": {}}
        for d in ("fraction_types", "concepts", "units_of_measure", "geometry_vocab"):
            new = set(getattr(b, d)) - (set(getattr(prev, d)) if prev else set())
            if new:
                row["add"][d] = sorted(new)
        ops = {}
        for op, forms in b.operation_operand_forms.items():
            new = set(forms) - (set(prev.operation_operand_forms.get(op, set())) if prev else set())
            if new:
                ops[op] = sorted(new)
        if ops:
            row["add"]["operation_operand_forms"] = ops
        out["boundaries"][lid] = row
        prev = b
    BOUNDARIES_PATH.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    from chalkbase.boundary.check import reload_default_store
    reload_default_store()
    last = boundaries[lessons[-1]]
    return {
        "n_lessons": len(lessons), "n_kps": len(kps), **{k: (v if k != "missing_enrichment" else len(v)) for k, v in info.items()},
        "final_boundary": {
            "integer_domain_max": last.integer_domain_max, "decimal_max_places": last.decimal_max_places,
            "fraction_types": sorted(last.fraction_types), "n_concepts": len(last.concepts),
            "units": sorted(last.units_of_measure), "n_geometry_vocab": len(last.geometry_vocab),
            "operation_forms": {k: len(v) for k, v in last.operation_operand_forms.items()},
        },
        "size_bytes": BOUNDARIES_PATH.stat().st_size,
    }


def apply_to_knowledge_points() -> dict:
    """把补全后的 grants 写回 data/knowledge_points.json（只改 `grants` 字段，其余原样）。"""
    path = DATA_DIR / "knowledge_points.json"
    kps = read_json(path)
    enriched, _ = load_enriched()
    changed = 0
    for kp in kps:
        g = enriched.get(kp["id"])
        if g is None:
            continue
        new = json.loads(g.model_dump_json(exclude_defaults=True))
        for k in ("fraction_types", "concepts", "units_of_measure", "geometry_vocab"):
            if k in new:
                new[k] = sorted(new[k])
        if "operation_operand_forms" in new:
            new["operation_operand_forms"] = {op: sorted(v) for op, v in sorted(new["operation_operand_forms"].items())}
        if new != kp.get("grants"):
            changed += 1
        kp["grants"] = new
        KnowledgePoint(**kp)  # schema 校验
    write_json(path, kps)
    return {"changed": changed, "n_kps": len(kps), "missing": sum(1 for kp in kps if kp["id"] not in enriched)}
