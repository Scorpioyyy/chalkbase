"""Stage 2 · 约束聚类与规范目录构建。

输入：局部知识点（work/books/*/knowledge_points.json）+ 成对判定 Judgment。
输出（data/）：knowledge_points.json、kp_local_map.json、books.json、lessons.json、edges_extends.json、
judgments/stage2_pairwise.jsonl。
"""
from __future__ import annotations

from collections import defaultdict

import networkx as nx

from chalkbase.common import DATA_DIR, book_index, lesson_order, load_work_books, local_key, local_kps, write_json, write_jsonl, JUDGMENTS_DIR
from chalkbase.models import Book, Edge, EdgeType, KnowledgePoint, Lesson, Unit

MASTERY_ORDER = ["know", "understand", "master", "apply"]


# ------------------------------------------------------------------ 约束聚类


def cannot_link_pairs() -> set[frozenset]:
    """同一课时内被抽取者刻意区分的两个知识点（同一课时 intro 列表中的不同局部知识点）不得合并。"""
    out = set()
    for bid, b in load_work_books().items():
        for l in b["lessons"]:
            keys = [local_key(bid, k) for k in l.get("intro_knowledge_point_ids", [])]
            for i, x in enumerate(keys):
                for y in keys[i + 1 :]:
                    if x != y:
                        out.add(frozenset((x, y)))
    return out


def constrained_components(
    same_edges: list[tuple[str, str, float]], judged_not_same: set[frozenset] | None = None
) -> tuple[list[set[str]], list[dict]]:
    """以 same 为边求连通分量；若分量违反 cannot-link，移除分量内置信度最低的 same 边后重求，直至无违反。

    cannot-link = 同一课时内刻意区分的知识点（KICKOFF）∪ 判定模型直接判为 extends/different 的候选对
    （直接判定的证据优先于传递推出的「同一」，见 decisions.md D13）。
    返回（分量列表，被移除的边记录）。
    """
    cl = cannot_link_pairs() | (judged_not_same or set())
    g = nx.Graph()
    g.add_nodes_from(k["_key"] for k in local_kps())
    for a, b, conf in same_edges:
        if g.has_edge(a, b):
            g[a][b]["confidence"] = max(g[a][b]["confidence"], conf)
        else:
            g.add_edge(a, b, confidence=conf)
    removed = []
    while True:
        violated = None
        for comp in nx.connected_components(g):
            if len(comp) < 2:
                continue
            members = sorted(comp)
            for i, x in enumerate(members):
                for y in members[i + 1 :]:
                    if frozenset((x, y)) in cl:
                        violated = comp
                        break
                if violated:
                    break
            if violated:
                break
        if not violated:
            break
        sub = g.subgraph(violated)
        a, b, d = min(sub.edges(data=True), key=lambda e: (e[2]["confidence"], e[0], e[1]))
        g.remove_edge(a, b)
        removed.append({"a": a, "b": b, "confidence": d["confidence"], "component_size": len(violated)})
    comps = [set(c) for c in nx.connected_components(g)]
    return comps, removed


# ------------------------------------------------------------------ 规范知识点


def _merge_grants(grants: list[dict]) -> dict:
    out: dict = {}
    for gr in grants:
        for k in ("integer_domain_max", "decimal_max_places"):
            if gr.get(k) is not None:
                out[k] = max(out.get(k, gr[k]), gr[k])
        for k in ("fraction_types", "concepts", "units_of_measure", "geometry_vocab"):
            if gr.get(k):
                out.setdefault(k, set()).update(gr[k])
        for op, forms in (gr.get("operation_operand_forms") or {}).items():
            out.setdefault("operation_operand_forms", {}).setdefault(op, set()).update(forms)
    return out


