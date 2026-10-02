"""Stage 6 · grants 补全：语义补全（流水线模型）+ 数据驱动观测交叉校验 + 覆盖修复。

输入（只读）：data/knowledge_points.json（含 first_introduced_lesson_id 与 Stage 1 草稿 grants）、data/exercises.json、data/lessons.json。
输出（work/stage6/）：
  grants_enriched.json  {"grants": {kp_id: CapabilityGrant}, "lesson_residual": {课时: CapabilityGrant}, "meta": {...}}
  crosscheck.json       语义补全与数据观测的交叉校验统计
  repairs.json          覆盖修复记录（哪个实例、哪个维度、归到哪个知识点/课时）
  data/judgments/stage6_grants.jsonl   每个知识点的语义补全 Judgment 记录

流程：
  1. 语义补全：qwen3.7-plus（非思考，D13）看知识点描述与引入课时的习题原文，在受控词表内填写各维度「新增能力」。
  2. 交叉校验：以引入课时的习题实例为证据，统计语义补全未覆盖/明显偏宽的比例（诊断，不改写语义结果）。
  3. 覆盖修复：沿课时顺序折叠，实例所需能力不在边界内时，把缺口补进「该课时引入的知识点」的 grants
     （优先实例的主/次知识点，其次该课时引入的第一个知识点；该课时没有引入任何知识点则记入 lesson_residual）。
     概念维度的缺口（知识点引入晚于使用它的实例）是引入位置问题，属于 Stage 5，只记录不修复。
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from curriculum.annotate.client import AnnotationClient, AnnotationRequest
from curriculum.annotate.gold import ModelConfig, judgment_record
from curriculum.boundary.check import BoundaryStore, check_item
from curriculum.boundary.fold import Accumulator, fold_boundaries, lesson_key, order_lessons
from curriculum.boundary.mine import instance_features
from curriculum.boundary.vocab import (
    ANY_FRACTION,
    FRACTION_TYPES,
    GEOMETRY_GENERIC,
    GEOMETRY_SEED,
    OP_TAGS,
    ORDERED_TAG_SUPPORT,
    OPS,
    UNITS,
    norm_term,
    norm_unit,
)
from curriculum.common import DATA_DIR, ROOT, read_json, write_json, write_jsonl
from curriculum.models import CapabilityGrant, ExerciseInstance, ItemFeatures

WORK = ROOT / "work" / "stage6"
GRANTS_PATH = WORK / "grants_enriched.json"
PIPELINE = ModelConfig("qwen3.7-plus", False, max_tokens=1500)
MAX_EXAMPLES = 4
TIME_BUDGET_NOTE = "单次请求超时 60s，最多重试 5 次"

SYSTEM = f"""你是小学数学教材分析专家。任务：为教材中的一个知识点填写「学会它之后学生**新获得**的能力增量」（grants），用于判断后续命题是否超纲。
只填写该知识点**本身新引入或新扩展**的能力；它所依赖的前置知识、以及后面年级才学的内容一律不要写。每个维度没有增量就留空（null / [] / {{}}）。

输出一个 JSON 对象，字段如下（取值必须来自下面的受控词表）：
- "integer_domain_max"：整数或 null。该知识点使学生能读写/比较/计算的整数上限（即「N 以内」的 N）。规则：5 以内→5，10 以内→10，20 以内→20，100 以内→100，1000 以内→1000，万以内→10000，亿以内→100000000，万亿以内→1000000000000。只有当该知识点新引入或扩大了整数范围（认数、读写、加减乘除的数域）时才填；计算类知识点要把运算结果也算进数域（如两位数乘两位数的积可达四位→10000，三位数加三位数→1000）；图形、统计、小数分数等与整数范围无关的写 null。
- "decimal_max_places"：整数或 null。该知识点使学生能处理的小数位数上限（一位小数→1，两位小数→2，三位小数→3）。不涉及小数的写 null。
- "fraction_types"：从 {json.dumps(FRACTION_TYPES, ensure_ascii=False)} 中选，该知识点新引入的分数类型（几分之一=分子为1；几分之几=分子大于1的真分数；真分数/假分数/带分数是学到这些名称与概念时才选）。
- "operation_operand_forms"：对象，键为 {json.dumps(OPS, ensure_ascii=False)}，值为该知识点**新教会计算**的操作数形态标签，只能从下表选：
{chr(10).join(f'    {op}：{json.dumps(tags, ensure_ascii=False)}' for op, tags in OP_TAGS.items())}
  标签含义：加/减法「整数」=整数加减（数域由 integer_domain_max 管，不在这里体现）；「进位」「退位」=需要进位/退位的整数加减；「小数」=小数加减；「小数·位数不同」=小数位数不同的加减；「分数·同分母」「分数·异分母」「分数·带分数」=分数加减；
  乘法「整数·表内」=一位数乘一位数（乘法口诀）；「整数·乘数一位数」=多位数乘一位数；「整数·乘数两位数」=乘数是两位数；「整数·乘数三位数及以上」；「小数·乘整数」「小数·乘小数」「分数·乘整数」「分数·乘分数」；
  除法「整数·表内」=表内除法（被除数≤81，除数一位数）；「整数·除数一位数/两位数/三位数及以上」=按除数位数；「整数·有余数」=有余数除法；「整数·商为小数」=整数除以整数商是小数；「小数·除以整数」「小数·除数是小数」「分数·除以整数」「分数·除数是分数」。
  一个知识点只教了其中一部分就只选那一部分；不教运算的知识点留 {{}}。
