"""Stage 4 · 候选生成与确定性证据（KICKOFF §9 Stage 4 第 1～3 步）。

候选是有序对 (A, B)，含义「A 可能是 B 的前置」。两路取并集：
  (a) 时间约束路：同一主线或同一主题中，A 的首次引入早于 B；
  (b) 共现路：A 在 B 的习题实例（B 为主知识点）中作为次知识点出现过，**不受引入顺序限制**；
另把 Stage 2 的 extends 边（窄 → 宽）并入（它们几乎都已被 (a) 覆盖，单列来源便于审计）。
"""
from __future__ import annotations

from collections import Counter, defaultdict

import json

from chalkbase.common import DATA_DIR, book_index, book_of, lesson_order, read_json, read_jsonl, JUDGMENTS_DIR


def load_canonical() -> tuple[dict[str, dict], dict[str, int], dict[str, int]]:
    kps = {k["id"]: k for k in read_json(DATA_DIR / "knowledge_points.json")}
    order = lesson_order()
    pos = {k: order[v["first_introduced_lesson_id"]] for k, v in kps.items()}
    sem = {k: book_index(book_of(v["first_introduced_lesson_id"])) for k, v in kps.items()}
    return kps, pos, sem


def cooccurrence() -> tuple[dict[tuple[str, str], int], Counter]:
    """(A, B) → B 的实例中把 A 当次知识点的实例数；以及 B 的实例总数。"""
    ex = read_json(DATA_DIR / "exercises.json")
    co: dict[tuple[str, str], int] = defaultdict(int)
    n_b: Counter = Counter()
    for e in ex:
        b = e["primary_knowledge_point_id"]
        n_b[b] += 1
        for a in e["secondary_knowledge_point_ids"]:
            co[(a, b)] += 1
    return dict(co), n_b


def thread_neighbors(kps: dict[str, dict], pos: dict[str, int]) -> set[tuple[str, str]]:
    by_thread = defaultdict(list)
    for k, v in kps.items():
        by_thread[(v["domain"], v["thread"])].append(k)
    out = set()
    for ks in by_thread.values():
        ks.sort(key=lambda k: (pos[k], k))
        for a, b in zip(ks, ks[1:]):
            out.add((a, b))
    return out


def generate_candidates() -> dict[tuple[str, str], dict]:
    kps, pos, sem = load_canonical()
    co, n_b = cooccurrence()
    extends = {(e["from_knowledge_point_id"], e["to_knowledge_point_id"]): e for e in read_json(DATA_DIR / "edges_extends.json")}
    neighbors = thread_neighbors(kps, pos)
    cands: dict[tuple[str, str], dict] = {}

    def add(a, b, route):
        if a == b:
            return
        rec = cands.setdefault((a, b), {"routes": []})
        if route not in rec["routes"]:
            rec["routes"].append(route)

    ids = sorted(kps)
    for a in ids:
        for b in ids:
            if a == b or pos[a] >= pos[b]:
                continue
            ka, kb = kps[a], kps[b]
            if ka["domain"] == kb["domain"] and ka["thread"] == kb["thread"]:
                add(a, b, "time_same_thread")
            elif ka["topic"] == kb["topic"]:
                add(a, b, "time_same_topic")
    for (a, b) in co:
        add(a, b, "cooccurrence")
    for (a, b) in extends:
        add(a, b, "stage2_extends")
    for j in read_jsonl(JUDGMENTS_DIR / "stage4_screen.jsonl"):  # 第三路：模型筛选（D15）
        b = j["item_id"].split("screen.", 1)[1]
        for a in json.loads(j["conclusion"]):
            add(a, b, "model_screen")
    for j in read_jsonl(JUDGMENTS_DIR / "stage4_screen_reverse.jsonl"):  # 第三路补充：反向筛选（D15）
        a = j["item_id"].split("screen_rev.", 1)[1]
        for b in json.loads(j["conclusion"]):
            add(a, b, "model_screen_reverse")

    for (a, b), rec in cands.items():
        ka, kb = kps[a], kps[b]
        rec["evidence"] = {
            "cooccurrence_count": co.get((a, b), 0),
            "cooccurrence_ratio": round(co.get((a, b), 0) / n_b[b], 4) if n_b[b] else 0.0,
            "b_instances": n_b[b],
            "reverse_cooccurrence_count": co.get((b, a), 0),
            "same_thread": ka["domain"] == kb["domain"] and ka["thread"] == kb["thread"],
            "same_topic": ka["topic"] == kb["topic"],
            "thread_adjacent": (a, b) in neighbors,
            "a_intro_lesson": ka["first_introduced_lesson_id"],
            "b_intro_lesson": kb["first_introduced_lesson_id"],
            "order_conflict": pos[a] > pos[b],  # A 的首次引入晚于 B：若判为前置即版本冲突信号，交 Stage 5
            "semester_gap": sem[b] - sem[a],
            "stage2_extends": (a, b) in extends,
            "stage2_extends_reverse": (b, a) in extends,
            "explicit_review_refs_b": kb.get("explicit_review_refs", []),
        }
    return cands


def stratum_of(pair: tuple[str, str], rec: dict | None) -> str:
    """成对金标分层（eval/specs/stage4.md §3.1）。"""
    if rec is None:
        return "N"
    ev = rec["evidence"]
    if ev["cooccurrence_count"] > 0:
        return "R" if ev["order_conflict"] else "C"
    if ev["stage2_extends"]:
        return "X"
    if ev["same_thread"]:
        return "T"
    if set(rec["routes"]) <= {"model_screen", "model_screen_reverse"}:
        return "M"  # 仅来自模型筛选路（D15 之后新增，金标分层抽样时尚无此路）
    return "P"
