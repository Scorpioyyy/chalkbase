"""Stage 2 标注数据：成对标注数据（候选对分层抽样）与锚点标注数据（全表挑选，无偏估计 blocking 召回）。

用法：
  python -m chalkbase.stage2.gold sample        # 生成抽样清单 eval/annotation/entity_resolution/samples.json
  python -m chalkbase.stage2.gold trial         # 试标（30 对 × 候选模型配置），输出一致性诊断
  python -m chalkbase.stage2.gold pairs         # 正式成对标注数据
  python -m chalkbase.stage2.gold anchors       # 正式锚点标注数据
"""
from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from decimal import Decimal
from typing import Any

from chalkbase.annotate.client import AnnotationClient
from chalkbase.annotate.gold import (
    ARBITRATION_CONFIDENCE_THRESHOLD,
    LabelTask,
    ModelConfig,
    call_models,
    judgment_record,
    run_label_gold,
    save_gold,
)
from chalkbase.common import EVAL_DIR, grade_of, load_work_books, local_kps, read_json, write_json, write_jsonl
from chalkbase.metrics import cohen_kappa, krippendorff_alpha_nominal
from chalkbase.stage2.blocking import build_similarity, generate_candidates, pair_key

TASK = "entity_resolution"
ANCHOR_TASK = "entity_resolution_anchor"
ANN_DIR = EVAL_DIR / "annotation" / TASK
SAMPLES_PATH = ANN_DIR / "samples.json"
SEED = 20260929

ANNOTATORS = (ModelConfig("qwen3.8-flash", False), ModelConfig("deepseek-v4.1-flash", False))
ARBITER = ModelConfig("qwen3.8-max", True, max_tokens=4096)

EDITION = {2022: "新版", 2011: "旧版"}
BOOK_CN = {"a": "上册", "b": "下册"}


# ------------------------------------------------------------------ 渲染


def _kp_index() -> dict[str, dict]:
    return {k["_key"]: k for k in local_kps()}


def _book_label(bid: str) -> str:
    year = load_work_books()[bid]["registry"]["book"]["curriculum_standard_year"]
    return f"{bid}（{grade_of(bid)}年级{BOOK_CN[bid[2]]}，{EDITION.get(year, year)}）"


def _examples(k: dict, n: int = 3) -> list[str]:
    ex = [e for e in load_work_books()[k["_book"]]["exercises"] if e["primary_knowledge_point_id"] == k["id"]]
    return [e["text"][:90].replace("\n", " ") for e in ex[:n]]


def render_kp(k: dict, with_examples: bool = True) -> str:
    lines = [
        f"所在书：{_book_label(k['_book'])}",
        f"名称：{k['name']}",
        f"别名：{'、'.join(k.get('aliases', [])) or '无'}",
        f"领域/主题/主线：{k['domain']} / {k['topic']} / {k['thread']}",
        f"描述：{k['description']}",
    ]
    if with_examples:
        exs = _examples(k)
        if exs:
            lines.append("教材习题举例：" + " ‖ ".join(exs))
    return "\n".join(lines)


def render_pair(item: dict) -> str:
    kps = _kp_index()
    a, b = kps[item["a"]], kps[item["b"]]
    return f"【知识点 A】\n{render_kp(a)}\n\n【知识点 B】\n{render_kp(b)}\n\n请判断 A 与 B 的关系，只输出 JSON。"


def validate_pair(d: Any) -> bool:
    if not isinstance(d, dict) or d.get("label") not in ("same", "extends", "different"):
        return False
    if d["label"] == "extends" and d.get("narrower") not in ("A", "B"):
        return False
    try:
        float(d.get("confidence", 0))
    except (TypeError, ValueError):
        return False
    return True


def extract_pair(d: Any) -> str:
    return d["label"]


PAIR_TASK = LabelTask(name=TASK, render=render_pair, validate=validate_pair, extract=extract_pair)


# ------------------------------------------------------------------ 抽样


def stratum_of(rec: dict) -> str:
    if rec["routes"] == ["thread_adjacent"]:
        return "T"
    if rec["sim"] >= 0.30:
        return "H"
    if rec["sim"] >= 0.15:
        return "M"
    return "L"


def grade_band(bid: str) -> str:
    g = grade_of(bid)
    return "low" if g <= 2 else ("mid" if g <= 4 else "high")