- "concepts"：数组，0～5 个**短名词短语**（≤10 个字），该知识点新引入的具体概念、术语、方法（如"序数"、"计数单位"、"进率"、"最简分数"）。知识点自己的名称会被自动加入，不要重复它；不要写句子。
- "units_of_measure"：数组，只能从 {json.dumps(UNITS, ensure_ascii=False)} 中选，该知识点**正式教学/认识**的计量单位（顺口提到不算）。年、月、日、周、个、只等日常单位不要写。
- "geometry_vocab"：数组，该知识点新引入的几何词汇。优先从下列种子词中选：{' '.join(GEOMETRY_SEED)}；必须新增时才写词表外的规范术语（≥2 字）。
- "confidence"：0～1 的数，对整体填写的把握。
- "reason"：不超过 60 字，说明主要依据。

只输出 JSON，不要多余文字。"""

USER_TMPL = """【知识点】{name}
别名：{aliases}
领域/主线：{domain} / {thread}
引入位置：{lesson_id}（{grade_label}）课时标题：{lesson_title}
描述：{description}
教材中该知识点引入课时的习题原文（最多 {n_ex} 条，供判断数域、小数位数、运算形态）：
{examples}
Stage 1 初稿（仅供参考，可能残缺或不规范）：{draft}"""


# ------------------------------------------------------------------ 数据加载


def load_inputs() -> dict[str, Any]:
    kps = read_json(DATA_DIR / "knowledge_points.json")
    ex = read_json(DATA_DIR / "exercises.json")
    lessons = read_json(DATA_DIR / "lessons.json")
    order = order_lessons(l["id"] for l in lessons)
    return {"kps": kps, "ex": ex, "lessons": {l["id"]: l for l in lessons}, "order": order}


def grade_label(lesson_id: str) -> str:
    g, sem = lesson_id[1], {"a": "上册", "b": "下册"}[lesson_id[2]]
    return f"{g}年级{sem}"


def _example_texts(kp: dict, ex_by_lesson: dict[str, list[dict]], ex_by_kp: dict[str, list[dict]]) -> list[str]:
    lid = kp["first_introduced_lesson_id"]
    pool = [e for e in ex_by_lesson.get(lid, []) if e["primary_knowledge_point_id"] == kp["id"]]
    if len(pool) < MAX_EXAMPLES:
        pool += [e for e in ex_by_lesson.get(lid, []) if kp["id"] in e["secondary_knowledge_point_ids"] and e not in pool]
    if not pool:
        pool = sorted(ex_by_kp.get(kp["id"], []), key=lambda e: lesson_key(e["lesson_id"]))
    return [e["text"].replace("\n", " ")[:170] for e in pool[:MAX_EXAMPLES]]


def build_prompts(inp: dict) -> list[tuple[str, str]]:
    ex_by_lesson, ex_by_kp = defaultdict(list), defaultdict(list)
    for e in inp["ex"]:
        ex_by_lesson[e["lesson_id"]].append(e)
        ex_by_kp[e["primary_knowledge_point_id"]].append(e)
    out = []
    for kp in inp["kps"]:
        lid = kp["first_introduced_lesson_id"]
        texts = _example_texts(kp, ex_by_lesson, ex_by_kp)
        draft = {k: v for k, v in (kp.get("grants") or {}).items() if v}
        msg = USER_TMPL.format(
            name=kp["name"], aliases="、".join(kp.get("aliases") or []) or "无", domain=kp["domain"], thread=kp["thread"],
            lesson_id=lid, grade_label=grade_label(lid), lesson_title=(inp["lessons"].get(lid) or {}).get("title", ""),
            description=kp["description"], n_ex=len(texts),
            examples="\n".join(f"  {i + 1}. {t}" for i, t in enumerate(texts)) or "  （无）",
            draft=json.dumps(draft, ensure_ascii=False)[:500] if draft else "无",
        )
        out.append((kp["id"], msg))
    return out


# ------------------------------------------------------------------ 语义补全


def _validate(d: Any) -> bool:
    return isinstance(d, dict) and isinstance(d.get("operation_operand_forms", {}) or {}, dict)


def sanitize(parsed: dict, kp_text: str = "") -> tuple[CapabilityGrant, dict]:
    """把模型输出收敛到受控词表内；返回 (grant, 被丢弃的内容)。"""
    dropped: dict[str, Any] = defaultdict(list)
    g = CapabilityGrant()
    v = parsed.get("integer_domain_max")
    try:
        v = int(v) if v not in (None, "", 0) else None
    except (TypeError, ValueError):
        dropped["integer_domain_max"] = v
        v = None
    if v is not None and not (1 <= v <= 10**15):
        dropped["integer_domain_max"] = v
        v = None
    g.integer_domain_max = v
    p = parsed.get("decimal_max_places")
    try:
        p = int(p) if p not in (None, "") else None
    except (TypeError, ValueError):
        dropped["decimal_max_places"] = p
        p = None
    g.decimal_max_places = p if p and 1 <= p <= 6 else None
    for t in parsed.get("fraction_types") or []:
        (g.fraction_types.add(t) if t in FRACTION_TYPES else dropped["fraction_types"].append(t))
    for op, tags in (parsed.get("operation_operand_forms") or {}).items():
        if op not in OP_TAGS:
            dropped["operation_operand_forms"].append(op)
            continue
        for t in tags or []:
            if t in OP_TAGS[op] and (op, t) in ORDERED_TAG_SUPPORT and not any(w in kp_text for w in ORDERED_TAG_SUPPORT[(op, t)]):
                dropped["operation_operand_forms"].append(f"{op}:{t}（名称/描述中无关键词支撑）")
            elif t in OP_TAGS[op]:
                g.operation_operand_forms.setdefault(op, set()).add(t)
            else:
                dropped["operation_operand_forms"].append(f"{op}:{t}")
    for c in (parsed.get("concepts") or [])[:6]:
        c = str(c).strip()
        if 2 <= len(c) <= 14 and norm_term(c):
            g.concepts.add(c)
        else:
            dropped["concepts"].append(c)
    for u in parsed.get("units_of_measure") or []:
        n = norm_unit(str(u))
        (g.units_of_measure.add(n) if n else dropped["units_of_measure"].append(u))
    seed = {norm_term(s) for s in GEOMETRY_SEED}
    for t in parsed.get("geometry_vocab") or []:
        t = str(t).strip()
        if t in GEOMETRY_GENERIC:
            dropped["geometry_vocab"].append(t)
        elif norm_term(t) in seed or 2 <= len(t) <= 8:
            g.geometry_vocab.add(t)
        else:
            dropped["geometry_vocab"].append(t)
    return g, dict(dropped)


def semantic_grants(inp: dict, client: AnnotationClient) -> tuple[dict[str, CapabilityGrant], list[dict], dict]:
    prompts = build_prompts(inp)
    reqs = [
        AnnotationRequest(
            request_id=f"sem:{kid}", model=PIPELINE.model, thinking=PIPELINE.thinking,
            messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": msg}],
            response_schema_validator=_validate, max_tokens=PIPELINE.max_tokens,
        )
        for kid, msg in prompts
    ]
    results = client.run_batch(reqs, label="stage6-semantic")
    grants: dict[str, CapabilityGrant] = {}
    judgments, stats = [], Counter()
    kp_by_id = {k["id"]: k for k in inp["kps"]}
    for (kid, msg), res in zip(prompts, results):
        if not res.ok:
            stats["failed"] += 1
            judgments.append(judgment_record("capability_grants", kid, "pipeline", res, msg, "ERROR"))
            continue
        kp = kp_by_id[kid]
        g, dropped = sanitize(res.parsed, kp["name"] + kp["description"])
        grants[kid] = g
        if dropped:
            stats["with_dropped"] += 1
        rec = judgment_record("capability_grants", kid, "pipeline", res, msg,
                              json.dumps({**g.model_dump(mode="json"), "dropped": dropped}, ensure_ascii=False, default=sorted))
        judgments.append(rec)
    stats["cost_cny"] = str(sum((Decimal(j["cost_cny"]) for j in judgments), Decimal("0")))
    return grants, judgments, dict(stats)


# ------------------------------------------------------------------ 概念：知识点名称与别名自动成为概念


def effective_grant(kp: dict, g: CapabilityGrant) -> CapabilityGrant:
    out = g.model_copy(deep=True)
    out.concepts |= {kp["name"], *[a for a in kp.get("aliases", []) if a and len(a) <= 20]}
    return out


# ------------------------------------------------------------------ 覆盖检查与修复


class _AccStore(BoundaryStore):
    """把 Accumulator 的当前状态包装成 BoundaryStore，复用 check_item，不落盘。"""

    def __init__(self, acc: Accumulator, lesson_id: str, order: list[str], intro: dict):
        self._snap = acc.snapshot(lesson_id)
        super().__init__({"lessons": order, "boundaries": {lesson_id: {}}, "introduced_at": intro, "kp_intro": []})
        self._cache[lesson_id] = self._snap


def _need_to_grant(features: ItemFeatures, report) -> tuple[CapabilityGrant, list[dict]]:
    """把校验报告里的缺口转成 grant（概念缺口不转换，属于引入位置问题）。"""
    from curriculum.boundary.vocab import analyze_operation

    g = CapabilityGrant()
    notes = []
    for v in report.violations:
        if v.dimension == "integer_domain":
            need = int(v.item_value)
            g.integer_domain_max = 10 ** len(str(need)) - 1
            notes.append({"dimension": "integer_domain_max", "value": g.integer_domain_max})
        elif v.dimension == "decimal_places":
            g.decimal_max_places = int(v.item_value)
            notes.append({"dimension": "decimal_max_places", "value": g.decimal_max_places})
        elif v.dimension == "fraction_types":
            t = "几分之一" if v.item_value == ANY_FRACTION else v.item_value
            g.fraction_types.add(t)
            notes.append({"dimension": "fraction_types", "value": t})
        elif v.dimension == "units_of_measure":
            g.units_of_measure.add(v.item_value)
            notes.append({"dimension": "units_of_measure", "value": v.item_value})
        elif v.dimension == "geometry_vocab":
            g.geometry_vocab.add(v.item_value)
            notes.append({"dimension": "geometry_vocab", "value": v.item_value})
        elif v.dimension == "operation_forms":
            if v.item_value == "进位/退位":
                g.operation_operand_forms.setdefault("加法", set()).add("进位")
                notes.append({"dimension": "operation_forms", "value": "加法:进位"})
            else:
                op, tags = v.item_value.split("：", 1)
                g.operation_operand_forms.setdefault(op, set()).update(tags.split("、"))
                notes.append({"dimension": "operation_forms", "value": v.item_value})
    return g, notes


def _merge_grant(a: CapabilityGrant, b: CapabilityGrant) -> CapabilityGrant:
    out = a.model_copy(deep=True)
    for d in ("integer_domain_max", "decimal_max_places"):
        x, y = getattr(out, d), getattr(b, d)
        if y is not None and (x is None or y > x):
            setattr(out, d, y)
    for d in ("fraction_types", "concepts", "units_of_measure", "geometry_vocab"):
        getattr(out, d).update(getattr(b, d))
    for op, forms in b.operation_operand_forms.items():
        out.operation_operand_forms.setdefault(op, set()).update(forms)
    return out


def coverage_pass(inp: dict, kp_grants: dict[str, CapabilityGrant], repair: bool,
                  residual_in: Optional[dict[str, CapabilityGrant]] = None, geo_vocab: set[str] = frozenset()):
    """沿课时顺序折叠并逐实例检查。repair=True 时把缺口补进该课时引入的知识点（或课时残差）。

    返回 (stats, repairs, kp_grants_out, residual_out)。
    """
    kps = {k["id"]: k for k in inp["kps"]}
    kp_names = {k["id"]: k["name"] for k in inp["kps"]}
    order = inp["order"]
    rank = {l: i for i, l in enumerate(order)}
    by_lesson_kps: dict[str, list[str]] = defaultdict(list)
    for k in inp["kps"]:
        by_lesson_kps[k["first_introduced_lesson_id"]].append(k["id"])
    ex_by_lesson: dict[str, list[dict]] = defaultdict(list)
    for e in inp["ex"]:
        ex_by_lesson[e["lesson_id"]].append(e)

    grants = {k: g.model_copy(deep=True) for k, g in kp_grants.items()}
    residual = {k: g.model_copy(deep=True) for k, g in (residual_in or {}).items()}
    acc = Accumulator()
    intro_log: dict = {"integer_domain": [], "decimal_places": [], "fraction_types": {}, "operation_forms": {},
                       "concepts": {}, "units_of_measure": {}, "geometry_vocab": {t: "-" for t in geo_vocab}}  # 已知词表（不关心引入课时）
    repairs: list[dict] = []
    stats = Counter()
    by_dim = Counter()
    upstream: list[dict] = []
    violating_dims = Counter()
    for lid in order:
        for kid in by_lesson_kps.get(lid, []):
            acc.merge(effective_grant(kps[kid], grants.get(kid, CapabilityGrant())))
        if lid in residual:
            acc.merge(residual[lid])
        # 实例
        store = _AccStore(acc, lid, order, intro_log)
        for e in ex_by_lesson.get(lid, []):
            f = instance_features(e, None, geo_vocab)
            rep = check_item(f, lid, store)
            stats["instances"] += 1
            non_concept = [v for v in rep.violations if v.dimension != "concepts"]
            kp_intro = kps[e["primary_knowledge_point_id"]]["first_introduced_lesson_id"]
            concept_v = [1] if rank[kp_intro] > rank[lid] else []
            if concept_v:
                upstream.append({"instance_id": e["id"], "lesson_id": lid, "kp": e["primary_knowledge_point_id"],
                                 "kp_intro": kp_intro})
            if not non_concept:
                stats["covered"] += 1
                if not concept_v:
                    stats["fully_covered"] += 1
                continue
            for v in non_concept:
                violating_dims[v.dimension] += 1
            if not repair:
                continue
            gap, notes = _need_to_grant(f, rep)
            cands = [k for k in [e["primary_knowledge_point_id"], *e["secondary_knowledge_point_ids"]] if k in kps and kps[k]["first_introduced_lesson_id"] == lid]
            cands += [k for k in by_lesson_kps.get(lid, []) if k not in cands]
            if cands:
                target = cands[0]
                grants[target] = _merge_grant(grants.get(target, CapabilityGrant()), gap)
                tkind = f"kp:{target}"
            else:
                target = None
                residual[lid] = _merge_grant(residual.get(lid, CapabilityGrant()), gap)
                tkind = f"lesson:{lid}"
            acc.merge(gap)
            store = _AccStore(acc, lid, order, intro_log)
            for n in notes:
                by_dim[n["dimension"]] += 1
            repairs.append({"instance_id": e["id"], "lesson_id": lid, "target": tkind, "gaps": notes,
                            "features": f.model_dump(mode="json", exclude_defaults=True), "text": e["text"][:120]})
    stats["violating_dims"] = dict(violating_dims)
    stats["repairs"] = len(repairs)
    stats["repair_by_dim"] = dict(by_dim)
    stats["concept_order_violations"] = len(upstream)
    return dict(stats), repairs, grants, residual, upstream


# ------------------------------------------------------------------ 交叉校验：语义补全 vs 数据观测


def crosscheck(inp: dict, sem: dict[str, CapabilityGrant]) -> dict:
    """以 grants 的非空覆盖率与观测对照（诊断用）。"""
    kps = inp["kps"]
    ex_by_kp_intro = defaultdict(list)
    for e in inp["ex"]:
        ex_by_kp_intro[(e["primary_knowledge_point_id"], e["lesson_id"])].append(e)
    rows = Counter()
    over_wide = []
    for k in kps:
        g = sem.get(k["id"])
        if g is None:
            continue
        obs = [e for e in ex_by_kp_intro.get((k["id"], k["first_introduced_lesson_id"]), [])]
        digits = [e["operand_features"].get("integer_digits") for e in obs if e["operand_features"].get("integer_digits")]
        dec = [e["operand_features"].get("decimal_places") for e in obs if e["operand_features"].get("decimal_places")]
        nts = {t for e in obs for t in e["operand_features"].get("number_types", [])}
        if digits:
            rows["obs_integer"] += 1
            if g.integer_domain_max is None:
                rows["integer_missing_in_sem"] += 1
            elif g.integer_domain_max < 10 ** (max(digits) - 1):
                rows["integer_sem_below_obs"] += 1
            elif len(str(g.integer_domain_max)) > max(digits) + 1:
                rows["integer_sem_above_obs_by_2_digits"] += 1
                over_wide.append({"kp": k["id"], "sem": g.integer_domain_max, "obs_digits": max(digits)})
        if dec:
            rows["obs_decimal"] += 1
            if not g.decimal_max_places:
                rows["decimal_missing_in_sem"] += 1
            elif g.decimal_max_places < max(dec):
                rows["decimal_sem_below_obs"] += 1
        if "分数" in nts:
            rows["obs_fraction"] += 1
            if not g.fraction_types:
                rows["fraction_missing_in_sem"] += 1
    return {"counts": dict(rows), "integer_over_wide_examples": over_wide[:15]}


def coverage_by_dim(kps: list[dict], grants: dict[str, CapabilityGrant]) -> dict:
    n = len(kps)
    dims = {"integer_domain_max": 0, "decimal_max_places": 0, "fraction_types": 0, "operation_operand_forms": 0,
            "concepts": 0, "units_of_measure": 0, "geometry_vocab": 0}
    for k in kps:
        g = grants.get(k["id"]) if isinstance(grants.get(k["id"]), CapabilityGrant) else CapabilityGrant(**(k.get("grants") or {}))
        for d in dims:
            if getattr(g, d):
                dims[d] += 1
    return {"n_kps": n, **dims}


# ------------------------------------------------------------------ 入口


def run(client: Optional[AnnotationClient] = None, geo_extra: bool = True) -> dict:
    WORK.mkdir(parents=True, exist_ok=True)
    inp = load_inputs()
    client = client or AnnotationClient(max_workers=24, timeout=60.0)
    sem, judgments, sem_stats = semantic_grants(inp, client)
    write_jsonl(DATA_DIR / "judgments" / "stage6_grants.jsonl", judgments)
    geo_vocab = {t for g in sem.values() for t in g.geometry_vocab}

    draft = {k["id"]: CapabilityGrant(**(k.get("grants") or {})) for k in inp["kps"]}
    base_stats, *_ = coverage_pass(inp, draft, repair=False, geo_vocab=geo_vocab)
    pre_stats, *_ = coverage_pass(inp, sem, repair=False, geo_vocab=geo_vocab)
    stats, repairs, final, residual, upstream = coverage_pass(inp, sem, repair=True, geo_vocab=geo_vocab)
    post_stats, *_ = coverage_pass(inp, final, repair=False, residual_in=residual, geo_vocab=geo_vocab)
    cc = crosscheck(inp, sem)

    out = {"grants": {k: g.model_dump() for k, g in final.items()},
           "lesson_residual": {k: g.model_dump() for k, g in residual.items()},
           "meta": {"model": PIPELINE.tag, "n_kps": len(inp["kps"]), "n_sem": len(sem)}}
    write_json(GRANTS_PATH, out)
    write_json(WORK / "repairs.json", {"repairs": repairs, "concept_order_violations": upstream})
    report = {
        "semantic": sem_stats,
        "coverage_before": coverage_by_dim(inp["kps"], draft),
        "coverage_after_semantic": coverage_by_dim(inp["kps"], sem),
        "coverage_after_repair": coverage_by_dim(inp["kps"], final),
        "baseline_draft_instances": base_stats,
        "semantic_only_instances": pre_stats,
        "repaired_instances": stats,
        "after_repair_recheck": post_stats,
        "crosscheck": cc,
        "n_residual_lessons": len(residual),
    }
    write_json(WORK / "enrich_report.json", report)
    return report


def load_enriched() -> tuple[dict[str, CapabilityGrant], dict[str, CapabilityGrant]]:
    d = read_json(GRANTS_PATH)
    return ({k: CapabilityGrant(**v) for k, v in d["grants"].items()},
            {k: CapabilityGrant(**v) for k, v in d["lesson_residual"].items()})
