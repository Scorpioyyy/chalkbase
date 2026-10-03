"""检索探针标准答案（CLAUDE.md 4.3 节流程，集合型任务）。

每条教师需求 → core / related 两档知识点集合。两模型各挑一次；对并集中每个 (探针, 知识点) 的档位
（core / related / 未选）求一致，分歧交仲裁模型（可见双方结论）。

用法：python -m chalkbase.probes.retrieval_gold
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from decimal import Decimal

from chalkbase.annotate.client import AnnotationClient
from chalkbase.annotate.gold import ARBITRATION_CONFIDENCE_THRESHOLD, ModelConfig, call_models, judgment_record
from chalkbase.common import DATA_DIR, EVAL_DIR, book_of, lesson_order, read_json, write_json, write_jsonl
from chalkbase.metrics import cohen_kappa, krippendorff_alpha_nominal

TASK = "retrieval_probe"
PROBES_MD = EVAL_DIR / "probes" / "retrieval_probes.md"
ANNOTATORS = (ModelConfig("qwen3.8-flash", False, 2048), ModelConfig("deepseek-v4.1-flash", False, 2048))
ARBITER = ModelConfig("qwen3.8-max", True, max_tokens=4096)
TIERS = ("core", "related", "none")


def load_probes() -> list[dict]:
    rows = []
    for line in PROBES_MD.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\|\s*(p\d+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|$", line)
        if m:
            rows.append({"id": m.group(1), "query": m.group(2), "grades": m.group(3), "domains": m.group(4), "type": m.group(5)})
    for i, r in enumerate(rows):
        r["split"] = "val" if i % 2 == 0 else "test"
    return rows


def catalog() -> tuple[str, dict[str, str]]:
    kps = read_json(DATA_DIR / "knowledge_points.json")
    order = lesson_order()
    kps.sort(key=lambda k: (order[k["first_introduced_lesson_id"]], k["id"]))
    lines, code2id = [], {}
    for i, k in enumerate(kps):
        code = f"C{i:03d}"
        code2id[code] = k["id"]
        reviews = sorted({book_of(l) for l in k["review_lesson_ids"]} - {book_of(k["first_introduced_lesson_id"])})
        rv = f"；复现 {','.join(reviews)}" if reviews else ""
        lines.append(f"{code} | 引入 {book_of(k['first_introduced_lesson_id'])}{rv} | {k['name']} | {k['domain']}/{k['thread']}")
    return "\n".join(lines), code2id


def render(probe: dict, cat: str) -> str:
    return f"【老师的需求】{probe['query']}\n\n【知识库全部规范知识点清单】\n{cat}\n\n只输出 JSON。"


def validate(d) -> bool:
    return isinstance(d, dict) and isinstance(d.get("core"), list) and isinstance(d.get("related"), list)


def validate_arb(d) -> bool:
    return isinstance(d, dict) and d.get("tier") in TIERS


def run(client: AnnotationClient | None = None) -> dict:
    client = client or AnnotationClient(max_workers=48)
    probes = load_probes()
    cat, code2id = catalog()
    system = (EVAL_DIR / "annotation" / TASK / "guideline.md").read_text(encoding="utf-8")
    msgs = [(p["id"], render(p, cat)) for p in probes]
    r1 = {c.tag: call_models(client, system, msgs, c, validate, role="r1") for c in ANNOTATORS}
    tags = [c.tag for c in ANNOTATORS]
    kps = {k["id"]: k for k in read_json(DATA_DIR / "knowledge_points.json")}

    def tiers(res) -> dict[str, str]:
        out = {}
        if not res.ok:
            return out
        for t in ("related", "core"):  # core 覆盖 related
            for c in res.parsed.get(t, []):
                if str(c).strip() in code2id:
                    out[code2id[str(c).strip()]] = t
        return out

    judgments, per, disputes = [], {}, []
    for p, (_, msg) in zip(probes, msgs):
        ta, tb = tiers(r1[tags[0]][p["id"]]), tiers(r1[tags[1]][p["id"]])
        for t in tags:
            res = r1[t][p["id"]]
            judgments.append(judgment_record(TASK, p["id"], "r1", res, p["query"], json.dumps(res.parsed, ensure_ascii=False) if res.ok else "ERROR"))
        per[p["id"]] = (ta, tb)
        for k in sorted(set(ta) | set(tb)):
            if ta.get(k, "none") != tb.get(k, "none"):
                disputes.append((p, k, ta.get(k, "none"), tb.get(k, "none")))
    arb_sys = system + '\n\n---\n## 仲裁形式\n针对「一条需求 + 一个知识点」，判断该知识点应属于 core / related / none 哪一档。只输出 JSON：{"tier": "core"|"related"|"none", "confidence": 0.0~1.0, "reason": "不超过60字"}'
    arb_items = []
    for p, k, x, y in disputes:
        v = kps[k]
        arb_items.append((f"{p['id']}::{k}",
                          f"【老师的需求】{p['query']}\n\n【知识点】{v['name']}（引入 {book_of(v['first_introduced_lesson_id'])}，{v['domain']}/{v['thread']}）\n描述：{v['description']}\n\n"
                          f"两位标注者的档位：甲={x}，乙={y}。请独立判断。"))
    arb = call_models(client, arb_sys, arb_items, ARBITER, validate_arb, role="arb") if arb_items else {}
    for iid, msg in arb_items:
        judgments.append(judgment_record(TASK, iid, "arb", arb[iid], msg, json.dumps(arb[iid].parsed, ensure_ascii=False) if arb[iid].ok else "ERROR"))

    gold, la, lb, counts = [], [], [], defaultdict(int)
    for p in probes:
        ta, tb = per[p["id"]]
        decisions = []
        for k in sorted(set(ta) | set(tb)):
            x, y = ta.get(k, "none"), tb.get(k, "none")
            la.append(x)
            lb.append(y)
            if x == y:
                decisions.append({"kp": k, "tier": x, "source": "consensus"})
                counts["consensus"] += 1
            else:
                r = arb[f"{p['id']}::{k}"]
                if not r.ok:
                    counts["failed"] += 1
                    continue
                conf = float(r.parsed.get("confidence", 0))
                src = "arbitrated" if conf >= ARBITRATION_CONFIDENCE_THRESHOLD else "human_queue"
                decisions.append({"kp": k, "tier": r.parsed["tier"], "source": src, "arbiter_confidence": conf, "annotator_tiers": [x, y]})
                counts[src] += 1
        mism = [bool((r1[t][p["id"]].parsed or {}).get("grade_mismatch")) for t in tags]
        gold.append({**p, "core": [d["kp"] for d in decisions if d["tier"] == "core"], "related": [d["kp"] for d in decisions if d["tier"] == "related"],
                     "grade_mismatch_votes": mism, "decisions": decisions})
    stats = {
        "n_probes": len(probes), "annotators": tags, "arbiter": ARBITER.tag, "decision_counts": dict(counts), "n_union_decisions": len(la),
        "raw_agreement_on_union": round(sum(1 for x, y in zip(la, lb) if x == y) / len(la), 4) if la else None,
        "cohen_kappa_on_union": cohen_kappa(la, lb),
        "krippendorff_alpha_on_union": krippendorff_alpha_nominal([[x, y] for x, y in zip(la, lb)]),
        "note": "一致性在两模型所选并集上计算（core/related/none 三档）",
        "cost_cny": str(sum((Decimal(j["cost_cny"]) for j in judgments), Decimal("0"))),
    }
    d = EVAL_DIR / "annotation" / TASK
    write_jsonl(d / "judgments.jsonl", judgments)
    write_json(d / "stats.json", stats)
    for split in ("val", "test"):
        write_jsonl(EVAL_DIR / "gold" / split / f"{TASK}.jsonl", [g for g in gold if g["split"] == split])
    return stats


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