def build_samples(per_stratum: int = 90, n_anchors: int = 120, full_strata: tuple = ("H",)) -> dict:
    rng = random.Random(SEED)
    cands = generate_candidates()
    by_stratum = defaultdict(list)
    for p, rec in sorted(cands.items()):
        by_stratum[stratum_of(rec)].append((p, rec))
    pairs = []
    for s in ("H", "M", "L", "T"):
        pool = by_stratum[s]
        chosen = list(pool) if s in full_strata else rng.sample(pool, min(per_stratum, len(pool)))
        order = list(range(len(chosen)))
        rng.shuffle(order)
        for rank, idx in enumerate(order):
            (a, b), rec = chosen[idx]
            pairs.append(
                {
                    "id": f"erp.{s}.{rank:03d}",
                    "a": a,
                    "b": b,
                    "stratum": s,
                    "sim": rec["sim"],
                    "routes": rec["routes"],
                    "split": "val" if rank < len(chosen) / 2 else "test",
                }
            )
    # 锚点：领域 × 年级段分层，按比例分配，每层至少 4 个（不足则全取）
    kps = local_kps()
    cells = defaultdict(list)
    for k in kps:
        cells[(k["domain"], grade_band(k["_book"]))].append(k["_key"])
    total = len(kps)
    alloc = {c: max(min(4, len(v)), round(n_anchors * len(v) / total)) for c, v in cells.items()}
    anchors = []
    for c in sorted(cells):
        chosen = rng.sample(sorted(cells[c]), min(alloc[c], len(cells[c])))
        for i, key in enumerate(chosen):
            anchors.append({"id": f"era.{c[0]}.{c[1]}.{i:02d}", "anchor": key, "cell": f"{c[0]}/{c[1]}"})
    rng.shuffle(anchors)
    for i, a in enumerate(anchors):
        a["split"] = "val" if i % 2 == 0 else "test"
    return {
        "seed": SEED,
        "per_stratum_population": {s: len(v) for s, v in by_stratum.items()},
        "pairs": pairs,
        "anchors": anchors,
    }


# ------------------------------------------------------------------ 锚点任务（集合型）

ANCHOR_INSTRUCTION = """
---
## 本次任务的形式（锚点挑选）

下面给出一个「锚点知识点」的完整信息，以及其余全部知识点的清单（编号、所在书、名称、领域/主线）。
请逐条浏览**整个清单**，找出：
- `same`：与锚点是同一个考点的全部知识点编号；
- `extends`：与锚点构成螺旋扩展（任一方向）的全部知识点编号。
没有就给空列表。不要遗漏清单后半部分。只输出 JSON：
{"same": ["编号", ...], "extends": ["编号", ...], "confidence": 0.0~1.0, "reason": "不超过60字"}
"""


def _catalog_lines(exclude: str) -> tuple[str, dict[str, str]]:
    lines, code2key = [], {}
    for i, k in enumerate(local_kps()):
        if k["_key"] == exclude:
            continue
        code = f"K{i:03d}"
        code2key[code] = k["_key"]
        lines.append(f"{code} | {k['_book']} | {k['name']} | {k['domain']}/{k['thread']}")
    return "\n".join(lines), code2key


def render_anchor(item: dict) -> tuple[str, dict[str, str]]:
    k = _kp_index()[item["anchor"]]
    catalog, code2key = _catalog_lines(item["anchor"])
    return f"【锚点知识点】\n{render_kp(k)}\n\n【全部其余知识点清单】\n{catalog}\n\n只输出 JSON。", code2key


def validate_anchor(d: Any) -> bool:
    return isinstance(d, dict) and isinstance(d.get("same"), list) and isinstance(d.get("extends"), list)


