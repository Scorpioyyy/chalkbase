"""Stage 5 · 确定性修复：顺序冲突前移（不动点）→ 缺口知识点并入 → 环检测 → 传递约简。

纯函数式、可重复运行（幂等）：对自己的输出再跑一次不产生变化。输入的所有「判断」都来自已落盘的 Judgment 记录
（逆序边分诊 stage5_conflict_triage.jsonl，缺口定义 stage5_gap_spec.json），本文件只做确定性计算。
"""
from __future__ import annotations

import copy
from collections import defaultdict

import networkx as nx

from curriculum.common import book_index, lesson_order, load_work_books
from curriculum.models import Edge, KnowledgePoint, Lesson

STAGE_END_BOOK = {"s1": "g2b", "s2": "g4b", "s3": "g6b"}  # 无依赖者的缺口默认放在其课标学段的最后一本书末尾


class ReconcileError(RuntimeError):
    pass


def _lesson_list() -> list[str]:
    order = lesson_order()
    return sorted(order, key=order.get)


def book_end_lesson(book_id: str) -> str:
    ls = [l for l in _lesson_list() if l.startswith(book_id + ".")]
    return ls[-1]


def gap_kp_record(g: dict, gid: str, lesson_id: str, note: str) -> dict:
    gr = g.get("grants") or {}
    grants = {}
    for k in ("integer_domain_max", "decimal_max_places"):
        if gr.get(k) is not None:
            grants[k] = gr[k]
    for k in ("fraction_types", "concepts", "units_of_measure", "geometry_vocab"):
        if gr.get(k):
            grants[k] = sorted(set(gr[k]))
    ops = {op: sorted(set(v)) for op, v in (gr.get("operation_operand_forms") or {}).items() if v}
    if ops:
        grants["operation_operand_forms"] = ops
    kp = {
        "id": gid, "name": g["name"], "aliases": list(g.get("aliases") or []), "domain": g["domain"],
        "topic": g.get("topic") or "", "thread": g["thread"], "description": g["description"],
        "mastery_level": g.get("mastery_level") if g.get("mastery_level") in ("know", "understand", "master", "apply") else "understand",
        "first_introduced_lesson_id": lesson_id, "review_lesson_ids": [], "is_assessable": True,
        "typical_errors": list(g.get("typical_errors") or []), "grants": grants, "explicit_review_refs": [],
        "provenance": "reconciled", "provenance_note": note,
    }
    KnowledgePoint(**kp)
    return kp


