"""构建单文件可视化 viz/index.html。

读取 data/（规范数据）、work/judgments/（边的判定理由）、work/stage5/（版本对齐记录）、reports/eval_history.jsonl（评测指标）、
eval/annotation/（金标一致性），精简后与 viz/vendor/ 里的第三方库、viz/src/ 里的源码一起装配成一个离线可打开的 HTML。

用法：python scripts/build_viz.py            构建（题型实例结果有缓存，只对新增/变更的题型重新采样）
      python scripts/build_viz.py --no-instances   不调用运行时接口，只用卡片里的改写示例（快速预览）

构建是确定性的：同样的输入得到逐字节相同的输出。习题原文（exercises.json 的 text）不进入页面。
"""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DATA = ROOT / "data"
SRC = ROOT / "viz" / "src"
VENDOR = ROOT / "viz" / "vendor"
OUT = ROOT / "viz" / "index.html"
CACHE = ROOT / ".cache" / "viz_instances.json"
N_SAMPLES = 6

DOMAINS = ["na", "gg", "sp", "ip"]
EDGE_TYPES = ["prerequisite", "builds_on", "extends", "related", "confusable"]
VT = {"program": 0, "rule": 1, "human": 2}


# ----------------------------------------------------------------------------- 读取

def read_json(p: Path):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def read_jsonl(p: Path):
    return [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]


def source_hash() -> str:
    """data/ 下全部 JSON 的内容哈希（含文件名），页面里记录它，测试用它判断页面是否由当前数据构建。"""
    h = hashlib.sha256()
    for f in sorted(DATA.glob("*.json")):
        h.update(f.name.encode())
        h.update(f.read_bytes())
    return h.hexdigest()


def data_counts() -> dict:
    kps = read_json(DATA / "knowledge_points.json")
    arch = read_json(DATA / "archetypes.json")
    rel = read_json(DATA / "edges_relations.json")
    ext = read_json(DATA / "edges_extends.json")
    return {
        "books": len(read_json(DATA / "books.json")),
        "lessons": len(read_json(DATA / "lessons.json")),
        "kps": len(kps),
        "archetypes": len(arch),
        "exercises": len(read_json(DATA / "exercises.json")),
        "edges": len(rel) + len(ext),
        "direct_prerequisite": sum(1 for e in rel if e["type"] == "prerequisite" and e["is_direct"]),
        "standard_items": read_json(DATA / "standard_coverage.json")["n_items"],
    }


# ----------------------------------------------------------------------------- 核心载荷