def run_anchor_gold(client: AnnotationClient, anchors: list[dict]) -> dict:
    """两步法：全表筛选（两模型各挑一次，取并集）→ 并集中每一对按成对标注数据流程逐对判定。"""
    system = PAIR_TASK.system_prompt() + ANCHOR_INSTRUCTION
    rendered = {a["id"]: render_anchor(a) for a in anchors}
    msgs = [(a["id"], rendered[a["id"]][0]) for a in anchors]
    anchor_cfgs = [ModelConfig(c.model, c.thinking, max_tokens=2048) for c in ANNOTATORS]
    r1 = {cfg.tag: call_models(client, system, msgs, cfg, validate_anchor, role="r1") for cfg in anchor_cfgs}
    judgments = []
    tags = [c.tag for c in anchor_cfgs]

    def picks(res, code2key) -> dict[str, str]:
        out = {}
        if not res.ok:
            return out
        for rel in ("extends", "same"):  # same 覆盖 extends（若同一编号两处都出现）
            for code in res.parsed.get(rel, []):
                key = code2key.get(str(code).strip())
                if key:
                    out[key] = rel
        return out

    # 第 1 步（筛选）：两模型所选并集即宽松候选，不依赖 blocking
    screened = {}
    for a in anchors:
        msg, code2key = rendered[a["id"]]
        for t in tags:
            res = r1[t][a["id"]]
            judgments.append(judgment_record(ANCHOR_TASK, a["id"], "screen", res, msg[:300], json.dumps(res.parsed, ensure_ascii=False) if res.ok else "ERROR"))
        pa = picks(r1[tags[0]][a["id"]], code2key)
        pb = picks(r1[tags[1]][a["id"]], code2key)
        screened[a["id"]] = {"union": sorted(set(pa) | set(pb)), "a": pa, "b": pb}

    # 第 2 步（逐对判定）：并集中每一对按成对标注数据的完整流程标注（完整描述 + 习题举例，盲标 + 仲裁）
    items = []
    for a in anchors:
        for key in screened[a["id"]]["union"]:
            x, y = pair_key(a["anchor"], key)
            items.append({"id": f"{a['id']}::{key}", "a": x, "b": y, "anchor_id": a["id"], "candidate": key})
    res = run_label_gold(client, PAIR_TASK, items, ANNOTATORS, ARBITER,
                         arbiter_extra_instruction="请独立判断，输出同样格式的 JSON。", judgment_task_type=ANCHOR_TASK)
    judgments += res.judgments
    by_anchor = defaultdict(list)
    for g in res.gold:
        by_anchor[g["anchor_id"]].append(g)
    gold = []
    for a in anchors:
        decisions = [
            {"key": g["candidate"], "label": g["label"], "source": g["source"], "arbiter_confidence": g.get("arbiter_confidence"),
             "annotator_labels": list(g["annotator_labels"].values()), "screened_by": [t for t, sel in zip(tags, (screened[a["id"]]["a"], screened[a["id"]]["b"])) if g["candidate"] in sel]}
            for g in by_anchor[a["id"]]
        ]
        gold.append({**a, "same": sorted(d["key"] for d in decisions if d["label"] == "same"),
                     "extends": sorted(d["key"] for d in decisions if d["label"] == "extends"), "decisions": decisions})
    screen_a = [k in screened[i]["a"] for i in screened for k in screened[i]["union"]]
    screen_b = [k in screened[i]["b"] for i in screened for k in screened[i]["union"]]
    stats = dict(res.stats)
    stats.update({
        "n_anchors": len(anchors),
        "method": "两步：全表筛选（两模型并集）→ 逐对判定（成对标注数据流程）；一致性为第 2 步逐对判定的一致性",
        "n_screened_pairs": len(items),
        "screening_overlap_on_union": round(sum(1 for x, y in zip(screen_a, screen_b) if x and y) / len(screen_a), 4) if screen_a else None,
        "cost_cny": str(sum((Decimal(j["cost_cny"]) for j in judgments), Decimal("0"))),
    })
    return {"gold": gold, "judgments": judgments, "stats": stats}


# ------------------------------------------------------------------ CLI


def main(argv: list[str]) -> None:
    cmd = argv[0] if argv else "sample"
    if cmd == "sample":
        s = build_samples()
        write_json(SAMPLES_PATH, s)
        print("pairs", len(s["pairs"]), "anchors", len(s["anchors"]), "population", s["per_stratum_population"])
        return
    samples = read_json(SAMPLES_PATH)
    client = AnnotationClient(max_workers=48)
    if cmd == "pairs":
        res = run_label_gold(client, PAIR_TASK, samples["pairs"], ANNOTATORS, ARBITER,
                             arbiter_extra_instruction="请独立判断，输出同样格式的 JSON。",
                             judgment_task_type="pairwise_entity_resolution")
        save_gold(TASK, res)
        print(json.dumps(res.stats, ensure_ascii=False, indent=1))
    elif cmd == "anchors":
        out = run_anchor_gold(client, samples["anchors"])
        ann_dir = EVAL_DIR / "annotation" / ANCHOR_TASK
        write_jsonl(ann_dir / "judgments.jsonl", out["judgments"])
        write_json(ann_dir / "stats.json", out["stats"])
        for split in ("val", "test"):
            write_jsonl(EVAL_DIR / "gold" / split / f"{ANCHOR_TASK}.jsonl", [g for g in out["gold"] if g["split"] == split])
        print(json.dumps(out["stats"], ensure_ascii=False, indent=1))
    else:
        raise SystemExit(f"unknown command {cmd}")


if __name__ == "__main__":
    main(sys.argv[1:])
