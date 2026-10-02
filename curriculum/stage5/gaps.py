"""Stage 5 · 缺口补全：汇总两路缺口来源 → 规范化为缺口知识点 → 与现有知识点建立前置边。

来源：(1) discover.py：逐知识点枚举被假定已学却不在知识库中的先备概念；
      (2) standard.py（pre）：课标条目未被（完整）覆盖的要点。
步骤：consolidate（去重、定义、判定「缺口 / 不适用」）→ link（筛选 + 逐对判定前置边）。
产物（均为模型判定的记录，供 `python -m curriculum.stage5` 确定性地应用）：
  data/judgments/stage5_gap_spec.json     缺口知识点定义 + 边 + 课标条目不适用判定
  data/judgments/stage5_gap_link.jsonl    筛选与逐对判定的 Judgment
"""
from __future__ import annotations

import json
import re

from curriculum.annotate.client import AnnotationClient
from curriculum.annotate.gold import ModelConfig, call_models, judgment_record
from curriculum.common import DATA_DIR, book_of, lesson_order, read_json, write_json, write_jsonl
from curriculum.stage4.build import PREREQ_TASK, edge_type
from curriculum.stage4.build import validate as validate_pair
from curriculum.stage5 import discover, standard

SPEC = DATA_DIR / "judgments" / "stage5_gap_spec.json"
LINK = DATA_DIR / "judgments" / "stage5_gap_link.jsonl"
PIPELINE = ModelConfig("qwen3.7-plus", False, max_tokens=2048)
PAIR = ModelConfig("qwen3.7-plus", False, max_tokens=1024)

GROUP_SYSTEM = """你是小学数学课程专家，正在为一套「北师大版小学数学 1～6 年级」知识库补全缺口。知识库由新版（2022 课标）与旧版（2011 课标）教材混合整理，有些内容在手头教材里没有任何一本教过。
下面给出两类原始缺口线索：
 D 类：某个知识点的先备概念，被模型判断为知识库里没有；
 S 类：2022 版课标的内容要求里，没有被知识库完整覆盖的要点。
你的任务：
1. 把指向同一内容的线索归为一组，每组对应一个**缺口知识点**。缺口知识点必须是小学数学里独立、可考查的知识点（老师会当成一个考点），粒度与教材知识点相当（如「分数的初步认识」「长方形和正方形的面积公式」，不要一句话一个，也不要把整个单元并成一个）。
2. 判定哪些线索**不适用**（n/a）：过程性/素养性表述（如「感悟…」「解释结果的实际意义」）没有可单独考查的知识内容；纯历史文化背景无数学知识；线索本身不成立（数学内容显然已属教材已有范围，只是措辞不同）。n/a 必须写一句理由。能落成具体知识点的，一律成组，不要滥用 n/a。
只输出 JSON：{"groups": [{"name": "缺口知识点的规范名称", "source_ids": ["线索编号", ...]}], "not_applicable": [{"source_id": "...", "reason": "..."}]}。每条线索必须出现在某个组或 not_applicable 中，不得遗漏、不得重复。"""

DEFINE_SYSTEM = """你是小学数学课程专家，正在为一套「北师大版小学数学 1～6 年级」知识库补全一个缺口知识点（教材中没有任何一本教过，但后续内容或课标要求它）。
根据给出的缺口名称和线索，写出该知识点的定义，只输出 JSON，字段如下：
- name：规范名称；slug：英文小写下划线短标识（只含 a-z、0-9、下划线）；
- domain（na 数与代数 / gg 图形与几何 / sp 统计与概率 / ip 综合与实践）、topic（课标主题）、thread（主线；尽量从给定的已有主线中选，实在没有再新定一个中文短名）；
- description：该学会什么，一到两句话（自己的话）；
- mastery_level：know/understand/master/apply；aliases：别名列表（可空）；typical_errors：1～2 个典型错误；
- grants：学会后获得的能力增量，字段只能是 integer_domain_max（整数或 null）、decimal_max_places（整数或 null）、fraction_types（字符串列表）、operation_operand_forms（{运算名: [操作数形态]}）、concepts（字符串列表）、units_of_measure（字符串列表）、geometry_vocab（字符串列表）；没有增量的字段给空值；
- stage_hint：课标学段 s1（1～2 年级）/s2（3～4 年级）/s3（5～6 年级）；
- archetype_directions：2～4 条建议的题型方向（如「已知长和宽求长方形面积」「已知面积和一边求另一边」，是方向描述，不是题目）。"""