def build_core(cur) -> dict:
    books_raw = read_json(DATA / "books.json")
    kps = read_json(DATA / "knowledge_points.json")
    arch = read_json(DATA / "archetypes.json")
    rel = read_json(DATA / "edges_relations.json")
    ext = read_json(DATA / "edges_extends.json")
    bnd = read_json(DATA / "boundaries.json")
    std = read_json(DATA / "standard_coverage.json")
    ex_n = len(read_json(DATA / "exercises.json"))

    lesson_ids = cur.lesson_ids()
    lidx = {l: i for i, l in enumerate(lesson_ids)}
    assert lesson_ids == bnd["lessons"], "课时全序与 boundaries.json 不一致"

    # --- 书 / 单元 / 课时
    book_ids = [b["book"]["id"] for b in books_raw]
    order = ["g1a", "g1b", "g2a", "g2b", "g3a", "g3b", "g4a", "g4b", "g5a", "g5b", "g6a", "g6b"]
    assert sorted(book_ids) == sorted(order)
    bmap = {b["book"]["id"]: b for b in books_raw}
    books, units, ulookup = [], [], {}
    for bi, bid in enumerate(order):
        b = bmap[bid]
        books.append({"id": bid, "g": b["book"]["grade"], "s": b["book"]["semester"], "std": b["book"]["curriculum_standard_year"]})
        for u in b["units"]:
            ulookup[u["id"]] = len(units)
            units.append({"id": u["id"], "t": u["title"], "b": bi, "ty": u["unit_type"]})
    lessons, lu = [], []
    for lid in lesson_ids:
        l = cur.lesson(lid)
        lessons.append([lid, l.title])
        lu.append(ulookup[l.unit_id])
    for ui, u in enumerate(units):
        idx = [i for i, x in enumerate(lu) if x == ui]
        u["l0"], u["l1"] = (min(idx), max(idx)) if idx else (None, None)

    # --- 知识点
    kidx = {k["id"]: i for i, k in enumerate(kps)}
    by_kp = defaultdict(list)
    for a in arch:
        by_kp[a["primary_knowledge_point_id"]].append(a)
    kp_out = []
    for k in kps:
        al = by_kp.get(k["id"], [])
        kp_out.append({
            "id": k["id"], "n": k["name"], "al": k["aliases"], "d": k["domain"], "tp": k["topic"], "th": k["thread"],
            "de": k["description"], "m": k["mastery_level"], "l": lidx[k["first_introduced_lesson_id"]],
            "rv": sorted(lidx[x] for x in k["review_lesson_ids"] if x in lidx), "as": 1 if k["is_assessable"] else 0,
            "te": k["typical_errors"], "g": k["grants"], "pv": 1 if k["provenance"] != "textbook" else 0,
            "pn": k.get("provenance_note") or "",
            "na": len(al), "ad": round(sum(a["difficulty"] for a in al) / len(al), 2) if al else 0,
            "vt": [sum(1 for a in al if a["verifiable_type"] == t) for t in VT],
        })

    # --- 边
    j4 = {r["id"]: r for r in read_jsonl(ROOT / "work/judgments/stage4_relations.jsonl")}
    j2 = {r["id"]: r for r in read_jsonl(ROOT / "work/judgments/stage2_pairwise.jsonl")}
    edges = []
    for e in sorted(rel + ext, key=lambda e: (EDGE_TYPES.index(e["type"]), e["from_knowledge_point_id"], e["to_knowledge_point_id"])):
        ev = e["evidence"]
        rec = {"f": kidx[e["from_knowledge_point_id"]], "t": kidx[e["to_knowledge_point_id"]], "k": EDGE_TYPES.index(e["type"]),
               "d": 1 if e["is_direct"] else 0}
        if e["type"] == "extends":
            js = ev.get("stage2_extends_judgments") or []
            if js:
                r = j2.get(js[0]["judgment_id"], {})
                rec["c"] = round(js[0].get("confidence") or 0, 2)
                rec["r"] = r.get("reasoning") or ""
                rec["s"] = 2
        else:
            r = j4.get(e["judgment_id"])
            rec["c"] = round(ev.get("judged_confidence") or 0, 2)
            rec["lb"] = ev.get("judged_label")
            if r:
                rec["r"] = r.get("reasoning") or ""
                rec["s"] = 4
            s5 = ev.get("stage5")
            if s5:
                rec["r"] = s5.get("reason") or rec.get("r", "")
                rec["s"] = 5
                rec["s5"] = s5.get("action")
            if e["provenance"] == "reconciled" and e.get("provenance_note"):
                rec["pn"] = e["provenance_note"]
            compact = {}
            for src, dst in (("cooccurrence_count", "c"), ("cooccurrence_ratio", "r"), ("b_instances", "bi"), ("reverse_cooccurrence_count", "rc"),
                             ("semester_gap", "gap"), ("routes", "ro")):
                if src in ev:
                    compact[dst] = ev[src]
            for src, dst in (("same_thread", "st"), ("same_topic", "sp"), ("thread_adjacent", "ta"), ("order_conflict", "oc")):
                if src in ev:
                    compact[dst] = 1 if ev[src] else 0
            if ev.get("implied_via"):
                compact["iv"] = [kidx[x] for x in ev["implied_via"]]
            rec["e"] = compact
        edges.append(rec)

    # --- 能力边界时间轴
    tl = build_timeline(bnd, lesson_ids, lidx, kp_out)

    # --- 课标覆盖
    std_items = []
    for it in std["items"]:
        row = {"id": it["item_id"], "s": it["stage"], "d": it["domain"], "t": it["text"], "st": it["status"], "v": it.get("via"),
               "k": [kidx[x] for x in it.get("kp_ids", []) if x in kidx]}
        if it["status"] != "covered" and it.get("reason"):
            row["rs"] = it["reason"]
        std_items.append(row)

    # --- 评测
    ev = build_eval(kidx, lidx)

    # --- 统计
    vt_diff = [[0] * 5 for _ in VT]
    forms = Counter()
    for a in arch:
        vt_diff[VT[a["verifiable_type"]]][a["difficulty"] - 1] += 1
        forms[a["item_form"]] += 1
    stats = {"vt_diff": vt_diff, "forms": dict(forms), "exercises": ex_n,
             "edge_counts": [{"k": EDGE_TYPES[k], "d": d, "n": n} for (k, d), n in sorted(Counter((e["k"], e["d"]) for e in edges).items())]}

    return {
        "meta": {"counts": data_counts(), "sha": source_hash(), "eval_ts": ev["timestamp"]},
        "books": books, "units": units, "lessons": lessons, "lu": lu, "kps": kp_out, "edges": edges,
        "tl": tl, "std": std_items, "eval": ev, "stats": stats, "oos": build_oos(cur, lesson_ids, lidx),
    }


