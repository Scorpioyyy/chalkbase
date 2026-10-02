"""Stage 5 · `reconciled` 条目审阅（CLAUDE.md 4.3 的流程）：两个不同厂商模型独立盲审 → 分歧由强模型思考模式仲裁 → 低置信度进入人工队列。

每个条目是一条「问题—证据—修复」记录，审阅者回答这一处修复是否正确（pass / fail）。
条目类型：move（前移）、drop（取消前置）、gap（补全缺口知识点）、gap_edge（补全知识点的前置边）。
产物：eval/annotation/reconciliation/{judgments.jsonl, review.jsonl, stats.json}；
未通过的条目写入 data/judgments/stage5_review_rejections.json，由 `python -m curriculum.stage5` 在下一次从原始状态重建时应用。
"""
from __future__ import annotations

import json
from collections import Counter
from decimal import Decimal

from curriculum.annotate.client import AnnotationClient
from curriculum.annotate.gold import ModelConfig, LabelTask, run_label_gold
from curriculum.common import DATA_DIR, EVAL_DIR, book_of, read_json, write_json, write_jsonl
from curriculum.metrics import wilson
from curriculum.stage4.render import book_label
from curriculum.stage5.gaps import gap_id

DIR = EVAL_DIR / "annotation" / "reconciliation"
REJECTIONS = DATA_DIR / "judgments" / "stage5_review_rejections.json"
ANNOTATORS = (ModelConfig("qwen3.8-flash", False), ModelConfig("deepseek-v4.1-flash", False))
ARBITER = ModelConfig("qwen3.8-max", True, max_tokens=4096)


# 审阅者凭先验认为「教材里应该有」而否决的缺口，与本项目已核实的事实冲突：这些内容在手头 12 本教材的知识库中确实不存在
# （decisions.md D14；对知识点名称/描述的全文检索无「分数的意义」「长方形面积」）。保留缺口，记录在案，停顿点由用户抽查。
OVERRULED = {
    "gap.kp.gg.多边形面积.area_of_rectangle_and_square": "D14：12 本教材中没有长方形/正方形面积（新版三下无、旧版四下无、g5a 默认已会）；审阅者凭「北师大三下有」的先验否决，但手头教材并无此内容。",
    "gap.kp.gg.面积单位与测量.area_units_recognition_and_conversion": "D14：面积单位（平方厘米、平方分米、平方米）在 12 本教材中没有引入（知识库里只有公顷、平方千米，g5a 才出现）；审阅者凭「三下已教」的先验否决，手头教材并无此内容。位置已按课标学段放在第二学段末，不再放在 g5a。",
    "gap.kp.na.小数的初步认识.meaning_of_fractions_unit_1_and_fractional_unit": "知识库 419 个知识点中没有任何「分数的意义/分数单位」知识点（名称与描述全文检索核实），而分数运算、比、百分数等 40 余个知识点都以它为基础。",
}


def _validate(d) -> bool:
    return isinstance(d, dict) and d.get("verdict") in ("pass", "fail")


def load_rejections() -> dict:
    base = {"gaps": [], "gap_edges": [], "moves": [], "drops": []}
    if REJECTIONS.exists():
        base.update(read_json(REJECTIONS))
    return base


def _similar(kp: dict, kps: list[dict], k: int = 5) -> list[dict]:
    import numpy as np
    from sklearn.feature_extraction.text import TfidfVectorizer

    docs = [f"{x['name']} {x['description']}" for x in kps]
    vec = TfidfVectorizer(analyzer="char", ngram_range=(1, 2)).fit(docs)
    sim = (vec.transform([f"{kp['name']} {kp['description']}"]) @ vec.transform(docs).T).toarray()[0]
    return [kps[i] for i in np.argsort(-sim)[:k]]


