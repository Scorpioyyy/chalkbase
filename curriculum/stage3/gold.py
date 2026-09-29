"""Stage 3 金标：题型粒度打分（eval/specs/stage3.md §5）。

用法：
  python -m curriculum.stage3.gold granularity   # 正式方法的题型，分层抽样 150 个
  python -m curriculum.stage3.gold baseline      # 基线（签名完全相同、无回退）的分组，抽样 40 个
"""
from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from typing import Any

from curriculum.annotate.client import AnnotationClient
from curriculum.annotate.gold import LabelTask, ModelConfig, run_label_gold, save_gold
from curriculum.common import DATA_DIR, EVAL_DIR, read_json, write_json
from curriculum.stage3.grouping import group_instances

TASK = "archetype_granularity"
SEED = 20260930
ANNOTATORS = (ModelConfig("qwen3.8-flash", False), ModelConfig("deepseek-v4.1-flash", False))
ARBITER = ModelConfig("qwen3.8-max", True, max_tokens=4096)
LABELS = ("too_coarse", "ok", "too_fine")


def _size_band(n: int) -> str:
    return "1" if n == 1 else ("2-4" if n <= 4 else "5+")


def _grade_band(g: int) -> str:
    return "low" if g <= 2 else ("mid" if g <= 4 else "high")


def _items_from_groups(groups: list[dict], prefix: str) -> list[dict]:
    """groups: [{id, kp, form, instance_ids, template?}] → 标注条目（含同知识点其他题型的摘要，用于判断过细）。"""
    ex = {e["id"]: e for e in read_json(DATA_DIR / "exercises.json")}
    kps = {k["id"]: k for k in read_json(DATA_DIR / "knowledge_points.json")}
    by_kp = defaultdict(list)
    for g in groups:
        by_kp[g["kp"]].append(g)
    items = []
    for g in groups:
        inst = [ex[i] for i in g["instance_ids"]]
        grade = min(int(i.split(".")[1][1]) for i in g["instance_ids"])
        items.append({
            "id": f"{prefix}.{g['id']}",
            "archetype_id": g["id"],
            "kp": g["kp"],
            "kp_name": kps[g["kp"]]["name"],
            "form": g["form"],
            "template": g.get("template"),
            "n_instances": len(inst),
            "texts": [e["text"][:160] for e in inst[:8]],
            "siblings": [
                f"{s['id']}（{s['form']}，{len(s['instance_ids'])} 条）：" + (s.get("template") or ex[s["instance_ids"][0]]["summary"])[:60]
                for s in by_kp[g["kp"]] if s is not g
            ][:12],
            "stratum": f"{_grade_band(grade)}/{kps[g['kp']]['domain']}/{_size_band(len(inst))}",
        })
    return items


def render(item: dict) -> str:
    texts = "\n".join(f"  {i + 1}. {t}" for i, t in enumerate(item["texts"]))
    sib = "\n".join(f"  - {s}" for s in item["siblings"]) or "  （无）"
    tmpl = f"模板：{item['template']}\n" if item.get("template") else ""
    return (
        f"【待判断的题型】{item['archetype_id']}\n主知识点：{item['kp_name']}\n题目形式：{item['form']}\n{tmpl}"
        f"源实例（共 {item['n_instances']} 条，最多列 8 条）：\n{texts}\n\n【同一主知识点下的其他题型】\n{sib}\n\n只输出 JSON。"
    )


def validate(d: Any) -> bool:
    return isinstance(d, dict) and d.get("label") in LABELS and d.get("verifiable_type") in ("program", "rule", "human")


GRAN_TASK = LabelTask(name=TASK, render=render, validate=validate, extract=lambda d: d["label"])


def stratified_sample(items: list[dict], n: int, rng: random.Random) -> list[dict]:
    cells = defaultdict(list)
    for it in items:
        cells[it["stratum"]].append(it)
    total = len(items)
    out = []
    for c in sorted(cells):
        k = max(1, round(n * len(cells[c]) / total))
        out += rng.sample(cells[c], min(k, len(cells[c])))
    rng.shuffle(out)
    out = out[:n]
    for i, it in enumerate(out):
        it["split"] = "val" if i % 2 == 0 else "test"
    return out


def main(argv: list[str]) -> None:
    cmd = argv[0] if argv else "granularity"
    rng = random.Random(SEED)
    client = AnnotationClient(max_workers=48)
    if cmd == "granularity":
        arch = read_json(DATA_DIR / "archetypes.json")
        groups = [{"id": a["id"], "kp": a["primary_knowledge_point_id"], "form": a["item_form"], "instance_ids": a["source_instance_ids"],
                   "template": a["template"]} for a in arch]
        items = stratified_sample(_items_from_groups(groups, "agr"), 150, rng)
        name = TASK
    elif cmd == "baseline":
        ex = read_json(DATA_DIR / "exercises.json")
        base = group_instances(ex, backoff=False)
        groups = [{"id": f"base{i:04d}", "kp": g["signature"][0], "form": g["signature"][1], "instance_ids": g["instance_ids"]} for i, g in enumerate(base)]
        items = stratified_sample(_items_from_groups(groups, "agb"), 40, rng)
        name = TASK + "_baseline"
    else:
        raise SystemExit(cmd)
    write_json(EVAL_DIR / "annotation" / TASK / f"samples_{cmd}.json", items)
    res = run_label_gold(client, GRAN_TASK, items, ANNOTATORS, ARBITER, arbiter_extra_instruction="请独立判断，输出同样格式的 JSON。",
                         judgment_task_type="archetype_granularity")
    save_gold(name, res)
    print(json.dumps(res.stats, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
