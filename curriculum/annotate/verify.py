"""停顿点人工核验（CLAUDE.md 4.3.6）：生成核验样本 / 读回核验结果估计金标准确率。

  python -m curriculum.annotate.verify build            → eval/label/stage3_checkpoint.json（加载到 eval/label/index.html）
  python -m curriculum.annotate.verify score <导出.json>  → eval/label/stage3_checkpoint_results.json + 打印各任务准确率（Wilson CI）

每类金标按「一致采纳」「仲裁采纳」两层分别抽样，连同人工队列一起核验。总量控制在约 30 分钟。
"""
from __future__ import annotations

import json
import random
import sys
from collections import defaultdict

from curriculum.common import DATA_DIR, EVAL_DIR, read_json, read_jsonl, write_json
from curriculum.metrics import wilson

SEED = 20261002
OUT = EVAL_DIR / "label" / "stage3_checkpoint.json"
KEY = EVAL_DIR / "label" / "stage3_checkpoint_key.json"
RESULTS = EVAL_DIR / "label" / "stage3_checkpoint_results.json"

LABEL_CN = {
    "same": "同一知识点（same）", "extends": "螺旋扩展（extends）", "different": "不同知识点（different）",
    "prerequisite": "A 是 B 的前置（prerequisite）", "builds_on": "B 在 A 基础上递进（builds_on）", "related": "相关无方向（related）",
    "confusable": "易混淆（confusable）", "none": "无关系（none）",
    "too_coarse": "过粗", "ok": "合适", "too_fine": "过细",
}
PLAN = {  # 任务 → {层: 抽样数}
    "entity_resolution": {"consensus": 12, "arbitrated": 12, "human_queue": 10},
    "entity_resolution_anchor": {"consensus": 5, "arbitrated": 5, "human_queue": 4},
    "prerequisite": {"consensus": 10, "arbitrated": 10, "human_queue": 8},
    "archetype_granularity": {"consensus": 8, "arbitrated": 8, "human_queue": 6},
    "retrieval_probe": {"consensus": 4, "arbitrated": 4, "human_queue": 0},
}


def _gold(name: str) -> list[dict]:
    return read_jsonl(EVAL_DIR / "gold" / "val" / f"{name}.jsonl") + read_jsonl(EVAL_DIR / "gold" / "test" / f"{name}.jsonl")


def _kp_local():
    from curriculum.common import local_kps

    return {k["_key"]: k for k in local_kps()}


def _kp_canon():
    return {k["id"]: k for k in read_json(DATA_DIR / "knowledge_points.json")}


def _local_desc(k: dict) -> str:
    return f"{k['_book']}「{k['name']}」（{k['domain']}/{k['thread']}）\n    {k['description'][:110]}"


def _canon_desc(k: dict) -> str:
    return f"「{k['name']}」（引入 {k['first_introduced_lesson_id'].split('.')[0]}，{k['domain']}/{k['thread']}）\n    {k['description'][:110]}"


def _reasons(row: dict) -> str:
    outs = row.get("annotator_outputs") or {}
    bits = [f"{m}：{(o or {}).get('label')} — {(o or {}).get('reason', '')}" for m, o in outs.items()]
    if row["source"] != "consensus" and row.get("output"):
        bits.append(f"仲裁：{row['output'].get('label')} — {row['output'].get('reason', '')}")
    return "\n".join(bits)


def _items_er(rows):
    kps = _kp_local()
    out = []
    for r in rows:
        a, b = kps[r["a"]], kps[r["b"]]
        concl = LABEL_CN[r["label"]]
        if r["label"] == "extends" and r.get("output", {}).get("narrower"):
            concl += f"，较窄/较基础的是 {r['output']['narrower']}"
        out.append({"id": f"v.er.{r['id']}", "task_type": "实体消解：两个知识点是否同一",
                    "input_summary": f"A：{_local_desc(a)}\nB：{_local_desc(b)}", "conclusion": concl,
                    "confidence": r.get("arbiter_confidence"), "reasoning": _reasons(r), "_layer": r["source"]})
    return out


def _items_er_anchor(rows, rng, plan):
    kps = _kp_local()
    pool = defaultdict(list)
    for r in rows:
        for d in r["decisions"]:
            if d["label"] is not None:
                pool[d["source"]].append((r, d))
    out = []
    for layer, n in plan.items():
        for r, d in rng.sample(pool[layer], min(n, len(pool[layer]))):
            a, b = kps[r["anchor"]], kps[d["key"]]
            out.append({"id": f"v.era.{r['id']}::{d['key']}", "task_type": "实体消解（全表挑选）：锚点与候选的关系",
                        "input_summary": f"锚点：{_local_desc(a)}\n候选：{_local_desc(b)}", "conclusion": LABEL_CN[d["label"]],
                        "confidence": d.get("arbiter_confidence"),
                        "reasoning": f"两位标注者：{d.get('annotator_labels', '一致')}", "_layer": layer})
    return out


