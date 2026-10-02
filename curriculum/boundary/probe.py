"""Stage 6 · 越界探针：构造 → 盲标 → 评测（eval/specs/stage6.md §4）。

  probe-build：构造者（qwen3.7-plus，流水线模型）在给定（课时, 维度）下各写一道「边界内」与「恰好在该维度越界」的题，
               并给出结构化特征（标准特征，链路 A 用）。产物 eval/annotation/boundary_probe/items.jsonl。
  probe-label：两个不同厂商模型（qwen3.8-flash + deepseek-v4.1-flash）独立盲标「是否超纲」，
               只看到年级/课时/已学知识点名称清单与题面，看不到边界数据、构造意图与特征；分歧由 qwen3.8-max（思考）仲裁。
               金标 eval/gold/{val,test}/boundary_probe.jsonl。
  probe-eval：链路 A（标准特征 → check_item，只测边界+校验函数）与链路 B（抽取器从题面抽特征 → check_item，端到端）的
              查准/查全（越界为正类，Wilson CI），按维度分报；基线 = 只校验整数数域与小数位数。
"""
from __future__ import annotations

import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from curriculum.annotate.client import AnnotationClient, AnnotationRequest
from curriculum.annotate.gold import LabelTask, ModelConfig, run_label_gold, save_gold
from curriculum.boundary.check import check_item, default_store
from curriculum.boundary.extract import FEATURE_SPEC, extract_features_batch, to_features
from curriculum.boundary.fold import lesson_key
from curriculum.common import DATA_DIR, EVAL_DIR, ROOT, read_json, read_jsonl, write_json, write_jsonl
from curriculum.metrics import wilson
from curriculum.models import ItemFeatures

ANN_DIR = EVAL_DIR / "annotation" / "boundary_probe"
TASK = "boundary_probe"
DIMS = ["integer_domain", "decimal_places", "fraction_types", "operation_forms", "concepts", "units_of_measure", "geometry_vocab"]
DIM_DESC = {
    "integer_domain": "整数的大小范围（题目里出现的最大整数，含需要算出的结果）",
    "decimal_places": "小数：是否涉及小数、小数的位数",
    "fraction_types": "分数：是否涉及分数、分数的类型（几分之一、几分之几、真分数、假分数、带分数）",
    "operation_forms": "运算的操作数形态（进位/退位、乘数/除数是几位数、有余数除法、小数运算、分数运算等）",
    "concepts": "解题必须掌握的概念或方法（某个尚未学习的知识点）",
    "units_of_measure": "计量单位（长度、质量、容量、面积、体积、角度、速度等单位）",
    "geometry_vocab": "几何词汇（图形名称、几何术语）",
}
LESSONS_PER_GRADE_PER_DIM = 3
CONSTRUCTOR = "qwen3.7-plus"
ANNOTATORS = (ModelConfig("qwen3.8-flash", False, 500), ModelConfig("deepseek-v4.1-flash", False, 500))
ARBITER = ModelConfig("qwen3.8-max", True, 4000)


# ------------------------------------------------------------------ 上下文

def _kp_by_lesson() -> dict[str, list[dict]]:
    out = defaultdict(list)
    for k in read_json(DATA_DIR / "knowledge_points.json"):
        out[k["first_introduced_lesson_id"]].append(k)
    return out


def learned_names(lesson_id: str, order: list[str], by_lesson: dict) -> list[str]:
    names = []
    for lid in order:
        names += [k["name"] for k in by_lesson.get(lid, [])]
        if lid == lesson_id:
            break
    return names


def unlearned_next(lesson_id: str, order: list[str], by_lesson: dict, n: int = 15) -> list[str]:
    out, seen = [], False
    for lid in order:
        if seen:
            out += [k["name"] for k in by_lesson.get(lid, [])]
            if len(out) >= n:
                break
        if lid == lesson_id:
            seen = True
    return out[:n]


def lesson_header(lesson_id: str, titles: dict[str, str]) -> str:
    g, sem = lesson_id[1], {"a": "上册", "b": "下册"}[lesson_id[2]]
    return f"{g}年级{sem}，{lesson_id}「{titles.get(lesson_id, '')}」"


