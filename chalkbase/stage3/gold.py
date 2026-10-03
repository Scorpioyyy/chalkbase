"""Stage 3 金标：题型粒度打分（eval/specs/stage3.md §5）。

用法：
  python -m chalkbase.stage3.gold granularity [groups.json]   # 正式方法的题型，分层抽样 150 个（可先用分组文件，与建卡片并行）
  python -m chalkbase.stage3.gold baseline      # 基线（签名完全相同、无回退）的分组，抽样 40 个
  python -m chalkbase.stage3.gold dev <groups.json> <标签>
      # 开发迭代：对分组方案（建卡片之前）抽样，只标 val 一半，结果存 eval/annotation/archetype_granularity_dev/<标签>/，不碰 test
  python -m chalkbase.stage3.gold relabel <旧 archetypes.json> <旧样本文件> <金标名>
      # 用当前指南重标「旧方案」的既有样本（同一批题型、同 val/test 划分），得到新指南下旧方案的基线
"""
from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from typing import Any

from chalkbase.annotate.client import AnnotationClient
from chalkbase.annotate.gold import LabelTask, ModelConfig, run_label_gold, save_gold
from chalkbase.common import DATA_DIR, EVAL_DIR, read_json, write_json, write_jsonl
from chalkbase.stage3.grouping import group_instances

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
            "n_instances": len(inst),
            "texts": [e["text"][:160] for e in inst[:8]],
            # 同知识点其他题型：只给源实例（指南 v2：比较源实例，不评判模板）
            "siblings": [
                f"{s['id']}（{s['form']}，{len(s['instance_ids'])} 条）：" + "｜".join(ex[i]["text"][:50].replace("\n", " ") for i in s["instance_ids"][:2])
                for s in by_kp[g["kp"]] if s is not g
            ][:12],
            "stratum": f"{_grade_band(grade)}/{kps[g['kp']]['domain']}/{_size_band(len(inst))}",
        })
    return items


def render(item: dict) -> str:
    texts = "\n".join(f"  {i + 1}. {t}" for i, t in enumerate(item["texts"]))
    sib = "\n".join(f"  - {s}" for s in item["siblings"]) or "  （无）"
    return (
        f"【待判断的题型】{item['archetype_id']}\n主知识点：{item['kp_name']}\n题目形式：{item['form']}\n"
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
        if len(argv) > 1:
            # 与建卡片并行：分组已定、卡片未生成完时，按 build.run 同样的规则预先算出题型 ID（ID 只取决于分组顺序与知识点 slug）
            from collections import Counter
            from chalkbase.stage3.build import kp_slugs
            kps = {k["id"]: k for k in read_json(DATA_DIR / "knowledge_points.json")}
            slugs, seq, groups = kp_slugs(kps), Counter(), []
            for g in read_json(argv[1]):
                kp = g["signature"][0]
                seq[kp] += 1
                groups.append({"id": f"at.{slugs[kp]}.{seq[kp]:02d}", "kp": kp, "form": g["signature"][1], "instance_ids": g["instance_ids"]})
        else:
            arch = read_json(DATA_DIR / "archetypes.json")
            groups = [{"id": a["id"], "kp": a["primary_knowledge_point_id"], "form": a["item_form"], "instance_ids": a["source_instance_ids"],
                       } for a in arch]
        items = stratified_sample(_items_from_groups(groups, "agr"), 150, rng)
        name = TASK
    elif cmd == "baseline":
        ex = read_json(DATA_DIR / "exercises.json")
        base = group_instances(ex, backoff=False)
        groups = [{"id": f"base{i:04d}", "kp": g["signature"][0], "form": g["signature"][1], "instance_ids": g["instance_ids"]} for i, g in enumerate(base)]
        items = stratified_sample(_items_from_groups(groups, "agb"), 40, rng)
        name = TASK + "_baseline"
    elif cmd == "dev":
        groups_json, tag = read_json(argv[1]), argv[2]
        groups = [{"id": f"p{i:04d}", "kp": g["signature"][0], "form": g["signature"][1], "instance_ids": g["instance_ids"]} for i, g in enumerate(groups_json)]
        items = [it for it in stratified_sample(_items_from_groups(groups, "agd"), 150, rng) if it["split"] == "val"]
        d = EVAL_DIR / "annotation" / "archetype_granularity_dev" / tag
        d.mkdir(parents=True, exist_ok=True)
        res = run_label_gold(client, GRAN_TASK, items, ANNOTATORS, ARBITER, arbiter_extra_instruction="请独立判断，输出同样格式的 JSON。",
                             judgment_task_type="archetype_granularity")
        write_jsonl(d / "judgments.jsonl", res.judgments)
        write_jsonl(d / "gold_val.jsonl", res.gold)
        write_json(d / "stats.json", res.stats)
        from collections import Counter
        print(json.dumps(res.stats, ensure_ascii=False), dict(Counter(g["label"] for g in res.gold)))
        return
    elif cmd == "relabel":
        old_arch, old_samples, name = read_json(argv[1]), read_json(argv[2]), argv[3]
        want = {s["archetype_id"]: s for s in old_samples}
        groups = [{"id": a["id"], "kp": a["primary_knowledge_point_id"], "form": a["item_form"], "instance_ids": a["source_instance_ids"]} for a in old_arch]
        items = [dict(it, split=want[it["archetype_id"]]["split"]) for it in _items_from_groups(groups, old_samples[0]["id"].split(".")[0]) if it["archetype_id"] in want]
        cmd = name
    else:
        raise SystemExit(cmd)
    (EVAL_DIR / "annotation" / name).mkdir(parents=True, exist_ok=True)
    write_json(EVAL_DIR / "annotation" / name / "samples.json", items)
    res = run_label_gold(client, GRAN_TASK, items, ANNOTATORS, ARBITER, arbiter_extra_instruction="请独立判断，输出同样格式的 JSON。",
                         judgment_task_type="archetype_granularity")
    save_gold(name, res)
    print(json.dumps(res.stats, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
