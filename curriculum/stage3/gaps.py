"""Stage 3 · 缺口知识点的题型卡片（provenance=reconciled）。

Stage 5 为教材版本缺口补建了知识点（`data/gap_kps_pending_cards.json`，只有描述与建议题型方向，没有教材习题）。
这里把「知识点 × 建议方向」当作虚拟分组，复用 `generate_cards`（D18 的校验与修复流程原样适用：程序重算、约束接受率、
5-gram 重合率〔无源文本，恒通过〕、槽位包络）。没有源实例：`source_instance_ids = []`；参数包络没有教材观测，
按知识点引入年级给一个保守上限（整数位数、小数位数）。

    python -m curriculum.stage3.gaps          # 生成并合并进 data/archetypes.json（幂等：先清掉该知识点已有的 reconciled 题型）
"""
from __future__ import annotations

import json
import re
from collections import Counter

from curriculum.annotate.client import AnnotationClient
from curriculum.annotate.gold import judgment_record
from curriculum.common import DATA_DIR, book_of, grade_of, read_json, read_jsonl, write_json, write_jsonl
from curriculum.models import ItemArchetype
from curriculum.stage3.build import ANSWER_FORM_DEFAULT, kp_slugs
from curriculum.stage3.generate import generate_cards, ngram_overlap

GAP_TMPL = """【题型信息】
主知识点：{kp_name}（{kp_desc}）
知识点要求的能力：{grants}
题型方向：{direction}
题目形式：{item_form}
适用年级：{grades}
参数包络（保守上限，没有教材观测）：{envelope}

【说明】这个知识点在现有教材里没有习题（教材版本缺口），没有源实例。请依据知识点描述与题型方向，**全新编写**一个符合小学数学教材风格、年级适宜的题型卡片；题目只写一种题，示例必须是不同的题。

请输出题型卡片 JSON。"""


def _form(direction: str) -> str:
    if re.search(r"画|作图|画出|标出|标记", direction):
        return "draw"
    if re.search(r"判断", direction):
        return "judge"
    if re.search(r"选择", direction):
        return "choice"
    if re.search(r"测量|折叠|操作|量", direction):
        return "measure"
    if re.search(r"实际问题|生活|应用|解决", direction):
        return "word_problem"
    if re.search(r"填", direction):
        return "fill_blank"
    if re.search(r"计算|求|化简|约分|通分|加|减|乘|除", direction):
        return "compute"
    return "other"


def _envelope(grade: int) -> dict:
    digits = {1: 2, 2: 3, 3: 4, 4: 6, 5: 6, 6: 8}[grade]
    places = 2 if grade <= 4 else 3
    return {"number_types": [], "integer_digits": [1, digits], "decimal_places": [0, places], "requires_carry_or_borrow": None,
            "requires_exact_division": None, "operation_steps": None, "answer_forms": {}, "figure_types": [], "requires_figure_ratio": 0.0,
            "grades": [grade], "n_instances": 0}