def _items_prereq(rows):
    kps = _kp_canon()
    out = []
    for r in rows:
        out.append({"id": f"v.pr.{r['id']}", "task_type": "前置关系：不掌握 A 能否学会 B",
                    "input_summary": f"A：{_canon_desc(kps[r['a']])}\nB：{_canon_desc(kps[r['b']])}", "conclusion": LABEL_CN[r["label"]],
                    "confidence": r.get("arbiter_confidence"), "reasoning": _reasons(r), "_layer": r["source"]})
    return out


def _items_gran(rows):
    out = []
    for r in rows:
        texts = "\n".join(f"  {i + 1}. {t}" for i, t in enumerate(r["texts"][:6]))
        sib = "\n".join(f"  - {s}" for s in r["siblings"][:6]) or "  （无）"
        out.append({"id": f"v.ag.{r['id']}", "task_type": "题型粒度：这是否是老师眼中的一种题型",
                    "input_summary": f"题型 {r['archetype_id']}｜主知识点：{r['kp_name']}｜形式：{r['form']}\n模板：{r.get('template')}\n"
                                     f"源实例（{r['n_instances']} 条）：\n{texts}\n同知识点其他题型：\n{sib}",
                    "conclusion": f"粒度{LABEL_CN[r['label']]}", "confidence": r.get("arbiter_confidence"), "reasoning": _reasons(r),
                    "_layer": r["source"]})
    return out


def _items_probe(rows, rng, plan):
    kps = _kp_canon()
    layered = defaultdict(list)
    for r in rows:
        layered["arbitrated" if any(d["source"] != "consensus" for d in r["decisions"]) else "consensus"].append(r)
    out = []
    for layer, n in plan.items():
        for r in rng.sample(layered[layer], min(n, len(layered[layer]))):
            core = "；".join(kps[k]["name"] for k in r["core"]) or "（空）"
            rel = "；".join(kps[k]["name"] for k in r["related"]) or "（空）"
            out.append({"id": f"v.rp.{r['id']}", "task_type": "检索探针：老师的需求应命中哪些知识点",
                        "input_summary": f"需求：{r['query']}", "conclusion": f"核心：{core}\n相关：{rel}",
                        "confidence": None, "reasoning": "判断标准：核心集合是否基本正确（允许个别可有可无的出入）；年级错配投票 " + str(r["grade_mismatch_votes"]),
                        "_layer": layer})
    return out


def build() -> None:
    rng = random.Random(SEED)
    items = []
    for task, plan in PLAN.items():
        rows = _gold(task)
        if not rows:
            print(f"跳过 {task}：金标尚未生成")
            continue
        if task == "entity_resolution_anchor":
            items += _items_er_anchor(rows, rng, plan)
            continue
        if task == "retrieval_probe":
            items += _items_probe(rows, rng, plan)
            continue
        maker = {"entity_resolution": _items_er, "prerequisite": _items_prereq, "archetype_granularity": _items_gran}[task]
        for layer, n in plan.items():
            pool = [r for r in rows if r.get("source") == layer and r.get("label")]
            items += maker(rng.sample(pool, min(n, len(pool))))
    rng.shuffle(items)
    key = {it["id"]: {"layer": it.pop("_layer"), "task": it["id"].split(".")[1]} for it in items}
    write_json(OUT, items)
    write_json(KEY, key)
    by = defaultdict(lambda: defaultdict(int))
    for v in key.values():
        by[v["task"]][v["layer"]] += 1
    print(f"{len(items)} 条 → {OUT}")
    print(json.dumps(by, ensure_ascii=False))


def score(results_path: str) -> dict:
    key = read_json(KEY)
    res = read_json(results_path)
    agg = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))  # task → layer → [accept, reject, unsure]
    for r in res:
        k = key.get(r["id"])
        if not k or not r.get("verdict"):
            continue
        idx = {"accept": 0, "reject": 1, "unsure": 2}[r["verdict"]]
        agg[k["task"]][k["layer"]][idx] += 1
        agg[k["task"]]["_all"][idx] += 1
    out = {t: {l: {"accept": v[0], "reject": v[1], "unsure": v[2], "accuracy": wilson(v[0], v[0] + v[1])} for l, v in layers.items()} for t, layers in agg.items()}
    write_json(RESULTS, {"source": str(results_path), "by_task": out, "rejected": [r for r in res if r.get("verdict") == "reject"]})
    return out


if __name__ == "__main__":
    if sys.argv[1] == "build":
        build()
    else:
        print(json.dumps(score(sys.argv[2]), ensure_ascii=False, indent=1))
