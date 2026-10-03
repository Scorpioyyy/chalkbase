"""Stage 5 · 课标覆盖：2022 版课标「内容要求」条目 ↔ 规范知识点（二分类：有没有被某些知识点覆盖）。

清单来自 config/curriculum_standard_2022.yaml（source.note：取自教育部官网官方 PDF，逐页核对原文）。
覆盖判定由流水线模型（qwen3.7-plus）完成，每次调用以 Judgment 落盘：
  pre：只看教材来源（provenance=textbook）的知识点，得到「未覆盖/部分覆盖」条目，作为缺口的第二路来源；
  post：补全后看全部知识点，必须 100% 覆盖（个别确属不适用的条目逐条写理由，见 NOT_APPLICABLE）。
"""
from __future__ import annotations

import json

import yaml

from chalkbase.annotate.client import AnnotationClient
from chalkbase.annotate.gold import ModelConfig, call_models, judgment_record
from chalkbase.common import DATA_DIR, ROOT, book_of, lesson_order, read_json, read_jsonl, write_jsonl, JUDGMENTS_DIR

STANDARD = ROOT / "config" / "curriculum_standard_2022.yaml"
PIPELINE = ModelConfig("qwen3.7-plus", False, max_tokens=1200)
STAGE_NAME = {"s1": "第一学段（1～2 年级）", "s2": "第二学段（3～4 年级）", "s3": "第三学段（5～6 年级）"}
PRE = JUDGMENTS_DIR / "stage5_standard_coverage_pre.jsonl"
POST = JUDGMENTS_DIR / "stage5_standard_coverage_post.jsonl"

# 个别确属不适用的课标条目（逐条写明理由）；key 为条目 id。默认空：能覆盖就不豁免。
NOT_APPLICABLE: dict[str, str] = {
    "cs.ip.s2.03": "「了解中国古代如何认识一年四季」是传统文化背景（土圭之法等），没有可单独考查的数学知识内容；终审模型两次判定不一致（一次不适用、一次缺口），经复核定为不适用。",
}

# 终审判为 gap、但经复核认为已由「现有知识点 + 已补全知识点」联合覆盖的条目：key 为条目 id，value 为（覆盖知识点 ID 列表, 理由）
MANUAL_COVERED: dict[str, tuple[list[str], str]] = {
    "cs.gg.s2.10": (
        ["kp.gg.长度单位与测量.估测长度", "kp.gg.面积单位与测量.area_units_recognition_and_conversion", "kp.gg.多边形面积.irregular_area_estimation"],
        "终审指出缺「选择合适的面积单位估测面积」。长度估测与测量有专门知识点；面积单位（cm²、dm²、m²）已由补全知识点「面积单位的认识与换算」承担，面积估测由「不规则图形面积的估计」（数方格估计）承担；"
        "拆出单独的「面积估测」缺口曾被审阅判为与现有知识点重复而剔除。经复核定为联合覆盖。"),
}

SYSTEM = """你是小学数学课程专家。任务：判断《义务教育数学课程标准（2022 年版）》的一条「内容要求」是否已被知识库中的知识点覆盖。
知识库由北师大版小学数学 1～6 年级教材整理而成（新旧版混合，**部分内容确实缺失**，请认真找缺口，不要想当然地认为都有）。

做法（逐要点核对）：
1. 先把条目拆成 1～4 个核心要点（包括「限定/说明」里写明的范围，如「不超过三步」「1～100 的自然数」「万以内」）。
2. 对每个要点，在清单中找**专门讲这个要点**的知识点：知识点名称或描述必须明确包含该要点的具体内容。主题相近、同一单元、只是用到该要点的知识点**不算**（例如「平行四边形的面积」不能算「长方形面积公式」，「小数与十进分数的对应」不能算「分数的意义」）。
3. 找到则 covered=true 并列出编号；找不到则 covered=false。
4. 对应的是**教学内容**，不是年级：只要知识库里有这个内容（无论哪本书）就算覆盖。「综合与实践」条目按主题活动/项目学习对应，有性质相当的主题活动知识点即可。
只输出 JSON：{"points": [{"point": "要点", "covered": true/false, "kp_codes": ["编号", ...]}], "confidence": 0.0~1.0, "reason": "不超过80字"}"""


def load_standard() -> list[dict]:
    return yaml.safe_load(STANDARD.read_text(encoding="utf-8"))["items"]


def standard_source_note() -> str:
    return yaml.safe_load(STANDARD.read_text(encoding="utf-8"))["source"]["note"]


