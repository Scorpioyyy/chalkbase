"""Stage 4 金标：成对金标（证据分层，闭包 P/R/F1）与锚点金标（候选召回）。

用法：
  python -m curriculum.stage4.gold sample
  python -m curriculum.stage4.gold pairs
  python -m curriculum.stage4.gold anchors
"""
from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from decimal import Decimal
from typing import Any

from curriculum.annotate.client import AnnotationClient
from curriculum.annotate.gold import (
    ARBITRATION_CONFIDENCE_THRESHOLD,
    ModelConfig,
    call_models,
    judgment_record,
    run_label_gold,
    save_gold,
)
from curriculum.common import DATA_DIR, EVAL_DIR, book_of, grade_of, read_json, write_json, write_jsonl
from curriculum.metrics import cohen_kappa, krippendorff_alpha_nominal
from curriculum.stage4.build import PREREQ_TASK, validate
from curriculum.stage4.candidates import generate_candidates, load_canonical, stratum_of
from curriculum.stage4.render import render_kp, render_pair

TASK = "prerequisite"
ANCHOR_TASK = "prerequisite_anchor"
SAMPLES = EVAL_DIR / "annotation" / TASK / "samples.json"
SEED = 20261001
ANNOTATORS = (ModelConfig("qwen3.8-flash", False), ModelConfig("deepseek-v4.1-flash", False))
ARBITER = ModelConfig("qwen3.8-max", True, max_tokens=4096)


def build_samples(per_stratum: int = 50, n_anchors: int = 100) -> dict:
    rng = random.Random(SEED)
    kps, pos, sem = load_canonical()
    cands = generate_candidates()
    strata = defaultdict(list)
    for p, rec in sorted(cands.items()):
        strata[stratum_of(p, rec)].append(p)
    ids = sorted(kps)
    non = [(a, b) for a in ids for b in ids if a != b and pos[a] < pos[b] and (a, b) not in cands]
    strata["N"] = non
    pairs = []
    for s in ("C", "R", "X", "T", "P", "N"):
        pool = strata[s]
        chosen = rng.sample(pool, min(per_stratum, len(pool)))
        for i, (a, b) in enumerate(chosen):
            pairs.append({"id": f"prp.{s}.{i:03d}", "a": a, "b": b, "stratum": s, "split": "val" if i % 2 == 0 else "test"})
    # 锚点：领域 × 年级段分层，排除 g1a 首课（没有前置可言）
    cells = defaultdict(list)
    for k, v in kps.items():
        if pos[k] == min(pos.values()):
            continue
        g = grade_of(book_of(v["first_introduced_lesson_id"]))
        cells[(v["domain"], "low" if g <= 2 else "mid" if g <= 4 else "high")].append(k)
    total = sum(len(v) for v in cells.values())
    anchors = []
    for c in sorted(cells):
        k = max(min(3, len(cells[c])), round(n_anchors * len(cells[c]) / total))
        for kid in rng.sample(sorted(cells[c]), min(k, len(cells[c]))):
            anchors.append({"id": f"pra.{kid}", "anchor": kid, "cell": f"{c[0]}/{c[1]}"})
    rng.shuffle(anchors)
    for i, a in enumerate(anchors):
        a["split"] = "val" if i % 2 == 0 else "test"
    return {"seed": SEED, "strata_population": {s: len(v) for s, v in strata.items()}, "pairs": pairs, "anchors": anchors}


ANCHOR_INSTRUCTION = """
---
## 本次任务的形式（锚点挑选）

下面给出一个「目标知识点 B」和知识库中全部其他知识点的清单（编号、首次引入的书、名称、领域/主线）。请逐条浏览**整个清单**，找出 B 的全部**直接前置**知识点：不掌握它就学不会 B，而且它是 B 直接建立在其上的那一层（不必列出前置的前置）。
不要受清单中「引入书」先后的限制——教材版本混杂，前置知识点可能在序列中更晚才出现。
只输出 JSON：{"prerequisites": ["编号", ...], "confidence": 0.0~1.0, "reason": "不超过80字"}
"""


def render_anchor(item: dict) -> tuple[str, dict[str, str]]:
    kps, pos, _ = load_canonical()
    lines, code2id = [], {}
    for i, k in enumerate(sorted(kps, key=lambda x: (pos[x], x))):
        if k == item["anchor"]:
            continue
        code = f"K{i:03d}"
        code2id[code] = k
        v = kps[k]
        lines.append(f"{code} | {book_of(v['first_introduced_lesson_id'])} | {v['name']} | {v['domain']}/{v['thread']}")
    return f"【目标知识点 B】\n{render_kp(item['anchor'])}\n\n【全部其他知识点清单】\n" + "\n".join(lines) + "\n\n只输出 JSON。", code2id