def _dedup(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def build_canonical(components: list[set[str]]) -> tuple[list[dict], dict[str, str]]:
    kps = {k["_key"]: k for k in local_kps()}
    order = lesson_order()
    lorder = {k["_key"]: i for i, k in enumerate(local_kps())}
    canon, local_to_canon = [], {}
    used_ids: dict[str, str] = {}
    comps = sorted(components, key=lambda c: min(lorder[x] for x in c))
    for comp in comps:
        members = sorted(comp, key=lambda x: lorder[x])
        primary = kps[members[0]]
        cid = primary["id"]
        if cid in used_ids:  # 局部 ID 跨书重名但未合并：追加书 ID 消歧
            cid = f"{cid}_{primary['_book']}"
        used_ids[cid] = members[0]
        mem = [kps[m] for m in members]
        intro = primary["first_introduced_lesson_id"]
        reviews = set()
        for m in mem:
            reviews.add(m["first_introduced_lesson_id"])
            reviews.update(m.get("review_lesson_ids", []))
        reviews.discard(intro)
        names = _dedup([m["name"] for m in mem] + [a for m in mem for a in m.get("aliases", [])])
        kp = {
            "id": cid,
            "name": primary["name"],
            "aliases": [n for n in names if n != primary["name"]],
            "domain": primary["domain"],
            "topic": primary["topic"],
            "thread": primary["thread"],
            "description": primary["description"],
            "mastery_level": max((m["mastery_level"] for m in mem), key=MASTERY_ORDER.index),
            "first_introduced_lesson_id": intro,
            "review_lesson_ids": sorted(reviews, key=lambda l: order.get(l, 10**6)),
            "is_assessable": any(m.get("is_assessable", True) for m in mem),
            "typical_errors": _dedup([e for m in mem for e in m.get("typical_errors", [])]),
            "grants": _merge_grants([m.get("grants") or {} for m in mem]),
            "explicit_review_refs": _dedup([r for m in mem for r in m.get("explicit_review_refs", [])]),
            "provenance": "textbook",
        }
        KnowledgePoint(**kp)  # schema 校验
        canon.append(kp)
        for m in members:
            local_to_canon[m] = cid
    return canon, local_to_canon


def build_structure(local_to_canon: dict[str, str], canon: list[dict]) -> tuple[list[dict], list[dict]]:
    """books.json（Book + Units）与 lessons.json（知识点引用改写为规范 ID）。

    课时 intro 只保留「该规范知识点首次引入于本课时」者；其余（包括新旧版重复教学）改记为 practice。
    """
    first_intro = {k["id"]: k["first_introduced_lesson_id"] for k in canon}
    books, lessons = [], []
    for bid, b in load_work_books().items():
        reg = b["registry"]
        Book(**reg["book"])
        for u in reg["units"]:
            Unit(**u)
        books.append({"book": reg["book"], "units": reg["units"]})
        for l in b["lessons"]:
            intro_c = _dedup([local_to_canon[local_key(bid, k)] for k in l.get("intro_knowledge_point_ids", [])])
            prac_c = _dedup([local_to_canon[local_key(bid, k)] for k in l.get("practice_knowledge_point_ids", [])])
            new_intro = [c for c in intro_c if first_intro[c] == l["id"]]
            new_prac = _dedup([c for c in intro_c + prac_c if c not in new_intro])
            row = dict(l, intro_knowledge_point_ids=new_intro, practice_knowledge_point_ids=new_prac)
            Lesson(**row)
            lessons.append(row)
    return books, lessons


def build_extends_edges(judgments: list[dict], local_to_canon: dict[str, str]) -> list[dict]:
    best: dict[tuple[str, str], dict] = {}
    for j in judgments:
        if j.get("label") != "extends":
            continue
        narrow, wide = (j["a"], j["b"]) if j["narrower"] == "A" else (j["b"], j["a"])
        f, t = local_to_canon[narrow], local_to_canon[wide]
        if f == t:
            continue
        rec = best.get((f, t))
        ev = {"local_pair": [narrow, wide], "judgment_id": j["id"], "confidence": j["confidence"]}
        if rec is None:
            best[(f, t)] = {"judgment": j, "evidence": [ev]}
        else:
            rec["evidence"].append(ev)
            if j["confidence"] > rec["judgment"]["confidence"]:
                rec["judgment"] = j
    edges = []
    for (f, t), rec in sorted(best.items()):
        e = {
            "id": f"e.extends.{f}.{t}",
            "type": EdgeType.EXTENDS.value,
            "from_knowledge_point_id": f,
            "to_knowledge_point_id": t,
            "evidence": {"stage2_extends_judgments": rec["evidence"]},
            "judgment_id": rec["judgment"]["id"],
            "is_direct": True,
            "provenance": "textbook",
        }
        Edge(**e)
        edges.append(e)
    return edges


def write_outputs(judgments, removed, canon, local_to_canon, books, lessons, edges) -> None:
    kps = {k["_key"]: k for k in local_kps()}
    write_jsonl(JUDGMENTS_DIR / "stage2_pairwise.jsonl", judgments)
    write_json(DATA_DIR / "knowledge_points.json", canon)
    write_json(
        DATA_DIR / "kp_local_map.json",
        [
            {"book_id": kps[k]["_book"], "local_id": kps[k]["id"], "local_name": kps[k]["name"], "canonical_id": c}
            for k, c in sorted(local_to_canon.items(), key=lambda kv: (book_index(kps[kv[0]]["_book"]), kv[0]))
        ],
    )
    write_json(DATA_DIR / "books.json", books)
    write_json(DATA_DIR / "lessons.json", lessons)
    write_json(DATA_DIR / "edges_extends.json", edges)
    write_json(JUDGMENTS_DIR / "stage2_cluster_log.json", {"removed_same_edges": removed})