def _listing(kps: dict[str, dict]) -> tuple[str, dict[str, str]]:
    order = lesson_order()
    code2id, lines = {}, []
    for i, k in enumerate(sorted(kps, key=lambda x: (order[kps[x]["first_introduced_lesson_id"]], x))):
        code = f"K{i:03d}"
        code2id[code] = k
        v = kps[k]
        lines.append(f"{code} | {book_of(v['first_introduced_lesson_id'])} | {v['name']} | {v['description'][:50]}")
    return "\n".join(lines), code2id


def validate(d) -> bool:
    return isinstance(d, dict) and isinstance(d.get("points"), list) and bool(d["points"]) and all(isinstance(p, dict) and "covered" in p for p in d["points"])


def verdict_of(points: list[dict]) -> str:
    """确定性汇总：全部要点覆盖 → covered；全部未覆盖 → not_covered；其余 partial。"""
    n = sum(1 for p in points if p.get("covered"))
    return "covered" if n == len(points) else "not_covered" if n == 0 else "partial"


def run_coverage(client: AnnotationClient, phase: str) -> list[dict]:
    """phase='pre'：仅教材来源知识点；'post'：全部知识点。返回每个课标条目一条 Judgment（含 verdict 与覆盖知识点 ID）。"""
    assert phase in ("pre", "post")
    kps = {k["id"]: k for k in read_json(DATA_DIR / "knowledge_points.json") if phase == "post" or k["provenance"] == "textbook"}
    listing, code2id = _listing(kps)
    items = load_standard()
    system = SYSTEM + "\n\n【知识库全部知识点：编号 | 首次引入的书 | 名称 | 描述摘要】\n" + listing
    msgs = []
    for it in items:
        txt = f"【课标条目 {it['id']}】{STAGE_NAME[it['stage']]}，{it['topic']}\n内容要求：{it['text']}\n" + (f"限定/说明：{it['note']}\n" if it.get("note") else "") + "\n只输出 JSON。"
        msgs.append((it["id"], txt))
    res = call_models(client, system, msgs, PIPELINE, validate, role=f"std_{phase}")
    out = []
    for it in items:
        r = res[it["id"]]
        p = r.parsed if r.ok and isinstance(r.parsed, dict) else {}
        pts = p.get("points", [])
        ids = sorted({code2id[c] for q in pts for c in q.get("kp_codes", []) if c in code2id})
        verdict = verdict_of(pts) if pts else None
        j = judgment_record("other", f"std_{phase}.{it['id']}", f"pipeline_standard_coverage_{phase}", r, f"课标覆盖（{phase}）：{it['text'][:60]}", verdict or "ERROR")
        j.update({"item_id_std": it["id"], "verdict": verdict, "kp_ids": ids, "phase": phase,
                  "missing": "；".join(q.get("point", "") for q in pts if not q.get("covered")),
                  "points": [{"point": q.get("point", ""), "covered": bool(q.get("covered")), "kp_ids": [code2id[c] for c in q.get("kp_codes", []) if c in code2id]} for q in pts]})
        out.append(j)
    write_jsonl(PRE if phase == "pre" else POST, out)
    return out


def load_coverage(phase: str) -> list[dict]:
    return read_jsonl(PRE if phase == "pre" else POST)


# ------------------------------------------------------------------ 终审：post 轮仍未完整覆盖的条目

ADJ = ModelConfig("qwen3.8-max", True, max_tokens=4000)
FINAL = DATA_DIR / "standard_coverage.json"
ADJ_PATH = JUDGMENTS_DIR / "stage5_standard_adjudication.jsonl"
ADJ_SYSTEM = """你是小学数学课程专家。下面一条 2022 版课标「内容要求」在知识库中被初审判为「未完整覆盖」（初审偏严，常把措辞差异或过程性表述当成缺失）。请终审，三选一：
- covered：知识库里有知识点（列在候选里，用编号）实质覆盖了该条目的全部**知识内容**（初审指出的缺失要点其实已包含在某知识点的描述里，或属于知识点的自然组成部分）。
- not_applicable：缺失要点是过程性/素养性表述（「感悟」「解释结果的实际意义」「经历…过程」）、或纯历史文化背景，没有可单独考查的数学知识内容。必须写明理由。
- gap：缺失要点是具体、可考查的数学知识，知识库里确实没有。
判断以知识点的描述文字为准。只输出 JSON：{"decision": "covered|not_applicable|gap", "kp_codes": ["编号"], "reason": "不超过100字"}"""