def build_timeline(bnd, lesson_ids, lidx, kp_out) -> dict:
    n = len(lesson_ids)
    im, dp = [], []
    add = {}
    for i, lid in enumerate(lesson_ids):
        row = bnd["boundaries"][lid]
        im.append(row["integer_domain_max"] or 0)
        dp.append(row["decimal_max_places"] or 0)
        a = row["add"]
        rec = {}
        if a.get("fraction_types"):
            rec["f"] = a["fraction_types"]
        if a.get("operation_operand_forms"):
            rec["o"] = {op: forms for op, forms in a["operation_operand_forms"].items() if forms}
        if a.get("units_of_measure"):
            rec["u"] = a["units_of_measure"]
        if a.get("geometry_vocab"):
            rec["g"] = a["geometry_vocab"]
        if a.get("concepts"):
            rec["c"] = a["concepts"][:6]
            rec["nc"] = len(a["concepts"])
        if rec:
            add[i] = rec
    return {"im": im, "dp": dp, "add": add, "n": n}


def build_oos(cur, lesson_ids, lidx) -> list:
    items = read_json(SRC / "oos_items.json")
    out = []
    for it in items:
        segs, last = [], None
        for i, lid in enumerate(lesson_ids):
            r = cur.check_item(it["features"], lid)
            vios = [[v.dimension, v.item_value, lidx.get(v.introduced_at) if v.introduced_at else None, v.detail] for v in r.violations]
            key = (r.verdict, json.dumps(vios, ensure_ascii=False))
            if key != last:
                segs.append([i, {"in": 0, "borderline": 1, "out": 2}[r.verdict], vios])
                last = key
        out.append({"id": it["id"], "t": it["text"], "tag": it["tag"], "segs": segs})
    return out


# 页面上展示的评测指标：(阶段标签, 指标名, 阶段键, 指标键)
HEADLINE = [
    ("实体消解", "成对判同 F1", "stage2_entity_resolution", "pair_same_f1"),
    ("实体消解", "簇 B-cubed F1", "stage2_entity_resolution", "bcubed_f1"),
    ("题型归纳", "题型粒度合适率", "stage3_archetype_induction", "granularity_ok_rate"),
    ("题型归纳", "program 题型可验证率", "stage3_archetype_induction", "generation_probe_program_rate"),
    ("关系推断", "前置闭包 F1", "stage4_relation_inference", "closure_f1"),
    ("关系推断", "候选边召回", "stage4_relation_inference", "candidate_recall"),
    ("版本对齐", "课标内容要求覆盖", "stage5_reconciliation", "standard_coverage"),
    ("版本对齐", "补全题型审阅通过率", "stage5_reconciliation", "reconciled_review_pass_rate"),
    ("能力边界", "教材习题在其课时边界内", "stage6_capability_boundary", "教材实例在所在课时边界内的比例（补全+修复后）"),
    ("能力边界", "生成探针边界通过率", "stage6_capability_boundary", "生成探针边界通过率（program 类 × 20 采样）"),
    ("能力边界", "越界探针查准（意图一致）", "stage6_capability_boundary", "越界探针·端到端(B)·意图一致子集·decided查准"),
    ("能力边界", "越界探针查全（意图一致）", "stage6_capability_boundary", "越界探针·端到端(B)·意图一致子集·decided查全"),
    ("查询接口", "检索 recall@5", "stage7_query_interface", "recall@5（封顶）"),
    ("查询接口", "检索 MRR", "stage7_query_interface", "MRR"),
]