def split_of(lesson_id: str) -> str:
    return "val" if int(hashlib.md5(lesson_id.encode()).hexdigest(), 16) % 2 == 0 else "test"


# ------------------------------------------------------------------ 构造

CONSTRUCT_SYSTEM = f"""你是小学数学命题专家。给定一个课时（学生刚学完该课时）、该课时及之前已学的全部知识点名称、以及接下来尚未学习的知识点名称（供你找「超纲」素材），
请就指定的**一个能力维度**写两道题：
- "in"：边界内的题。题目所需的一切（数的范围、运算形态、单位、几何词汇、概念）都在已学范围内，且在该维度上贴近已学内容的上限（不要太平凡）；
- "out"：在**该维度上明显超出**已学范围、其余维度都在已学范围内的题。越界要清晰（取自上面「尚未学习」的内容，或高出一个明确层级），不要擦边。
如果该课时在这个维度上无法写出清晰的越界题（比如已经学完所有相关内容），"out" 写 null；如果写不出边界内的题，"in" 写 null。
题目要像教材习题一样自然、完整、单一（一道题，不要多问）。每道题再给出结构化特征，规则如下：
{FEATURE_SPEC}

输出 JSON：{{"in": {{"problem": "...", "features": {{...}}}} | null, "out": {{"problem": "...", "features": {{...}}, "why_out": "一句话说明超出了什么"}} | null}}。只输出 JSON。"""


def _construct_user(lesson_id: str, dim: str, learned: list[str], nxt: list[str], titles: dict, menu: list[str]) -> str:
    return (f"【课时】{lesson_header(lesson_id, titles)}\n【能力维度】{dim}：{DIM_DESC[dim]}\n"
            f"【已学知识点名称】{'；'.join(learned)}\n【尚未学习的知识点（接下来的若干个）】{'；'.join(nxt)}\n"
            f"【概念候选菜单（features.concepts 只能从这里原样选取）】{'；'.join(menu)}")


def probe_build() -> None:
    ANN_DIR.mkdir(parents=True, exist_ok=True)
    st = default_store()
    order = st.lessons
    by_lesson = _kp_by_lesson()
    lessons = read_json(DATA_DIR / "lessons.json")
    titles = {l["id"]: l["title"] for l in lessons}
    menu = [k["name"] for kps in by_lesson.values() for k in kps]
    rng = random.Random(20261003)
    # 候选课时：该课时有引入的知识点（上下文有意义），且之前已有至少 3 个知识点（排除开学导入）
    cand = defaultdict(list)
    seen = 0
    for lid in order:
        seen += len(by_lesson.get(lid, []))
        if by_lesson.get(lid) and seen >= 3 and lid[:3] != "g6b":
            cand[int(lid[1])].append(lid)
    jobs = []
    for dim in DIMS:
        for grade in range(1, 7):
            pool = [l for l in cand[grade]]
            for lid in rng.sample(pool, min(LESSONS_PER_GRADE_PER_DIM, len(pool))):
                jobs.append((dim, lid))
    client = AnnotationClient(max_workers=24)
    reqs = []
    for dim, lid in jobs:
        user = _construct_user(lid, dim, learned_names(lid, order, by_lesson), unlearned_next(lid, order, by_lesson), titles, menu)
        reqs.append(AnnotationRequest(request_id=f"con:{dim}:{lid}", model=CONSTRUCTOR, thinking=False,
                                      messages=[{"role": "system", "content": CONSTRUCT_SYSTEM}, {"role": "user", "content": user}],
                                      response_schema_validator=lambda d: isinstance(d, dict) and ("in" in d or "out" in d), max_tokens=2000,
                                      temperature=0.7))
    res = client.run_batch(reqs, label="probe-construct")
    items, judgments = [], []
    for (dim, lid), r in zip(jobs, res):
        if not r.ok:
            continue
        for intent in ("in", "out"):
            x = r.parsed.get(intent)
            if not isinstance(x, dict) or not x.get("problem"):
                continue
            items.append({"id": f"bp.{dim}.{lid}.{intent}", "lesson_id": lid, "dimension": dim, "intended": intent,
                          "problem": x["problem"], "features": x.get("features") or {}, "why_out": x.get("why_out"),
                          "split": split_of(lid), "stratum": f"{dim}/{intent}"})
    write_jsonl(ANN_DIR / "items.jsonl", items)
    print(json.dumps({"n_jobs": len(jobs), "n_items": len(items), "by_stratum": Counter(i["stratum"] for i in items),
                      "cost_cny": str(sum(r.cost_cny for r in res))}, ensure_ascii=False, default=str))