def _threads() -> list[str]:
    kps = read_json(DATA_DIR / "knowledge_points.json")
    seen = sorted({(k["domain"], k["topic"], k["thread"]) for k in kps if k["provenance"] == "textbook"})
    return [f"{d} / {t} / {th}" for d, t, th in seen]


def raw_sources() -> list[dict]:
    """两路原始线索，编号 D001.. / S:<课标条目 id>。"""
    kps = {k["id"]: k for k in read_json(DATA_DIR / "knowledge_points.json")}
    out = []
    n = 0
    for c in discover.candidates():
        if c.get("nearest_existing"):
            continue
        n += 1
        out.append({"id": f"D{n:03d}", "kind": "D", "text": f"知识点「{kps[c['target']]['name']}」需要先备概念「{c['name']}」：{c.get('description', '')}（{c.get('why_needed', '')}）",
                    "target": c["target"], "name": c["name"], "judgment_id": c["judgment_id"]})
    items = {i["id"]: i for i in standard.load_standard()}
    for j in standard.load_coverage("pre"):
        if j["verdict"] == "covered":
            continue
        it = items[j["item_id_std"]]
        note = f"；限定：{it['note']}" if it.get("note") else ""
        out.append({"id": f"S:{it['id']}", "kind": "S", "text": f"课标条目「{it['text']}」（{standard.STAGE_NAME[it['stage']]}）{note}，未覆盖的要点：{j['missing']}",
                    "std_item": it["id"], "judgment_id": j["id"], "stage": it["stage"]})
    return out


def _validate_groups(d) -> bool:
    return (isinstance(d, dict) and isinstance(d.get("groups"), list) and isinstance(d.get("not_applicable"), list)
            and all(isinstance(g, dict) and g.get("name") and isinstance(g.get("source_ids"), list) and g["source_ids"] for g in d["groups"]))


def _validate_def(d) -> bool:
    return (isinstance(d, dict) and all(d.get(k) for k in ("name", "slug", "domain", "thread", "description"))
            and d["domain"] in ("na", "gg", "sp", "ip") and bool(re.fullmatch(r"[a-z0-9_]+", d["slug"])))


def _normalise(d: dict) -> dict:
    """模型有时把「领域 / 主题 / 主线」整串写进 thread；统一取最后一段，并用已有主线的主题补全 topic。"""
    d = dict(d)
    d["thread"] = d["thread"].split("/")[-1].strip()
    topics = {(k["domain"], k["thread"]): k["topic"] for k in read_json(DATA_DIR / "knowledge_points.json") if k["provenance"] == "textbook"}
    d["topic"] = topics.get((d["domain"], d["thread"])) or d.get("topic") or ("综合与实践" if d["domain"] == "ip" else "数与运算")
    return d


def consolidate(client: AnnotationClient) -> dict:
    src = raw_sources()
    by_id = {s["id"]: s for s in src}
    user = "【原始缺口线索】\n" + "\n".join(f"{s['id']}：{s['text']}" for s in src) + "\n\n只输出 JSON。"
    res = call_models(client, GROUP_SYSTEM, [("group", user)], PIPELINE, _validate_groups, role="gapgroup")["group"]
    if not res.ok:
        raise SystemExit(f"缺口分组失败：{res.error}")
    d = res.parsed
    seen: dict[str, int] = {}
    for g in d["groups"]:
        for x in g["source_ids"]:
            seen[x] = seen.get(x, 0) + 1
    for x in d["not_applicable"]:
        seen[x["source_id"]] = seen.get(x["source_id"], 0) + 1
    bad = {i for i in set(by_id) | set(seen) if seen.get(i, 0) != 1 or i not in by_id}
    if bad:
        raise SystemExit(f"分组遗漏/重复/未知线索：{sorted(bad)}")
    judgments = [judgment_record("other", "gapgroup", "pipeline_gap_grouping", res, f"缺口分组：{len(src)} 条线索", f"{len(d['groups'])} 组，{len(d['not_applicable'])} 条不适用")]
    threads = "\n【已有主线（领域 / 主题 / 主线）】\n" + "\n".join(_threads())
    msgs = [(f"def{i:02d}", f"缺口名称：{g['name']}\n线索：\n" + "\n".join(f"- {by_id[x]['text']}" for x in g["source_ids"]) + "\n\n只输出 JSON。") for i, g in enumerate(d["groups"])]
    rd = call_models(client, DEFINE_SYSTEM + threads, msgs, PIPELINE, _validate_def, role="gapdef")
    gaps = []
    for i, g in enumerate(d["groups"]):
        r = rd[f"def{i:02d}"]
        if not r.ok:
            raise SystemExit(f"缺口 {g['name']} 定义失败：{r.error}")
        judgments.append(judgment_record("other", f"gapdef.{i:02d}", "pipeline_gap_definition", r, f"缺口定义：{g['name']}", json.dumps(r.parsed, ensure_ascii=False)[:400]))
        gaps.append({**_normalise(r.parsed), "source_ids": g["source_ids"], "judgment_id": judgments[-1]["id"]})
    ids = [gap_id(g) for g in gaps]
    if len(set(ids)) != len(ids):
        raise SystemExit(f"缺口 ID 重复：{[i for i in ids if ids.count(i) > 1]}")
    spec = {"sources": src, "gaps": gaps, "not_applicable": d["not_applicable"], "edges": []}
    write_json(SPEC, spec)
    write_jsonl(DATA_DIR / "judgments" / "stage5_gap_consolidation.jsonl", judgments)
    return spec