KAPPA_NAMES = {
    "entity_resolution": "实体消解（成对）", "entity_resolution_anchor": "实体消解（锚点）",
    "prerequisite": "前置关系（成对）", "prerequisite_anchor": "前置关系（锚点）", "prerequisite_anchor/holdout": "前置关系（验收集）",
    "archetype_granularity": "题型粒度（v2 指南）", "boundary_probe": "越界探针",
    "retrieval_probe": "检索探针", "retrieval_probe_holdout": "检索探针（验收集）",
}
GOLD_NAMES = {"pr": "前置关系", "er": "实体消解（成对）", "rp": "检索探针", "ag": "题型粒度（旧指南）", "ea": "实体消解（锚点）"}


def build_eval(kidx, lidx) -> dict:
    hist = read_jsonl(ROOT / "reports/eval_history.jsonl")
    rec = [r for r in hist if r.get("split") == "test"][-1]
    sm = rec["stage_metrics"]
    metrics = []
    for stage, name, sk, mk in HEADLINE:
        m = sm[sk]["headline"][mk]
        metrics.append({"stage": stage, "name": name, "v": m["value"], "ci": m.get("ci"), "n": m.get("n"), "base": m.get("baseline"),
                        "thr": m.get("threshold") if isinstance(m.get("threshold"), (int, float)) else None, "ok": m.get("pass")})
    kappa = []
    for f in sorted((ROOT / "eval/annotation").glob("*/stats*.json")):
        key = f.parent.name + ("/holdout" if "holdout" in f.name else "")
        if key not in KAPPA_NAMES:
            continue
        s = read_json(f)
        kappa.append({"name": KAPPA_NAMES[key], "n": s.get("n", s.get("n_probes")), "k": s.get("cohen_kappa", s.get("cohen_kappa_on_union")),
                      "agree": s.get("raw_agreement", s.get("raw_agreement_on_union"))})
    gold = []
    rr = ROOT / "eval/gold/review_results.json"
    if rr.exists():
        for key, v in read_json(rr)["by_task"].items():
            acc = v.get("_all", {}).get("accuracy")
            if acc and acc.get("p") is not None and key in GOLD_NAMES:
                gold.append({"name": GOLD_NAMES[key], "p": acc["p"], "lo": acc["lo"], "hi": acc["hi"], "n": acc["n"]})
    rec5 = read_json(ROOT / "work/stage5/reconciliation.json")
    s5 = sm["stage5_reconciliation"]["headline"]
    story = {
        "order_before": rec5["initial_order_conflicts"], "order_after": s5["order_violations"]["value"],
        "cycles": rec5["cycles"] and len(rec5["cycles"]) or 0,
        "gaps": [{"k": kidx[g["kp"]], "l": lidx[g["lesson"]], "how": g["how"]} for g in rec5["gaps"] if g["kp"] in kidx],
        "drops": [{"f": kidx[d["from"]], "t": kidx[d["to"]], "cat": d["category"], "r": d["reason"]} for d in rec5["edge_drops"] if d["from"] in kidx and d["to"] in kidx],
        "cov_before": rec5["coverage_before"]["covered"], "cov_n": rec5["coverage_before"]["n"],
        "closure_f1": s5["stage4_final_closure_f1"]["value"],
        "reduction": rec5["reduction"],
    }
    s1 = sm["stage1_extraction"]["headline"]
    s3 = sm["stage3_archetype_induction"]["headline"]
    pipeline = [
        {"id": "textbook", "name": "教材", "sub": "12 册 PDF", "badge": "12 册", "note": "北师大版 · 2022 课标 9 册 + 2011 课标 3 册"},
        {"id": "extract", "name": "抽取", "sub": "课时 · 习题 · 局部知识点", "badge": "4319 习题", "note": "含「未能判读」图形信息的实例仅 %.2f%%" % (100 * s1["含「未能判读」图形信息的实例占比"]["value"])},
        {"id": "resolve", "name": "实体消解", "sub": "466 局部 → 433", "badge": "F1 %.2f" % sm["stage2_entity_resolution"]["headline"]["pair_same_f1"]["value"], "note": "同一知识点跨册合并，成对判同 F1（基线 0.80）"},
        {"id": "archetype", "name": "题型归纳", "sub": "习题 → 题型卡片", "badge": "粒度合适 %.2f" % s3["granularity_ok_rate"]["value"], "note": "题型粒度合适率（基线 0.61）；program 题型 100% 可程序求解"},
        {"id": "relation", "name": "关系推断", "sub": "前置 · 递进 · 相关 · 易混淆", "badge": "闭包 F1 %.2f" % sm["stage4_relation_inference"]["headline"]["closure_f1"]["value"], "note": "前置闭包 F1（基线 0.71）；传递约简后 852 条直接前置边"},
        {"id": "reconcile", "name": "版本对齐", "sub": "新旧课标 · 缺口补全", "badge": "课标 133/133", "note": "11 条逆序前置边 → 0；补全 14 个知识点；课标内容要求 100% 覆盖"},
        {"id": "boundary", "name": "能力边界", "sub": "567 课时 × 7 维度", "badge": "通过率 %.3f" % sm["stage6_capability_boundary"]["headline"]["生成探针边界通过率（program 类 × 20 采样）"]["value"], "note": "生成探针边界通过率；教材习题 100% 在其课时边界内"},
        {"id": "query", "name": "查询接口", "sub": "检索 · 链路 · 实例化 · 校验", "badge": "recall@5 %.2f" % sm["stage7_query_interface"]["headline"]["recall@5（封顶）"]["value"], "note": "教师口吻需求检索 recall@5（基线 0.78），MRR 0.91"},
    ]
    return {"timestamp": rec["timestamp"], "metrics": metrics, "kappa": kappa, "gold": gold, "story": story, "pipeline": pipeline,
            "invariants": rec["invariants"]["summary_tail"]}