def build_items() -> list[dict]:
    kps_all = read_json(DATA_DIR / "knowledge_points.json")
    kps = {k["id"]: k for k in kps_all}
    textbook = [k for k in kps_all if k["provenance"] == "textbook"]
    log = read_json(DATA_DIR / "stage5_reconciliation.json")
    edges = read_json(DATA_DIR / "edges_relations.json")
    titles = {l["id"]: l["title"] for l in read_json(DATA_DIR / "lessons.json")}
    spec = read_json(DATA_DIR / "judgments" / "stage5_gap_spec.json")
    gaps = {gap_id(g): g for g in spec["gaps"]}
    srcs = {s["id"]: s for s in spec["sources"]}

    def card(k, lesson=None):
        lid = lesson or k["first_introduced_lesson_id"]
        return f"名称：{k['name']}\n领域/主线：{k['domain']} / {k['thread']}\n引入位置：{book_label(book_of(lid))}，课时 {lid}「{titles[lid]}」\n描述：{k['description']}"

    items = []
    for m in log["moves"]:
        a = kps[m["kp"]]
        for t in m["triggers"]:
            b = kps[t["to"]]
            ev = next((e for e in edges if e["from_knowledge_point_id"] == t["from"] and e["to_knowledge_point_id"] == t["to"] and e["type"] == "prerequisite"), None)
            txt = (f"【条目类型】move（前移引入位置）\n【问题】前置关系 A → B 成立，但 A 在教材序列中的引入晚于 B。\n【知识点 A（教材原位置）】\n{card(a, m['from_lesson'])}\n\n"
                   f"【知识点 B】\n{card(b)}\n【证据】Stage 4 判定 A → B 为前置（置信度 {ev['evidence']['judged_confidence'] if ev else '?'}）。\n"
                   f"【修复】把 A 的引入前移到 B 所在课时 {m['to_lesson']}，作为该课时的先备知识点；A 原位置改记为复现。\n\n这一处修复是否正确？只输出 JSON。")
            items.append({"id": f"move.{t['from']}->{t['to']}", "kind": "move", "_render": txt})
    for d in log["edge_drops"]:
        a, b = kps[d["from"]], kps[d["to"]]
        txt = (f"【条目类型】drop（取消前置属性）\n【问题】前置关系 A → B 此前被判成立，但 A 在教材序列中的引入晚于 B。\n【知识点 A】\n{card(a)}\n\n【知识点 B】\n{card(b)}\n"
               f"【证据】分诊判断（{d['category']}）：{d['reason']}\n【修复】不前移 A，把 A → B 改记为 related（不再是前置）。\n\n这一处修复是否正确？只输出 JSON。")
        items.append({"id": f"drop.{d['from']}->{d['to']}", "kind": "drop", "_render": txt})
    for g in log["gaps"]:
        k = kps[g["kp"]]
        sim = _similar(k, textbook)
        deps = [kps[e["to_knowledge_point_id"]]["name"] for e in edges if e["from_knowledge_point_id"] == g["kp"] and e["type"] == "prerequisite"][:6]
        pre = [kps[e["from_knowledge_point_id"]]["name"] for e in edges if e["to_knowledge_point_id"] == g["kp"] and e["type"] == "prerequisite"][:6]
        why = "；".join(srcs[s]["text"][:100] for s in g["source_ids"][:3] if s in srcs)
        txt = (f"【条目类型】gap（补全缺口知识点）\n【问题】教材（12 本）中没有任何一本引入下面这个知识点，但后续内容或课标要求它。线索：{why}\n【新建的知识点】\n{card(k)}\n"
               f"【最相似的现有知识点（供判断是否重复）】\n" + "\n".join(f"- {s['name']}（{book_of(s['first_introduced_lesson_id'])}）：{s['description'][:70]}" for s in sim)
               + f"\n【修复】新建该知识点，引入位置 {g['lesson']}（{g['how']}）。它是这些知识点的前置：{('、'.join(deps)) or '（无）'}；它的前置：{('、'.join(pre)) or '（无）'}。\n\n这一处补全是否正确？只输出 JSON。")
        items.append({"id": f"gap.{g['kp']}", "kind": "gap", "_render": txt})
    for e in edges:
        if e["provenance"] == "reconciled" and e["type"] == "prerequisite" and e["evidence"].get("stage5", {}).get("action") == "gap_edge":
            a, b = kps[e["from_knowledge_point_id"]], kps[e["to_knowledge_point_id"]]
            txt = (f"【条目类型】gap_edge（补全知识点的前置边）\n【知识点 A】\n{card(a)}\n\n【知识点 B】\n{card(b)}\n【证据】模型判定 A → B 为前置：{e['evidence']['stage5'].get('reason', '')}\n"
                   f"【修复】新增前置边 A → B。\n\n这一处新增是否正确？只输出 JSON。")
            items.append({"id": f"gap_edge.{a['id']}->{b['id']}", "kind": "gap_edge", "_render": txt})
    return items