CURATE_SYSTEM = """你是小学数学课程专家，正在审查一个拟补全的「缺口知识点」是否真的是缺口。知识库已有 419 个来自教材的知识点。
给出拟补全的缺口知识点、与它文字最相似的几个现有知识点，以及其他拟补全缺口的名称列表。判定 action：
- keep：它是独立、可考查的小学数学知识点，现有知识点没有实质覆盖它，也没有与其他缺口重复。
- duplicate_existing：现有知识点（在 target 中填其编号）已经实质覆盖了它的全部内容（名称不同、年级不同都算）；部分相关不算。
- merge_into：它与另一个拟补全缺口内容重叠，应合并进去（在 target 填被并入缺口的序号，如 "G07"；被并入的应是范围更完整的那个）。
- not_math：不属于小学数学知识点（如语文拼音）或只是一句话的小提示。
只输出 JSON：{"action": "keep|duplicate_existing|merge_into|not_math", "target": "编号或null", "confidence": 0.0~1.0, "reason": "不超过60字"}"""


def _similar_existing(gaps: list[dict], top: int = 6) -> list[list[dict]]:
    import numpy as np
    from sklearn.feature_extraction.text import TfidfVectorizer

    kps = [k for k in read_json(DATA_DIR / "knowledge_points.json") if k["provenance"] == "textbook"]
    docs = [f"{k['name']} {' '.join(k['aliases'])} {k['description']}" for k in kps]
    gdocs = [f"{g['name']} {g['description']}" for g in gaps]
    vec = TfidfVectorizer(analyzer="char", ngram_range=(1, 2)).fit(docs + gdocs)
    sim = (vec.transform(gdocs) @ vec.transform(docs).T).toarray()
    return [[kps[j] for j in np.argsort(-row)[:top]] for row in sim]


def curate(client: AnnotationClient) -> dict:
    """缺口定义之后的去重/去伪：与现有知识点重复、彼此重叠、非数学的缺口剔除或合并。"""
    spec = read_json(SPEC)
    gaps = spec["gaps"]
    sims = _similar_existing(gaps)
    existing_code = {}
    msgs = []
    for i, (g, sim) in enumerate(zip(gaps, sims)):
        codes = {f"E{n}": k["id"] for n, k in enumerate(sim)}
        existing_code[i] = codes
        others = "\n".join(f"G{j:02d}：{h['name']}——{h['description'][:40]}" for j, h in enumerate(gaps) if j != i)
        txt = (f"【拟补全缺口 G{i:02d}】\n名称：{g['name']}\n描述：{g['description']}\n\n【最相似的现有知识点】\n"
               + "\n".join(f"E{n}：{k['name']}（{book_of(k['first_introduced_lesson_id'])}）——{k['description'][:70]}" for n, k in enumerate(sim))
               + f"\n\n【其他拟补全缺口】\n{others}\n\n只输出 JSON。")
        msgs.append((f"cur{i:02d}", txt))
    val = lambda d: isinstance(d, dict) and d.get("action") in ("keep", "duplicate_existing", "merge_into", "not_math")
    res = call_models(client, CURATE_SYSTEM, msgs, PIPELINE, val, role="gapcurate")
    kept, rejected, judgments = [], [], []
    for i, g in enumerate(gaps):
        r = res[f"cur{i:02d}"]
        p = r.parsed if r.ok else {"action": "keep", "target": None, "reason": "判定失败，保守保留"}
        tgt = p.get("target")
        if p["action"] == "duplicate_existing":
            tgt = existing_code[i].get(tgt) if tgt else None
            if not tgt:
                p["action"] = "keep"
        if p["action"] == "merge_into":
            try:
                j = int(str(tgt).lstrip("G"))
            except ValueError:
                j = -1
            if not (0 <= j < len(gaps)) or j == i:
                p["action"] = "keep"
            tgt = j
        judgments.append(judgment_record("other", f"gapcurate.{i:02d}", "pipeline_gap_curation", r, f"缺口审查：{g['name']}", json.dumps(p, ensure_ascii=False)))
        rec = {**g, "curation": {"action": p["action"], "target": tgt, "reason": p.get("reason", ""), "judgment_id": judgments[-1]["id"]}}
        (kept if p["action"] == "keep" else rejected).append(rec)
    # 被并入的缺口：线索并入目标（目标本身若也被并走，则不并，保守保留）
    kept_names = {gaps[i]["name"] for i in range(len(gaps)) if any(k["name"] == gaps[i]["name"] for k in kept)}
    for rec in list(rejected):
        c = rec["curation"]
        if c["action"] == "merge_into":
            tgt = gaps[c["target"]]
            host = next((k for k in kept if k["name"] == tgt["name"]), None)
            if host is None:
                rejected.remove(rec)
                kept.append({**rec, "curation": {**c, "action": "keep", "reason": "合并目标未保留，保守保留：" + c["reason"]}})
            else:
                host["source_ids"] = host["source_ids"] + rec["source_ids"]
    spec["gaps"] = [{k: v for k, v in g.items()} for g in kept]
    spec["rejected_gaps"] = rejected
    write_json(SPEC, spec)
    write_jsonl(DATA_DIR / "judgments" / "stage5_gap_curation.jsonl", judgments)
    return {"kept": len(kept), "rejected": {a: sum(1 for r in rejected if r["curation"]["action"] == a) for a in {r["curation"]["action"] for r in rejected}}}