def run_anchor_gold(client: AnnotationClient, anchors: list[dict]) -> dict:
    system = PREREQ_TASK.system_prompt() + ANCHOR_INSTRUCTION
    rendered = {a["id"]: render_anchor(a) for a in anchors}
    cfgs = [ModelConfig(c.model, c.thinking, max_tokens=2048) for c in ANNOTATORS]
    val = lambda d: isinstance(d, dict) and isinstance(d.get("prerequisites"), list)
    r1 = {c.tag: call_models(client, system, [(a["id"], rendered[a["id"]][0]) for a in anchors], c, val, role="r1") for c in cfgs}
    tags = [c.tag for c in cfgs]
    judgments, items, screened = [], [], {}
    for a in anchors:
        msg, code2id = rendered[a["id"]]
        sets = []
        for t in tags:
            res = r1[t][a["id"]]
            judgments.append(judgment_record(ANCHOR_TASK, a["id"], "screen", res, msg[:300], json.dumps(res.parsed, ensure_ascii=False) if res.ok else "ERROR"))
            sets.append({code2id[c] for c in (res.parsed or {}).get("prerequisites", []) if c in code2id} if res.ok else set())
        screened[a["id"]] = sets
        for k in sorted(sets[0] | sets[1]):
            items.append({"id": f"{a['id']}::{k}", "a": k, "b": a["anchor"], "anchor_id": a["id"]})
    # 第 2 步：并集中每一对 (A=候选, B=锚点) 按成对金标流程逐对判定（完整描述 + 习题举例，盲标 + 仲裁）
    res = run_label_gold(client, PREREQ_TASK, items, ANNOTATORS, ARBITER, arbiter_extra_instruction="请独立判断，输出同样格式的 JSON。",
                         judgment_task_type=ANCHOR_TASK)
    judgments += res.judgments
    by_anchor = defaultdict(list)
    for g in res.gold:
        by_anchor[g["anchor_id"]].append(g)
    gold = []
    for a in anchors:
        decisions = [{"a": g["a"], "label": g["label"], "is_prereq": g["label"] == "prerequisite", "source": g["source"],
                      "arbiter_confidence": g.get("arbiter_confidence")} for g in by_anchor[a["id"]]]
        gold.append({**a, "prerequisites": sorted(d["a"] for d in decisions if d["is_prereq"]), "decisions": decisions})
    sa = [k in screened[i][0] for i in screened for k in sorted(screened[i][0] | screened[i][1])]
    sb = [k in screened[i][1] for i in screened for k in sorted(screened[i][0] | screened[i][1])]
    stats = dict(res.stats)
    stats.update({
        "n_anchors": len(anchors),
        "method": "两步：全表筛选直接前置（两模型并集）→ 逐对判定（成对金标流程）；一致性为第 2 步的一致性",
        "n_screened_pairs": len(items),
        "screening_overlap_on_union": round(sum(1 for x, y in zip(sa, sb) if x and y) / len(sa), 4) if sa else None,
        "cost_cny": str(sum((Decimal(j["cost_cny"]) for j in judgments), Decimal("0"))),
    })
    return {"gold": gold, "judgments": judgments, "stats": stats}


def main(argv: list[str]) -> None:
    cmd = argv[0] if argv else "sample"
    if cmd == "sample":
        s = build_samples()
        write_json(SAMPLES, s)
        print("pairs", len(s["pairs"]), "anchors", len(s["anchors"]), "population", s["strata_population"])
        return
    s = read_json(SAMPLES)
    client = AnnotationClient(max_workers=48)
    if cmd == "pairs":
        res = run_label_gold(client, PREREQ_TASK, s["pairs"], ANNOTATORS, ARBITER, arbiter_extra_instruction="请独立判断，输出同样格式的 JSON。",
                             judgment_task_type="prerequisite_judgment")
        save_gold(TASK, res)
        print(json.dumps(res.stats, ensure_ascii=False, indent=1))
    elif cmd == "anchors":
        out = run_anchor_gold(client, s["anchors"])
        d = EVAL_DIR / "annotation" / ANCHOR_TASK
        write_jsonl(d / "judgments.jsonl", out["judgments"])
        write_json(d / "stats.json", out["stats"])
        for split in ("val", "test"):
            write_jsonl(EVAL_DIR / "gold" / split / f"{ANCHOR_TASK}.jsonl", [g for g in out["gold"] if g["split"] == split])
        print(json.dumps(out["stats"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