def reconcile(kps_in: list[dict], lessons_in: list[dict], edges_in: list[dict], spec: dict, triage: list[dict],
              prev_log: dict | None = None, rejections: dict | None = None) -> tuple[list[dict], list[dict], list[dict], dict]:
    """返回（知识点, 课时, 关系边, 修复日志）。输入不被修改。"""
    kps = {k["id"]: copy.deepcopy(k) for k in kps_in}
    kp_order = [k["id"] for k in kps_in]
    lessons = {l["id"]: copy.deepcopy(l) for l in lessons_in}
    edges = [copy.deepcopy(e) for e in edges_in]
    order = lesson_order()
    pristine = not any(k["provenance"] == "reconciled" for k in kps.values())
    log = {"moves": [], "gaps": [], "edge_drops": [], "implied": {}, "cycles": []} if (pristine or not prev_log) else copy.deepcopy(prev_log)
    pos = lambda k: order[kps[k]["first_introduced_lesson_id"]]
    if pristine or not prev_log:
        log["initial_order_conflicts"] = sum(1 for e in edges if e["type"] == "prerequisite" and pos(e["from_knowledge_point_id"]) > pos(e["to_knowledge_point_id"]))

    # ---- 1. 判定错误的逆序边：取消前置属性（改记 related，原判定保留在 evidence）
    rej = rejections or {}
    pair_of = lambda i: tuple(i.split(".", 1)[1].split("->"))
    drop = {(t["a"], t["b"]): t for t in triage if t["decision"] == "drop"}
    for i in rej.get("drops", []):  # 审阅否决了「取消前置」：回到前移
        drop.pop(pair_of(i["id"]), None)
    for i in rej.get("moves", []):  # 审阅否决了「前移」：A 并非 B 的必需前置，改为取消前置
        drop[pair_of(i["id"])] = {"id": "review.reject_move", "category": "review_rejected_move", "reasoning": i.get("reason", ""), "confidence": 0.0}
    rej_gaps = {i["id"].split(".", 1)[1] for i in rej.get("gaps", [])}
    rej_gedges = {pair_of(i["id"]) for i in rej.get("gap_edges", [])}
    for e in edges:
        key = (e["from_knowledge_point_id"], e["to_knowledge_point_id"])
        if e["type"] == "prerequisite" and key in drop:
            t = drop[key]
            e["evidence"]["stage5"] = {"action": "dropped_prerequisite", "was_type": "prerequisite", "triage_judgment_id": t["id"], "category": t["category"], "reason": t["reasoning"]}
            e["type"] = "related"
            e["id"] = f"e.related.{key[0]}.{key[1]}"
            e["provenance"] = "reconciled"
            e["provenance_note"] = f"Stage 5 复核：A 的引入晚于 B，经分诊判定为 Stage 4 判定错误（{t['category']}），前置改记为 related。理由：{t['reasoning']}"
            log["edge_drops"].append({"from": key[0], "to": key[1], "category": t["category"], "reason": t["reasoning"], "triage_judgment_id": t["id"], "confidence": t["confidence"]})

    # ---- 2. 并入缺口知识点（幂等：已存在则跳过）
    from curriculum.stage5.gaps import gap_id  # 延迟导入，避免循环

    prereq_pairs = lambda: [(e["from_knowledge_point_id"], e["to_knowledge_point_id"]) for e in edges if e["type"] == "prerequisite"]
    existing_pairs = {(e["from_knowledge_point_id"], e["to_knowledge_point_id"], e["type"]) for e in edges}
    new_gap_ids = []
    for g in spec.get("gaps", []):
        gid = gap_id(g)
        if gid in kps or gid in rej_gaps:
            continue
        new_gap_ids.append(gid)
    gedges = [e for e in spec.get("edges", []) if e["from"] in kps or e["from"] in new_gap_ids]
    gedges = [e for e in gedges if (e["to"] in kps or e["to"] in new_gap_ids) and (e["from"], e["to"]) not in rej_gedges and (e["from"], e["to"]) not in drop]
    for g in spec.get("gaps", []):
        gid = gap_id(g)
        if gid not in new_gap_ids:
            continue
        deps = [e["to"] for e in gedges if e["from"] == gid and e["type"] == "prerequisite" and e["to"] in kps]
        pre = [e["from"] for e in gedges if e["to"] == gid and e["type"] == "prerequisite" and e["from"] in kps]
        soft = [e["to"] for e in gedges if e["from"] == gid and e["type"] == "builds_on" and e["to"] in kps]
        if deps:
            lesson = min((kps[d]["first_introduced_lesson_id"] for d in deps), key=order.get)
            how = f"最早的直接依赖者「{kps[min(deps, key=pos)]['name']}」所在课时"
            # 递进（builds_on）依赖者是较弱的信号，不构成约束，但缺口的位置本来就是选择出来的：不晚于它们、且不早于自己的前置知识点
            early = [d for d in soft if order[kps[d]["first_introduced_lesson_id"]] < order[lesson]]
            if early:
                cand = min((kps[d]["first_introduced_lesson_id"] for d in early), key=order.get)
                floor = max((kps[p]["first_introduced_lesson_id"] for p in pre), key=order.get, default=None)
                if floor is None or order[floor] <= order[cand]:
                    lesson, how = cand, f"最早的依赖者（含递进关系）「{kps[min(early, key=pos)]['name']}」所在课时"
            # 课标把它安排在某个学段内学：到该学段最后一本书结束时学生应当已会，所以不晚于该学段末尾（且不早于自己的前置）
            stage_end = book_end_lesson(STAGE_END_BOOK[g.get("stage_hint", "s2")])
            floor = max((kps[p]["first_introduced_lesson_id"] for p in pre), key=order.get, default=None)
            if order[stage_end] < order[lesson] and (floor is None or order[floor] <= order[stage_end]):
                lesson, how = stage_end, f"其课标学段 {g.get('stage_hint', 's2')} 的最后一本书 {STAGE_END_BOOK[g.get('stage_hint', 's2')]} 末尾（早于最早的依赖者「{kps[min(deps + soft, key=pos)]['name']}」）"
        else:
            lesson = book_end_lesson(STAGE_END_BOOK[g.get("stage_hint", "s2")])
            how = f"无教材内依赖者，按课标学段 {g.get('stage_hint', 's2')} 放在 {STAGE_END_BOOK[g.get('stage_hint', 's2')]} 末尾"
        if pre:
            latest = max((kps[p]["first_introduced_lesson_id"] for p in pre), key=order.get)
            if not deps and order[latest] > order[lesson]:
                lesson, how = latest, how + "，并不早于其前置知识点"
        srcs = {s["id"]: s for s in spec["sources"]}
        why = "；".join(srcs[s]["text"][:80] for s in g["source_ids"][:3] if s in srcs)
        note = f"缺口补全：教材中没有任何一本引入此知识点。线索：{why}。引入位置：{how}（作为该课时的先备知识点）。"
        kps[gid] = gap_kp_record(g, gid, lesson, note)
        kp_order.append(gid)
        log["gaps"].append({"kp": gid, "name": g["name"], "lesson": lesson, "how": how, "source_ids": g["source_ids"], "stage_hint": g.get("stage_hint")})
        lessons[lesson]["intro_knowledge_point_ids"] = [gid] + [x for x in lessons[lesson]["intro_knowledge_point_ids"] if x != gid]
    for e in gedges:
        key = (e["from"], e["to"])
        if (key[0], key[1], e["type"]) in existing_pairs or e["type"] not in ("prerequisite", "builds_on", "related", "confusable"):
            continue
        new = {
            "id": f"e.{e['type']}.{key[0]}.{key[1]}", "type": e["type"], "from_knowledge_point_id": key[0], "to_knowledge_point_id": key[1],
            "evidence": {"routes": ["stage5_gap_link"], "judged_label": e["judged_label"], "judged_confidence": e["judged_confidence"], "order_conflict": False,
                         "stage5": {"action": "gap_edge", "reason": e.get("reason", "")}},
            "judgment_id": e["judgment_id"], "is_direct": True, "provenance": "reconciled",
            "provenance_note": f"缺口补全产生的关系边：{kps[key[0]]['name']} → {kps[key[1]]['name']}",
        }
        edges.append(new)
        existing_pairs.add((key[0], key[1], e["type"]))

    # ---- 3. 顺序冲突前移（单调，迭代到不动点）
    g_pre = nx.DiGraph()
    changed, rounds = True, 0
    while changed:
        changed = False
        rounds += 1
        if rounds > len(kps) + 5:
            raise ReconcileError("前移未收敛（不应发生：引入位置只会前移，课时数有限）")
        for e in sorted((e for e in edges if e["type"] == "prerequisite"), key=lambda e: (e["from_knowledge_point_id"], e["to_knowledge_point_id"])):
            a, b = e["from_knowledge_point_id"], e["to_knowledge_point_id"]
            if pos(a) <= pos(b):
                continue
            target = kps[b]["first_introduced_lesson_id"]
            origin = kps[a]["first_introduced_lesson_id"]
            _move(kps, lessons, a, origin, target, b, log)
            changed = True

    # ---- 4. 环检测
    g_pre.add_nodes_from(kps)
    g_pre.add_edges_from(prereq_pairs())
    if not nx.is_directed_acyclic_graph(g_pre):
        cyc = nx.find_cycle(g_pre)
        log["cycles"] = [list(c) for c in cyc]
        raise ReconcileError(f"前置子图有环，需回到 Stage 4 复核相关边：{cyc}")

    # ---- 5. 传递约简：被约简的边标记 is_direct=False（隐含），附一条替代路径作为证据
    red0 = nx.transitive_reduction(g_pre)
    red = nx.DiGraph()  # 重建以固定邻接顺序（集合迭代顺序随进程变化），使替代路径证据可复现
    red.add_nodes_from(sorted(red0.nodes))
    red.add_edges_from(sorted(red0.edges))
    for e in edges:
        if e["type"] != "prerequisite":
            e["is_direct"] = True
            continue
        a, b = e["from_knowledge_point_id"], e["to_knowledge_point_id"]
        if red.has_edge(a, b):
            e["is_direct"] = True
            e["evidence"].pop("implied_via", None)
        else:
            e["is_direct"] = False
            path = nx.shortest_path(red, a, b)
            e["evidence"]["implied_via"] = path[1:-1]
    log["reduction"] = {"prerequisite_edges": sum(1 for e in edges if e["type"] == "prerequisite"), "direct": red.number_of_edges(),
                        "implied": sum(1 for e in edges if e["type"] == "prerequisite" and not e["is_direct"])}

    kps_out = [kps[i] for i in kp_order]
    for k in kps_out:
        KnowledgePoint(**k)
    lessons_out = [lessons[l["id"]] for l in lessons_in]
    for l in lessons_out:
        Lesson(**l)
    for e in edges:
        Edge(**e)
    return kps_out, lessons_out, edges, log