# ------------------------------------------------------------------ 盲标

def _render(item: dict) -> str:
    st = default_store()
    by_lesson = _kp_by_lesson()
    titles = {l["id"]: l["title"] for l in read_json(DATA_DIR / "lessons.json")}
    names = learned_names(item["lesson_id"], st.lessons, by_lesson)
    return f"【课时】{lesson_header(item['lesson_id'], titles)}\n【已学知识点名称】{'；'.join(names)}\n\n【题目】\n{item['problem']}"


def probe_label() -> None:
    items = read_jsonl(ANN_DIR / "items.jsonl")
    # 盲标看不到 features / intended：render 只用 lesson_id 与 problem
    task = LabelTask(name=TASK, render=_render,
                     validate=lambda d: isinstance(d, dict) and d.get("label") in ("in", "out"),
                     extract=lambda d: d["label"], guideline_path=ANN_DIR / "guideline.md")
    client = AnnotationClient(max_workers=24)
    res = run_label_gold(client, task, items, ANNOTATORS, ARBITER, judgment_task_type="boundary_probe")
    save_gold(TASK, res)
    print(json.dumps(res.stats, ensure_ascii=False, indent=1))


# ------------------------------------------------------------------ 评测

ADJACENT_LESSONS = 2  # 引入位置的容差：知识点引入课时在教材里有 ±1～2 课时的模糊（同一单元内相邻课时讲的常是同一类内容）


def _pred_out(features: ItemFeatures, lesson_id: str, only: tuple[str, ...] | None = None) -> tuple[bool, list[str]]:
    rep = check_item(features, lesson_id)
    dims = [v.dimension for v in rep.violations if only is None or v.dimension in only]
    return bool(dims), dims


def _adjacent_only(features: ItemFeatures, lesson_id: str) -> bool:
    """所有违例的能力都是在接下来 ADJACENT_LESSONS 个课时内才引入的（边界模糊带）。"""
    st = default_store()
    rep = check_item(features, lesson_id)
    if not rep.violations:
        return False
    r0 = st.rank(lesson_id)
    return all(v.introduced_at and 0 < st.rank(v.introduced_at) - r0 <= ADJACENT_LESSONS for v in rep.violations)


def _prf(rows: list[tuple[bool, bool]]) -> dict:
    tp = sum(1 for g, p in rows if g and p)
    fp = sum(1 for g, p in rows if not g and p)
    fn = sum(1 for g, p in rows if g and not p)
    P = wilson(tp, tp + fp) if tp + fp else None
    R = wilson(tp, tp + fn) if tp + fn else None
    f1 = round(2 * tp / (2 * tp + fp + fn), 4) if tp + fp + fn else None
    return {"precision": P, "recall": R, "f1": f1, "tp": tp, "fp": fp, "fn": fn, "tn": sum(1 for g, p in rows if not g and not p), "n": len(rows)}