# ----------------------------------------------------------------------------- 题型载荷

def _fmt_example(e):
    return [e["problem"], e.get("answer") or "", e.get("solution") or ""]


def _card_key(a) -> str:
    """整张题型卡片内容的哈希：卡片任何字段变动，该题型的缓存实例即失效。"""
    return hashlib.sha256(json.dumps(a, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def sample_instances(cur, arch, use_runtime: bool) -> dict:
    """program 类题型：用运行时接口按固定种子实例化 N_SAMPLES 道题（答案由沙箱中的求解程序算出）。结果带缓存。"""
    cache = {}
    if CACHE.exists():
        cache = json.loads(CACHE.read_text(encoding="utf-8"))
    todo = [a for a in arch if a["verifiable_type"] == "program" and cache.get(a["id"], {}).get("key") != _card_key(a)]
    if use_runtime and todo:
        print(f"  实例化 {len(todo)} 个 program 题型 × {N_SAMPLES} 个种子 …", flush=True)

        def work(a):
            try:
                ps = cur.instantiate_many(a["id"], N_SAMPLES, 0)
                return a["id"], [[p.problem, p.answer or "", p.seed] for p in ps if p.answer is not None]
            except Exception as ex:  # 极少数卡片无法实例化：页面退回到改写示例
                return a["id"], []

        done = 0
        with ThreadPoolExecutor(max_workers=8) as pool:
            for aid, rows in pool.map(work, todo):
                cache[aid] = {"rows": rows}
                done += 1
                if done % 100 == 0:
                    print(f"    {done}/{len(todo)}", flush=True)
        for a in todo:
            cache[a["id"]]["key"] = _card_key(a)
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return {k: v["rows"] for k, v in cache.items() if v.get("rows")} if use_runtime else {}


def build_arch(cur, use_runtime: bool) -> dict:
    kps = read_json(DATA / "knowledge_points.json")
    kidx = {k["id"]: i for i, k in enumerate(kps)}
    arch = read_json(DATA / "archetypes.json")
    inst = sample_instances(cur, arch, use_runtime)
    out = defaultdict(list)
    n_inst = 0
    for a in arch:
        pc = a["parameter_constraints"]
        rec = {
            "i": a["id"], "f": a["item_form"], "t": a["template"], "v": VT[a["verifiable_type"]], "d": a["difficulty"], "af": a["answer_form"],
            "sl": pc.get("slots") or {}, "cs": pc.get("constraints") or [], "fm": pc.get("answer_format") or "",
            "st": a["solution_steps"], "te": a["typical_errors"][:3],
            "ex": [_fmt_example(e) for e in a["rewritten_examples"]],
            "s2": [kidx[x] for x in a["secondary_knowledge_point_ids"] if x in kidx],
            "cx": [c.replace("ctx.", "") for c in a["allowed_contexts"]],
            "pv": 1 if a["provenance"] != "textbook" else 0,
            "gr": (pc.get("observed") or {}).get("grades") or [], "ni": len(a["source_instance_ids"]),
        }
        if a["id"] in inst:
            rec["sm"] = [r[:2] for r in inst[a["id"]]]
            n_inst += len(rec["sm"])
        out[kidx[a["primary_knowledge_point_id"]]].append(rec)
    return {"a": {str(k): v for k, v in sorted(out.items())}, "n_inst": n_inst}


# ----------------------------------------------------------------------------- 装配

def pack(obj) -> str:
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.b64encode(gzip.compress(raw, 9, mtime=0)).decode("ascii")


def vendor_text(name: str, license_name: str) -> str:
    code = (VENDOR / name).read_text(encoding="utf-8")
    lic = (VENDOR / license_name).read_text(encoding="utf-8").strip()
    # 去掉库注释里的文档性 URL 的协议头，保证页面没有任何外部地址（XML 命名空间除外）
    code = re.sub(r"https?://(?!www\.w3\.org/)", "", code)
    code = code.replace("</script", "<\\/script")
    return f"/*! {name} · bundled under its license:\n{lic}\n*/\n{code}\n"


JS_ORDER = ["util", "data", "hero", "panorama", "focus", "detail", "timeline", "search", "quality", "main"]


def assemble(core: dict, arch: dict) -> str:
    tpl = (SRC / "template.html").read_text(encoding="utf-8")
    css = (SRC / "style.css").read_text(encoding="utf-8")
    js = "\n".join((SRC / "js" / f"{n}.js").read_text(encoding="utf-8") for n in JS_ORDER if (SRC / "js" / f"{n}.js").exists()).replace("</script", "<\\/script")
    vendor = vendor_text("d3.min.js", "d3.LICENSE") + vendor_text("dagre.min.js", "dagre.LICENSE")
    counts = core["meta"]["counts"]
    meta = (f'<meta name="chalkbase-data-sha256" content="{core["meta"]["sha"]}">\n'
            f'<meta name="chalkbase-counts" content=\'{json.dumps(counts, separators=(",", ":"))}\'>')
    repl = {"{{META}}": meta, "/*{{STYLE}}*/": css, "/*{{VENDOR}}*/": vendor, "{{CORE}}": pack(core), "{{ARCH}}": pack(arch), "/*{{APP}}*/": js}
    for k, v in repl.items():
        assert k in tpl, k
        tpl = tpl.replace(k, v)
    return tpl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-instances", action="store_true", help="不调用运行时接口，只用卡片里的改写示例")
    args = ap.parse_args()
    from chalkbase import Curriculum

    cur = Curriculum()
    use_runtime = not args.no_instances and hasattr(cur, "instantiate_many")
    print("构建核心载荷 …")
    core = build_core(cur)
    print("构建题型载荷 …")
    arch = build_arch(cur, use_runtime)
    html = assemble(core, arch)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8", newline="\n")
    print(f"viz/index.html：{len(html.encode('utf-8')) / 1e6:.2f} MB；知识点 {len(core['kps'])}，边 {len(core['edges'])}，"
          f"题型 {sum(len(v) for v in arch['a'].values())}，实例化题目 {arch['n_inst']}")


if __name__ == "__main__":
    main()