# ------------------------------------------------------------------ 与现有知识点建立前置边


def render_card(k: dict) -> str:
    intro = f"首次引入：{book_of(k['first_introduced_lesson_id'])}" if k.get("first_introduced_lesson_id") else "首次引入：（教材中没有，待补全）"
    return "\n".join([
        f"名称：{k['name']}",
        f"领域/主题/主线：{k['domain']} / {k['topic']} / {k['thread']}",
        intro,
        f"描述：{k['description']}",
    ])


def gap_id(g: dict) -> str:
    return f"kp.{g['domain']}.{g['thread']}.{g['slug']}"


def gap_kp_stub(g: dict) -> dict:
    return {"id": gap_id(g), "name": g["name"], "domain": g["domain"], "topic": g.get("topic", ""),
            "thread": g["thread"], "description": g["description"], "first_introduced_lesson_id": None}


SCREEN_FWD = """
---
## 本次任务的形式（筛选直接依赖者）

下面给出一个「基础知识点 A」（它在教材中没有被教过，是补全的缺口）和知识库全部知识点清单（编号、首次引入的书、名称、主线）。请浏览**整个清单**，找出所有**直接建立在 A 之上**的知识点 B：不掌握 A 就学不会 B，且 A 是 B 直接依赖的那一层。
不要受「引入书」先后的限制。本步只是初筛，宁多勿漏；不相关的不要列。
只输出 JSON：{"dependents": ["编号", ...], "confidence": 0.0~1.0, "reason": "不超过80字"}
"""
SCREEN_BWD = """
---
## 本次任务的形式（筛选直接前置）

下面给出一个「目标知识点 B」（它在教材中没有被教过，是补全的缺口）和知识库全部知识点清单（编号、首次引入的书、名称、主线）。请浏览**整个清单**，找出 B 的全部**直接前置**知识点：不掌握它就学不会 B，而且它是 B 直接建立在其上的那一层（不必列出前置的前置）。
不要受「引入书」先后的限制。本步只是初筛，宁多勿漏；不相关的不要列。
只输出 JSON：{"prerequisites": ["编号", ...], "confidence": 0.0~1.0, "reason": "不超过80字"}
"""