def _move(kps, lessons, a: str, origin: str, target: str, b: str, log: dict) -> None:
    """A 的引入从 origin 前移到 target（B 的引入课时）；原位置改记为复现。"""
    k = kps[a]
    k["first_introduced_lesson_id"] = target
    if origin not in k["review_lesson_ids"]:
        k["review_lesson_ids"] = sorted(set(k["review_lesson_ids"]) | {origin}, key=lesson_order().get)
    k["review_lesson_ids"] = [l for l in k["review_lesson_ids"] if l != target]
    lo = lessons[origin]
    if a in lo["intro_knowledge_point_ids"]:
        lo["intro_knowledge_point_ids"].remove(a)
    if a not in lo["practice_knowledge_point_ids"]:
        lo["practice_knowledge_point_ids"].append(a)
    lt = lessons[target]
    lt["practice_knowledge_point_ids"] = [x for x in lt["practice_knowledge_point_ids"] if x != a]
    lt["intro_knowledge_point_ids"] = [a] + [x for x in lt["intro_knowledge_point_ids"] if x != a]
    prev = next((m for m in log["moves"] if m["kp"] == a), None)
    trigger = {"from": a, "to": b}
    if prev:
        prev["to_lesson"] = target
        prev["triggers"].append(trigger)
    else:
        log["moves"].append({"kp": a, "name": k["name"], "from_lesson": origin, "to_lesson": target, "triggers": [trigger]})
    m = next(m for m in log["moves"] if m["kp"] == a)
    trig = "；".join(f"{t['to'].split('.')[-1]}" for t in m["triggers"])
    k["provenance"] = "reconciled"
    k["provenance_note"] = (f"顺序冲突修复：教材原引入于 {m['from_lesson']}，但它是引入于 {m['to_lesson']} 的知识点（{trig}）的前置，"
                            f"故前移到 {m['to_lesson']}（作为该课时的先备知识点），原位置改记为复现。")
