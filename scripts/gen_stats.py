"""生成 reports/stats.md：各书的单元/课时/知识点/题型/实例数量与知识图结构统计（Stage 8，派生物，不手工编辑）。

用法：python scripts/gen_stats.py
只读 data/，数据变化后重跑即可。
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chalkbase.common import DATA_DIR, ROOT, book_of, book_sequence, lesson_order, read_json  # noqa: E402

OUT = ROOT / "reports" / "stats.md"
DOMAINS = {"na": "数与代数", "gg": "图形与几何", "sp": "统计与概率", "ip": "综合与实践"}
VTYPES = ["program", "rule", "human"]


def table(header: list[str], rows: list[list]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return out + [""]


def longest_path(nodes: set[str], edges: list[tuple[str, str]]) -> tuple[int, list[str]]:
    succ, indeg = defaultdict(list), Counter()
    for a, b in edges:
        succ[a].append(b)
        indeg[b] += 1
    order, stack = [], [n for n in nodes if indeg[n] == 0]
    while stack:
        n = stack.pop()
        order.append(n)
        for m in succ[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                stack.append(m)
    assert len(order) == len(nodes), "prerequisite 子图有环"
    dist, prev = {n: 0 for n in nodes}, {}
    for n in order:
        for m in succ[n]:
            if dist[n] + 1 > dist[m]:
                dist[m], prev[m] = dist[n] + 1, n
    end = max(dist, key=dist.get)
    path = [end]
    while path[-1] in prev:
        path.append(prev[path[-1]])
    return dist[end], path[::-1]


def components(nodes: set[str], edges: list[tuple[str, str]]) -> int:
    parent = {n: n for n in nodes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        parent[find(a)] = find(b)
    return len({find(n) for n in nodes})


def main() -> None:
    books = {b["book"]["id"]: b for b in read_json(DATA_DIR / "books.json")}
    lessons = read_json(DATA_DIR / "lessons.json")
    kps = read_json(DATA_DIR / "knowledge_points.json")
    kp_by_id = {k["id"]: k for k in kps}
    local_map = read_json(DATA_DIR / "kp_local_map.json")
    exercises = read_json(DATA_DIR / "exercises.json")
    archetypes = read_json(DATA_DIR / "archetypes.json")
    edges = read_json(DATA_DIR / "edges_relations.json")
    extends = read_json(DATA_DIR / "edges_extends.json")
    contexts = read_json(DATA_DIR / "contexts.json")
    glossary = read_json(DATA_DIR / "glossary.json")
    boundaries = read_json(DATA_DIR / "boundaries.json")
    order = lesson_order()

    per_book = defaultdict(Counter)
    for bid in book_sequence():
        per_book[bid]["units"] = len(books[bid]["units"])
    for l in lessons:
        per_book[book_of(l["id"])]["lessons"] += 1
    for r in local_map:
        per_book[r["book_id"]]["local_kps"] += 1
    for kp in kps:
        per_book[book_of(kp["first_introduced_lesson_id"])]["canonical_kps"] += 1
    for e in exercises:
        per_book[book_of(e["id"])]["exercises"] += 1
    ex_book = {e["id"]: book_of(e["id"]) for e in exercises}
    for a in archetypes:
        src = a.get("source_instance_ids") or []
        if src:
            per_book[Counter(ex_book[i] for i in src).most_common(1)[0][0]]["archetypes"] += 1
        else:
            per_book["（补全，无教材实例）"]["archetypes"] += 1

    out = ["# 统计概览（脚本生成，勿手工编辑：`python scripts/gen_stats.py`）", ""]
    out += ["## 各书数量", "", "规范知识点按其**引入课时**所在的书统计；题型按其源实例中最多的那本书统计。", ""]
    rows, tot = [], Counter()
    for bid in list(book_sequence()) + ["（补全，无教材实例）"]:
        c = per_book[bid]
        if bid not in books and not c:
            continue
        ed = books[bid]["book"]["curriculum_standard_year"] if bid in books else "—"
        rows.append([bid, ed, c["units"] or "—", c["lessons"] or "—", c["local_kps"] or "—", c["canonical_kps"] or "—", c["exercises"] or "—", c["archetypes"]])
        tot.update(c)
    rows.append(["**合计**", "", tot["units"], tot["lessons"], tot["local_kps"], tot["canonical_kps"], tot["exercises"], tot["archetypes"]])
    out += table(["书", "课标", "单元", "课时", "局部知识点", "规范知识点（引入）", "习题实例", "题型"], rows)

    out += ["## 规范知识点", ""]
    by_dom = Counter(k["domain"] for k in kps)
    by_prov = Counter(k["provenance"] for k in kps)
    by_mastery = Counter(k["mastery_level"] for k in kps)
    out += [f"- 总数 {len(kps)}（局部 {len(local_map)} → 规范 {len(kps)}，教材来源 {by_prov.get('textbook', 0)}，补全 {by_prov.get('reconciled', 0)}）",
            f"- 领域：" + "，".join(f"{DOMAINS[d]} {by_dom[d]}" for d in DOMAINS),
            f"- 掌握层级：" + "，".join(f"{m} {n}" for m, n in sorted(by_mastery.items())),
            f"- 可直接考查（is_assessable）：{sum(1 for k in kps if k['is_assessable'])}", ""]
    out += ["各学期引入的规范知识点数：", ""]
    by_sem = Counter(book_of(k["first_introduced_lesson_id"]) for k in kps)
    out += table(["书"] + list(book_sequence()), [["引入数"] + [by_sem[b] for b in book_sequence()]])

    out += ["## 题型", ""]
    vt = Counter(a["verifiable_type"] for a in archetypes)
    n_src = Counter(len(a.get("source_instance_ids") or []) for a in archetypes)
    covered = {i for a in archetypes for i in a.get("source_instance_ids") or []}
    out += [f"- 总数 {len(archetypes)}；习题实例 {len(exercises)}，被题型覆盖 {len(covered)}；压缩比 {len(exercises) / max(1, len(archetypes) - n_src[0]):.2f}",
            "- 可验证类型：" + "，".join(f"{t} {vt[t]}" for t in VTYPES),
            f"- 单实例题型 {n_src[1]}（{n_src[1] / len(archetypes):.1%}）；无教材实例（补全）{n_src[0]}",
            "- 难度分布（1～5）：" + "，".join(f"{d}级 {n}" for d, n in sorted(Counter(a['difficulty'] for a in archetypes).items())),
            "- 题目形式：" + "，".join(f"{f} {n}" for f, n in Counter(a["item_form"] for a in archetypes).most_common()),
            f"- 情境库 {len(contexts)} 类；表述规范 {len(glossary)} 条", ""]

    out += ["## 关系图", ""]
    et = Counter(e["type"] for e in edges)
    out += ["- 边类型：" + "，".join(f"{t} {n}" for t, n in et.most_common()) + f"；螺旋扩展（extends）{len(extends)}"]
    prereq = [e for e in edges if e["type"] == "prerequisite"]
    direct = [e for e in prereq if e.get("is_direct", True)]
    nodes = set(kp_by_id)
    pairs = [(e["from_knowledge_point_id"], e["to_knowledge_point_id"]) for e in direct]
    depth, path = longest_path(nodes, pairs)
    indeg, outdeg = Counter(b for _, b in pairs), Counter(a for a, _ in pairs)
    reversed_n = sum(1 for e in prereq if order[kp_by_id[e["from_knowledge_point_id"]]["first_introduced_lesson_id"]] > order[kp_by_id[e["to_knowledge_point_id"]]["first_introduced_lesson_id"]])
    out += [f"- 前置边 {len(prereq)}：直接 {len(direct)}，隐含（被传递约简）{len(prereq) - len(direct)}；无环；最长直接前置链 {depth + 1} 个知识点",
            f"- 直接前置图：{components(nodes, pairs)} 个弱连通分量；无前置的起点 {sum(1 for n in nodes if indeg[n] == 0)}，无后继的终点 {sum(1 for n in nodes if outdeg[n] == 0)}；平均入度 {len(pairs) / len(nodes):.2f}",
            f"- 违反自洽条件（前置晚于后继引入）的前置边：{reversed_n}",
            "- 最长链：" + " → ".join(kp_by_id[k]["name"] for k in path), ""]

    out += ["## 能力边界", ""]
    bl = boundaries["boundaries"]
    out += [f"- 已建边界的课时：{len(bl)}/{len(lessons)}", f"- 能力维度：{', '.join(k for k in next(iter(bl.values())).keys()) if bl else '—'}", ""]
    OUT.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