def evaluate(split: str = "val", with_extraction: bool = True) -> dict:
    gold = [g for g in read_jsonl(EVAL_DIR / "gold" / split / f"{TASK}.jsonl") if g.get("label") in ("in", "out") and g.get("source") in ("consensus", "arbitrated")]
    queue = sum(1 for g in read_jsonl(EVAL_DIR / "gold" / split / f"{TASK}.jsonl") if g.get("source") in ("human_queue", "failed"))
    A, B, BASE, A_TOL, B_TOL = [], [], [], [], []
    fails = []
    ext = {}
    if with_extraction and gold:
        ext = extract_features_batch({g["id"]: g["problem"] for g in gold})
    for g in gold:
        y = g["label"] == "out"
        fa = to_features(g["features"]) if g.get("features") else ItemFeatures()
        pa, da = _pred_out(fa, g["lesson_id"])
        A.append((g, y, pa))
        if not (not y and pa and _adjacent_only(fa, g["lesson_id"])):  # 容差口径：金标 in、仅因「紧邻课时才引入」而判越界的，不计入
            A_TOL.append((g, y, pa))
        _, dbase = _pred_out(fa, g["lesson_id"], only=("integer_domain", "decimal_places"))
        BASE.append((g, y, bool(dbase)))
        if g["id"] in ext:
            pb, db = _pred_out(ext[g["id"]][0], g["lesson_id"])
            B.append((g, y, pb))
            if not (not y and pb and _adjacent_only(ext[g["id"]][0], g["lesson_id"])):
                B_TOL.append((g, y, pb))
        if y != pa:
            fails.append({"id": g["id"], "lesson": g["lesson_id"], "dimension": g["dimension"], "gold": g["label"], "pred_A": "out" if pa else "in",
                          "dims_A": da, "problem": g["problem"][:140], "features": g.get("features"),
                          "gold_dims": (g.get("output") or {}).get("out_dimensions")})

    def block(rows):
        return {"overall": _prf([(y, p) for _, y, p in rows]),
                "by_dimension": {d: _prf([(y, p) for g, y, p in rows if g["dimension"] == d]) for d in DIMS}}
    agree_ids = {g["id"] for g in gold if g["intended"] == g["label"]}
    intended = [(g["intended"] == g["label"]) for g in gold]
    return {
        "split": split, "n_gold": len(gold), "n_human_queue_or_failed": queue,
        "gold_label_counts": dict(Counter(g["label"] for g in gold)),
        "constructor_intent_agreement": wilson(sum(intended), len(intended)) if intended else None,
        "A_oracle_features": block(A), "B_extracted_features": block(B) if B else None,
        "A_oracle_features_adjacent_tolerant": block(A_TOL), "B_extracted_features_adjacent_tolerant": block(B_TOL) if B_TOL else None,
        "A_oracle_agreed_subset": block([(g, y, p) for g, y, p in A if g["id"] in agree_ids]),
        "B_extracted_agreed_subset": block([(g, y, p) for g, y, p in B if g["id"] in agree_ids]) if B else None,
        "A_oracle_agreed_subset_adjacent_tolerant": block([(g, y, p) for g, y, p in A_TOL if g["id"] in agree_ids]),
        "B_extracted_agreed_subset_adjacent_tolerant": block([(g, y, p) for g, y, p in B_TOL if g["id"] in agree_ids]) if B_TOL else None,
        "A_oracle_without_concepts": block([(g, y, p) for g, y, p in A if g["dimension"] != "concepts"]), "baseline_int_decimal_only": block(BASE),
        "failures_A": fails[:40],
    }


def probe_eval(split: str = "val") -> None:
    rep = evaluate(split)
    write_json(ROOT / "work" / "stage6" / f"probe_eval_{split}.json", rep)
    a = rep["A_oracle_features"]["overall"]
    print(json.dumps({k: rep[k] for k in ("split", "n_gold", "gold_label_counts", "constructor_intent_agreement")}, ensure_ascii=False))
    for k in ("A_oracle_features", "B_extracted_features", "A_oracle_features_adjacent_tolerant", "B_extracted_features_adjacent_tolerant",
              "A_oracle_agreed_subset", "B_extracted_agreed_subset", "A_oracle_agreed_subset_adjacent_tolerant",
              "B_extracted_agreed_subset_adjacent_tolerant", "A_oracle_without_concepts", "baseline_int_decimal_only"):
        if rep[k]:
            o = rep[k]["overall"]
            print(k, "P", o["precision"] and o["precision"]["p"], "R", o["recall"] and o["recall"]["p"], "F1", o["f1"], o["tp"], o["fp"], o["fn"], o["tn"])
            print("   by dim:", {d: (v["precision"] and v["precision"]["p"], v["recall"] and v["recall"]["p"], v["n"]) for d, v in rep[k]["by_dimension"].items()})