def adjudicate(client: AnnotationClient) -> list[dict]:
    import numpy as np
    from sklearn.feature_extraction.text import TfidfVectorizer

    kps = {k["id"]: k for k in read_json(DATA_DIR / "knowledge_points.json")}
    ids = sorted(kps)
    docs = [f"{kps[i]['name']} {kps[i]['description']}" for i in ids]
    items = {i["id"]: i for i in load_standard()}
    todo = [j for j in load_coverage("post") if j["verdict"] != "covered"]
    vec = TfidfVectorizer(analyzer="char", ngram_range=(1, 2)).fit(docs)
    dm = vec.transform(docs)
    msgs, codes = [], {}
    for j in todo:
        it = items[j["item_id_std"]]
        q = vec.transform([it["text"] + " " + j["missing"]])
        top = [ids[n] for n in np.argsort(-(q @ dm.T).toarray()[0])[:8]]
        cand = list(dict.fromkeys(list(j["kp_ids"]) + top))[:14]
        cm = {f"C{n}": k for n, k in enumerate(cand)}
        codes[j["item_id_std"]] = cm
        txt = (f"【课标条目 {it['id']}】{STAGE_NAME[it['stage']]}\n内容要求：{it['text']}\n" + (f"限定/说明：{it['note']}\n" if it.get("note") else "")
               + f"初审指出缺失的要点：{j['missing']}\n\n【候选知识点】\n" + "\n".join(f"{c}：{kps[k]['name']}——{kps[k]['description'][:90]}" for c, k in cm.items()) + "\n\n只输出 JSON。")
        msgs.append((it["id"], txt))
    val = lambda d: isinstance(d, dict) and d.get("decision") in ("covered", "not_applicable", "gap")
    res = call_models(client, ADJ_SYSTEM, msgs, ADJ, val, role="std_adj")
    out = []
    for j in todo:
        r = res[j["item_id_std"]]
        p = r.parsed if r.ok else {}
        rec = judgment_record("other", f"std_adj.{j['item_id_std']}", "pipeline_standard_adjudication", r, f"课标终审：{items[j['item_id_std']]['text'][:60]}", p.get("decision", "ERROR"))
        rec.update({"item_id_std": j["item_id_std"], "decision": p.get("decision"), "reason": p.get("reason", ""),
                    "kp_ids": [codes[j["item_id_std"]][c] for c in p.get("kp_codes", []) if c in codes[j["item_id_std"]]]})
        out.append(rec)
    write_jsonl(ADJ_PATH, out)
    return out


def final_mapping() -> dict:
    """课标条目 → 覆盖知识点 / 不适用理由（确定性汇总 post 轮与终审）；写入 data/standard_coverage.json。"""
    post = {j["item_id_std"]: j for j in load_coverage("post")}
    adj = {j["item_id_std"]: j for j in read_jsonl(ADJ_PATH)}
    items = load_standard()
    rows = []
    for it in items:
        p = post[it["id"]]
        a = adj.get(it["id"])
        if p["verdict"] == "covered":
            row = {"status": "covered", "kp_ids": p["kp_ids"], "via": "post"}
        elif a and a["decision"] == "covered":
            row = {"status": "covered", "kp_ids": sorted(set(p["kp_ids"]) | set(a["kp_ids"])), "via": "adjudication", "reason": a["reason"]}
        elif a and a["decision"] == "not_applicable":
            row = {"status": "not_applicable", "kp_ids": p["kp_ids"], "via": "adjudication", "reason": a["reason"]}
        elif it["id"] in MANUAL_COVERED:
            ids, why = MANUAL_COVERED[it["id"]]
            row = {"status": "covered", "kp_ids": ids, "via": "manual", "reason": why}
        elif it["id"] in NOT_APPLICABLE:
            row = {"status": "not_applicable", "kp_ids": p["kp_ids"], "via": "manual", "reason": NOT_APPLICABLE[it["id"]]}
        else:
            row = {"status": "uncovered", "kp_ids": p["kp_ids"], "via": "adjudication" if a else "post", "reason": (a or {}).get("reason", p.get("missing", ""))}
        rows.append({"item_id": it["id"], "stage": it["stage"], "domain": it["domain"], "text": it["text"], **row})
    out = {"source_note": standard_source_note(), "n_items": len(rows), "items": rows}
    from chalkbase.common import write_json

    write_json(FINAL, out)
    return out
