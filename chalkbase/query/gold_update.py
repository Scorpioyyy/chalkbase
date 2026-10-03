"""Stage 5 补全（知识点 419→433）后检索探针金标的更新（eval/CHANGELOG.md 2026-10-03）。

1. D14 的三条（p08/p35/p47）：整条用 `holdout_gold.annotate` 重新标注（同一指南、同一模型组），替换原金标行。
2. 其余全部探针（开发集与验收集）：只对 14 个新增（provenance=reconciled）知识点补标「core/related/none」，
   两标注者逐对独立判断，分歧交仲裁；原有金标条目不动，只追加新增知识点的判定（`decisions[].stage5_delta=true`）。
用法：python -m chalkbase.query.gold_update
"""
from __future__ import annotations

import json
from collections import defaultdict
from decimal import Decimal

from chalkbase.annotate.client import AnnotationClient
from chalkbase.annotate.gold import ARBITRATION_CONFIDENCE_THRESHOLD, call_models, judgment_record
from chalkbase.common import DATA_DIR, EVAL_DIR, book_of, read_json, read_jsonl, write_json, write_jsonl
from chalkbase.probes import retrieval_gold as rg
from chalkbase.query import holdout_gold as hg

D14 = ("p08", "p35", "p47")
FILES = {  # 金标文件 -> Judgment 任务名
    EVAL_DIR / "gold" / "val" / "retrieval_probe.jsonl": "retrieval_probe",
    EVAL_DIR / "gold" / "test" / "retrieval_probe.jsonl": "retrieval_probe",
    EVAL_DIR / "gold" / "test" / "retrieval_probe_holdout.jsonl": "retrieval_probe_holdout",
}


def _pair_msg(query: str, kp: dict, extra: str = "") -> str:
    return (f"【老师的需求】{query}\n\n【知识点】{kp['name']}（引入 {book_of(kp['first_introduced_lesson_id'])}，{kp['domain']}/{kp['thread']}）\n"
            f"描述：{kp['description']}\n\n{extra}请独立判断该知识点属于 core / related / none 哪一档。")


def main() -> None:
    kps = {k["id"]: k for k in read_json(DATA_DIR / "knowledge_points.json")}
    new_ids = sorted(k for k, v in kps.items() if v.get("provenance") == "reconciled")
    rows_by_file = {f: read_jsonl(f) for f in FILES}
    all_rows = [(f, r) for f, rs in rows_by_file.items() for r in rs]

    # ---- 1. D14 整条重标
    d14_probes = [{k: r[k] for k in ("id", "query", "grades", "domains", "type", "split")} for _, r in all_rows if r["id"] in D14]
    new_gold, d14_judgments, _ = hg.annotate(d14_probes, task="retrieval_probe")
    new_by_id = {g["id"]: g for g in new_gold}

    # ---- 2. 其余探针 × 新增知识点
    system = (EVAL_DIR / "annotation" / "retrieval_probe" / "guideline.md").read_text(encoding="utf-8")
    form = '\n\n---\n## 逐对判定形式\n针对「一条需求 + 一个知识点」，判断该知识点应属于 core / related / none 哪一档。只输出 JSON：{"tier": "core"|"related"|"none", "confidence": 0.0~1.0, "reason": "不超过60字"}'
    items = [(f"{r['id']}::{k}", _pair_msg(r["query"], kps[k])) for _, r in all_rows if r["id"] not in D14 for k in new_ids]
    client = AnnotationClient(max_workers=48)
    r1 = {c.tag: call_models(client, system + form, items, c, rg.validate_arb, role="s5r1") for c in rg.ANNOTATORS}
    tags = [c.tag for c in rg.ANNOTATORS]
    tier = lambda res: res.parsed["tier"] if res.ok else "none"
    disputes = [(iid, msg) for iid, msg in items if tier(r1[tags[0]][iid]) != tier(r1[tags[1]][iid])]
    arb_msgs = [(iid, msg.replace("请独立判断", f"两位标注者的档位：甲={tier(r1[tags[0]][iid])}，乙={tier(r1[tags[1]][iid])}。请独立判断")) for iid, msg in disputes]
    arb = call_models(client, system + form, arb_msgs, rg.ARBITER, rg.validate_arb, role="s5arb") if arb_msgs else {}

    judgments_by_task: dict[str, list[dict]] = defaultdict(list)
    delta: dict[str, list[dict]] = defaultdict(list)
    task_of = {r["id"]: FILES[f] for f, r in all_rows}
    for iid, msg in items:
        pid, kid = iid.split("::")
        for t in tags:
            judgments_by_task[task_of[pid]].append(judgment_record(task_of[pid], iid, "s5r1", r1[t][iid], msg[:200], json.dumps(r1[t][iid].parsed, ensure_ascii=False) if r1[t][iid].ok else "ERROR"))
        a, b = tier(r1[tags[0]][iid]), tier(r1[tags[1]][iid])
        if a == b:
            d = {"kp": kid, "tier": a, "source": "consensus"}
        else:
            res = arb[iid]
            judgments_by_task[task_of[pid]].append(judgment_record(task_of[pid], iid, "s5arb", res, msg[:200], json.dumps(res.parsed, ensure_ascii=False) if res.ok else "ERROR"))
            if not res.ok:
                continue
            conf = float(res.parsed.get("confidence", 0))
            d = {"kp": kid, "tier": res.parsed["tier"], "source": "arbitrated" if conf >= ARBITRATION_CONFIDENCE_THRESHOLD else "human_queue",
                 "arbiter_confidence": conf, "annotator_tiers": [a, b]}
        d["stage5_delta"] = True
        delta[pid].append(d)
    for j in d14_judgments:
        judgments_by_task["retrieval_probe"].append(j)

    # ---- 写回
    summary = defaultdict(lambda: defaultdict(int))
    for f, rs in rows_by_file.items():
        out = []
        for r in rs:
            if r["id"] in D14:
                g = new_by_id[r["id"]]
                g["split"] = r["split"]
                out.append(g)
                continue
            r = dict(r)
            ds = [d for d in delta.get(r["id"], []) if d["tier"] != "none"]
            r["decisions"] = [d for d in r["decisions"] if not d.get("stage5_delta")] + delta.get(r["id"], [])
            for d in ds:
                if d["tier"] == "core" and d["kp"] not in r["core"]:
                    r["core"] = sorted(r["core"] + [d["kp"]])
                elif d["tier"] == "related" and d["kp"] not in r["core"] + r["related"]:
                    r["related"] = sorted(r["related"] + [d["kp"]])
                summary[f.name][d["tier"]] += 1
            out.append(r)
        write_jsonl(f, out)
    for task, js in judgments_by_task.items():
        p = EVAL_DIR / "annotation" / task / "judgments.jsonl"
        keep = [j for j in read_jsonl(p) if not (j["role"] in ("s5r1", "s5arb") or (j["item_id"] in D14 and task == "retrieval_probe"))]
        write_jsonl(p, keep + js)
    cost = sum(Decimal(j["cost_cny"]) for js in judgments_by_task.values() for j in js)
    print(json.dumps({"added_by_file": {k: dict(v) for k, v in summary.items()}, "n_pairs": len(items), "n_disputes": len(disputes), "cost_cny": str(cost)}, ensure_ascii=False, indent=1))
    for pid in D14:
        g = new_by_id[pid]
        print(pid, [kps[k]["name"] for k in g["core"]], "| related:", [kps[k]["name"] for k in g["related"]])


if __name__ == "__main__":
    main()