def run_review(client: AnnotationClient) -> dict:
    items = build_items()
    task = LabelTask(name="reconciliation", render=lambda it: it["_render"], validate=_validate, extract=lambda d: d["verdict"])
    res = run_label_gold(client, task, items, ANNOTATORS, ARBITER, arbiter_extra_instruction="请独立判断，输出同样格式的 JSON。", judgment_task_type="reconciliation_review")
    DIR.mkdir(parents=True, exist_ok=True)
    write_jsonl(DIR / "judgments.jsonl", res.judgments)
    write_jsonl(DIR / "review.jsonl", [{k: v for k, v in g.items() if k != "_render"} for g in res.gold])
    by_kind: dict[str, dict] = {}
    for kind in ["move", "drop", "gap", "gap_edge", "_all"]:
        rows = [g for g in res.gold if kind == "_all" or g["kind"] == kind]
        c = Counter((g["label"], g["source"]) for g in rows)
        passed = sum(1 for g in rows if g["label"] == "pass" and g["source"] != "human_queue")
        by_kind[kind] = {"n": len(rows), "pass": passed, "fail": sum(1 for g in rows if g["label"] == "fail" and g["source"] != "human_queue"),
                         "human_queue": sum(1 for g in rows if g["source"] == "human_queue"), "pass_rate": wilson(passed, len(rows)),
                         "consensus_pass": c[("pass", "consensus")], "arbitrated_pass": c[("pass", "arbitrated")]}
    stats = dict(res.stats)
    stats["by_kind"] = by_kind
    write_json(DIR / "stats.json", stats)
    rej = load_rejections()  # 累积：每一轮审阅的否决都保留，从原始状态重建时全部应用
    overruled = []
    for g in res.gold:
        if (g["label"] == "fail" and g["source"] != "human_queue") or g["source"] == "failed":  # 仲裁调用失败（两位审阅者有分歧）：保守处理，视同未通过
            reason = (g.get("output") or {}).get("reason", "") or "两位审阅者有分歧且仲裁调用失败，保守视为未通过"
            if g["id"] in OVERRULED:  # 审阅者与已核实的事实冲突（见 OVERRULED），不应用否决，但如实记录
                overruled.append({"id": g["id"], "reviewer_reason": reason, "overruled_because": OVERRULED[g["id"]]})
                continue
            key = {"gap": "gaps", "gap_edge": "gap_edges", "move": "moves", "drop": "drops"}[g["kind"]]
            if not any(i["id"] == g["id"] for i in rej[key]):
                rej[key].append({"id": g["id"], "reason": reason})
    rej["overruled"] = overruled
    write_json(REJECTIONS, rej)
    return {"n": len(items), "by_kind": by_kind, "cost_cny": stats["cost_cny"], "counts": stats["counts"], "kappa": stats["cohen_kappa"],
            "rejected": {k: len(v) for k, v in rej.items()}}