def run(client: AnnotationClient | None = None) -> dict:
    client = client or AnnotationClient(max_workers=int(__import__("os").environ.get("STAGE3_WORKERS", "48")))
    kps = {k["id"]: k for k in read_json(DATA_DIR / "knowledge_points.json")}
    gaps = read_json(DATA_DIR / "gap_kps_pending_cards.json")
    slugs = kp_slugs(kps)
    jobs = []
    for gi, g in enumerate(gaps):
        kp = kps[g["kp_id"]]
        grade = grade_of(book_of(kp["first_introduced_lesson_id"]))
        env = _envelope(grade)
        for di, d in enumerate(g["suggested_archetype_directions"]):
            form = _form(d)
            env_p = {k: v for k, v in env.items() if k not in ("answer_forms", "n_instances") and v not in (None, [], "", 0.0)}
            msg = GAP_TMPL.format(kp_name=kp["name"], kp_desc=kp["description"][:160], grants=json.dumps(g.get("grants") or {}, ensure_ascii=False),
                                  direction=d, item_form=form, grades=grade, envelope=json.dumps(env_p, ensure_ascii=False))
            jobs.append({"id": f"gap{gi:02d}_{di}", "kp": g["kp_id"], "form": form, "direction": d, "user_msg": msg, "envelope": env,
                         "source_texts": [], "seed": 5000 + gi * 10 + di})
    state = generate_cards(jobs, client)

    old = read_json(DATA_DIR / "archetypes.json")
    gap_kp_ids = {g["kp_id"] for g in gaps}
    kept = [a for a in old if not (a["provenance"] == "reconciled" and a["primary_knowledge_point_id"] in gap_kp_ids)]
    seq = Counter(a["primary_knowledge_point_id"] for a in kept)
    new, judgments, failures = [], [], []
    for j in jobs:
        s = state[j["id"]]
        for rnd, res in enumerate(s["calls"]):
            jr = judgment_record("other", f"{j['id']}.r{rnd + 1}", "pipeline", res, j["user_msg"], json.dumps(res.parsed, ensure_ascii=False)[:2000] if res.ok else "ERROR")
            jr["task"] = "archetype_generation"
            judgments.append(jr)
        card, downgraded = s["card"], False
        seq[j["kp"]] += 1
        aid = f"at.{slugs[j['kp']]}.{seq[j['kp']]:02d}"
        if s["errors"] or card is None:
            failures.append({"archetype_id": aid, "group": j["id"], "errors": s["errors"], "rounds": s["rounds"], "resolution": "downgraded_to_human" if card else "missing"})
            if card is None:
                seq[j["kp"]] -= 1
                continue
            for res in reversed(s["calls"]):
                c = res.parsed if res.ok else None
                if c and len({e["problem"] for e in c["examples"]}) == len(c["examples"]):
                    card = c
                    break
            card = dict(card, verifiable_type="human", solver=None)
            downgraded = True
        vt = card["verifiable_type"]
        answer_form = {"draw": "drawing", "judge": "boolean", "choice": "choice_letter"}.get(j["form"]) or ("number" if vt == "program" else "free_text")
        a = {
            "id": aid, "primary_knowledge_point_id": j["kp"], "secondary_knowledge_point_ids": [], "item_form": j["form"],
            "template": card["template"],
            "parameter_constraints": {"observed": j["envelope"], "slots": card["slots"], "constraints": card.get("constraints") or [],
                                      "answer_format": card.get("answer_format", ""), "direction": j["direction"]},
            "answer_form": answer_form or ANSWER_FORM_DEFAULT, "solution_steps": [str(x) for x in card.get("solution_steps", [])],
            "allowed_contexts": [], "figure_types": [], "verifiable_type": vt,
            "solver_program": card.get("solver") if vt == "program" else None,
            "difficulty": 1, "difficulty_features": {"reverse_thinking": int(bool(card.get("requires_reverse_thinking", False)))},
            "source_instance_ids": [],
            "rewritten_examples": [{"problem": e["problem"], "answer": str(e["answer"]), "solution": str(e.get("solution", "")), "params": e.get("params") or {},
                                    "answer_value": e.get("answer_value")} for e in card["examples"]],
            "typical_errors": [str(x) for x in card.get("typical_errors", [])],
            "provenance": "reconciled",
            "provenance_note": ("教材版本缺口补全（Stage 5）：无教材源实例，由知识点描述与建议方向生成" + ("；程序校验在 6 轮修复后仍未通过，降级为 human 类" if downgraded else "")),
        }
        ItemArchetype(**a)
        new.append(a)
    write_json(DATA_DIR / "archetypes.json", kept + new)
    jpath = DATA_DIR / "judgments" / "stage3_generation.jsonl"
    prior = read_jsonl(jpath) if jpath.exists() else []
    ids = {r["id"]: r for r in judgments}
    prior = [r for r in prior if not (r["id"] in ids and not r["ok"])]
    seen = {r["id"] for r in prior}
    write_jsonl(jpath, prior + [r for r in judgments if r["id"] not in seen])
    fpath = DATA_DIR / "judgments" / "stage3_generation_failures.json"
    prev_f = [f for f in (read_json(fpath) if fpath.exists() else []) if not str(f.get("group", "")).startswith("gap")]
    write_json(fpath, prev_f + failures)
    # 回写缺口清单的 cards_generated 标记
    done = {a["primary_knowledge_point_id"] for a in new}
    for g in gaps:
        g["cards_generated"] = g["kp_id"] in done
    write_json(DATA_DIR / "gap_kps_pending_cards.json", gaps)
    return {"n_new": len(new), "n_jobs": len(jobs), "verifiable": dict(Counter(a["verifiable_type"] for a in new)), "n_failures": len(failures)}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