def link(client: AnnotationClient) -> dict:
    spec = read_json(SPEC)
    kps = {k["id"]: k for k in read_json(DATA_DIR / "knowledge_points.json") if k["provenance"] == "textbook"}
    order = lesson_order()
    code2id, lines = {}, []
    for i, k in enumerate(sorted(kps, key=lambda x: (order[kps[x]["first_introduced_lesson_id"]], x))):
        code2id[f"K{i:03d}"] = k
        lines.append(f"K{i:03d} | {book_of(kps[k]['first_introduced_lesson_id'])} | {kps[k]['name']} | {kps[k]['thread']}")
    listing = "\n".join(lines)
    gaps = {gap_id(g): g for g in spec["gaps"]}
    msgs = [(gid, f"【知识点】\n{render_card(gap_kp_stub(g))}\n\n【全部其他知识点清单】\n{listing}\n\n只输出 JSON。") for gid, g in gaps.items()]
    vf = lambda d: isinstance(d, dict) and isinstance(d.get("dependents"), list)
    vb = lambda d: isinstance(d, dict) and isinstance(d.get("prerequisites"), list)
    rf = call_models(client, PREREQ_TASK.system_prompt() + SCREEN_FWD, msgs, PIPELINE, vf, role="gaplink_fwd")
    rb = call_models(client, PREREQ_TASK.system_prompt() + SCREEN_BWD, msgs, PIPELINE, vb, role="gaplink_bwd")
    judgments, pairs = [], {}
    for gid in gaps:
        for res, key, role, fwd in ((rf[gid], "dependents", "fwd", True), (rb[gid], "prerequisites", "bwd", False)):
            picked = [code2id[c] for c in (res.parsed or {}).get(key, []) if c in code2id] if res.ok else []
            judgments.append(judgment_record("prerequisite_judgment", f"gaplink_{role}.{gid}", f"pipeline_gap_screen_{role}", res,
                                             f"缺口 {gid} 筛选（{role}）", json.dumps(picked, ensure_ascii=False)))
            for x in picked:
                pairs[(gid, x) if fwd else (x, gid)] = True
    # D 类线索指明的依赖者必然是候选（缺口正是因它而发现）
    for s in spec["sources"]:
        if s["kind"] == "D":
            for g in spec["gaps"]:
                if s["id"] in g["source_ids"]:
                    pairs[(gap_id(g), s["target"])] = True
    for a in gaps:  # 缺口之间也要判定（如面积单位 → 长方形面积）
        for b in gaps:
            if a != b:
                pairs[(a, b)] = True
    allk = {**kps, **{gid: gap_kp_stub(g) for gid, g in gaps.items()}}
    plist = sorted(pairs)
    pm = [(f"{a}->{b}", f"【知识点 A】\n{render_card(allk[a])}\n\n【知识点 B】\n{render_card(allk[b])}\n\n问题：不掌握 A，学生能否学会 B？请判断 A → B 的关系，只输出 JSON。") for a, b in plist]
    rp = call_models(client, PREREQ_TASK.system_prompt(), pm, PAIR, validate_pair, role="gaplink_pair")
    names = {k: v["name"] for k, v in allk.items()}
    edges = []
    for a, b in plist:
        r = rp[f"{a}->{b}"]
        j = judgment_record("prerequisite_judgment", f"{a}->{b}", "pipeline_gap_pair", r, f"{names[a]} → {names[b]}", json.dumps(r.parsed, ensure_ascii=False) if r.ok else "ERROR")
        j["id"] = f"j.s5.{a}->{b}"
        j["a"], j["b"], j["label"] = a, b, (r.parsed["label"] if r.ok else None)
        judgments.append(j)
        if not r.ok or r.parsed["label"] == "none":
            continue
        et = edge_type({"a": a, "b": b, "label": r.parsed["label"], "confidence": j["confidence"]}, names)
        edges.append({"from": a, "to": b, "type": et, "judged_label": r.parsed["label"], "judged_confidence": j["confidence"],
                      "judgment_id": j["id"], "reason": j["reasoning"]})
    # 缺口之间互为前置/递进的（如「分数的初步认识」与「分数的意义」）：保留学段较低者指向较高者的一条；同学段则两条都不要（环检测的前置复核）
    stage = {gid: g.get("stage_hint", "s2") for gid, g in gaps.items()}
    ekey = {(e["from"], e["to"]): e for e in edges}
    mutual_dropped = []
    for (a, b) in sorted(ekey):
        if not (a in gaps and b in gaps and (b, a) in ekey and a < b):
            continue
        # 学段不同：丢弃从高学段指向低学段的一条；同学段：两条都丢弃
        drop = [(a, b), (b, a)] if stage[a] == stage[b] else [(a, b)] if stage[a] > stage[b] else [(b, a)]
        for k in drop:
            e = ekey.pop(k)
            mutual_dropped.append({"from": k[0], "to": k[1], "type": e["type"], "reason": "与反向边互为前置/递进（环）：" + ("学段相同，两条都不保留" if stage[a] == stage[b] else "保留学段较低指向较高的一条")})
    edges = list(ekey.values())
    spec["edges"] = edges
    spec["mutual_edges_dropped"] = mutual_dropped
    write_json(SPEC, spec)
    write_jsonl(LINK, judgments)
    by_type: dict[str, int] = {}
    for e in edges:
        by_type[e["type"]] = by_type.get(e["type"], 0) + 1
    return {"gaps": len(gaps), "pairs": len(plist), "edges": len(edges), "by_type": by_type}
