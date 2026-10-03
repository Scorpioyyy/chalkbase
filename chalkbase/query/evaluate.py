"""Stage 7 检索探针评测（eval/specs/stage7.md）：recall@5（封顶）、hit@5、MRR，对比 TF-IDF 基线。

`python -m chalkbase.query.evaluate [--split val|test|all] [--system hybrid|lexical|baseline]` 单独运行；
`chalkbase.eval` 通过 `metrics(split)` 注册为 `stage7_query_interface`。
"""
from __future__ import annotations

import random
from collections import defaultdict
from typing import Callable, Optional

from chalkbase.common import EVAL_DIR, read_jsonl
from chalkbase.metrics import wilson

D14_PROBES = ("p08", "p35", "p47")  # 金标待 Stage 5 补全面积缺口后更新（D14）
MAIN_K = 5
THRESHOLDS = {"recall@5": 0.85, "mrr": 0.70}


def load_gold(split: str) -> list[dict]:
    """val = 开发集：原 val 30 条 + 原 test 30 条（首次验收未达标后降级并入开发集，见 eval/CHANGELOG.md）；
    test = 新验收集（eval/probes/retrieval_probes_holdout.md，从未参与开发）；all = 二者之和。"""
    d = EVAL_DIR / "gold"
    dev = read_jsonl(d / "val" / "retrieval_probe.jsonl") + [{**r, "demoted_from_test": True} for r in read_jsonl(d / "test" / "retrieval_probe.jsonl")]
    holdout = read_jsonl(d / "test" / "retrieval_probe_holdout.jsonl")
    return {"val": dev, "test": holdout, "all": dev + holdout}[split]


def probe_scores(ranked: list[str], core: set[str], related: set[str]) -> dict:
    def rec(k, target):
        return len(set(ranked[:k]) & target) / min(len(target), k)

    rank = next((i + 1 for i, r in enumerate(ranked[:50]) if r in core), None)
    return {
        "recall@5": rec(5, core),
        "recall@10": rec(10, core),
        "hit@5": float(bool(set(ranked[:5]) & core)),
        "mrr": 1.0 / rank if rank else 0.0,
        "first_rank": rank,
        "cr_recall@10": rec(10, core | related) if related else None,
    }


def bootstrap_ci(xs: list[float], n_boot: int = 10000, seed: int = 0) -> list[float]:
    rng = random.Random(seed)
    n = len(xs)
    means = sorted(sum(xs[rng.randrange(n)] for _ in range(n)) / n for _ in range(n_boot))
    return [round(means[int(0.025 * n_boot)], 4), round(means[int(0.975 * n_boot) - 1], 4)]


def evaluate(ranker: Callable[[str, dict], list[str]], gold: list[dict], valid_ids: set[str]) -> dict:
    """ranker(query, probe_row) -> 排序后的 kp_id 列表。金标里已不存在的 ID（如 Stage 5 改名）会被丢弃并计数。"""
    per = []
    dropped = 0
    n_empty = 0
    for row in gold:
        core = {k for k in row["core"] if k in valid_ids}
        related = {k for k in row["related"] if k in valid_ids}
        dropped += (len(row["core"]) - len(core)) + (len(row["related"]) - len(related))
        if not core:
            n_empty += 1
            continue
        ranked = ranker(row["query"], row)
        s = probe_scores(ranked, core, related)
        s.update(id=row["id"], type=row["type"], query=row["query"], n_core=len(core), top5=ranked[:5])
        per.append(s)
    return {"per_probe": per, "n": len(per), "n_empty_core": n_empty, "n_dropped_ids": dropped}


def summarize(res: dict) -> dict:
    per = res["per_probe"]

    def agg(rows):
        n = len(rows)
        if not n:
            return {"n": 0}
        out = {"n": n}
        for m in ("recall@5", "recall@10", "mrr"):
            xs = [r[m] for r in rows]
            out[m] = round(sum(xs) / n, 4)
            out[m + "_ci"] = bootstrap_ci(xs) if n >= 5 else None
        k = int(sum(r["hit@5"] for r in rows))
        out["hit@5"] = wilson(k, n)
        cr = [r["cr_recall@10"] for r in rows if r["cr_recall@10"] is not None]
        out["core_related_recall@10"] = round(sum(cr) / len(cr), 4) if cr else None
        return out

    by_type = defaultdict(list)
    for r in per:
        by_type[r["type"]].append(r)
    return {
        "all": agg(per),
        "excl_d14": agg([r for r in per if r["id"] not in D14_PROBES]),
        "by_type": {t: agg(rs) for t, rs in sorted(by_type.items())},
        "d14": {r["id"]: {"first_rank": r["first_rank"], "recall@5": round(r["recall@5"], 3)} for r in per if r["id"] in D14_PROBES},
        "n_empty_core": res["n_empty_core"],
        "n_dropped_ids": res["n_dropped_ids"],
    }


def make_rankers():
    from chalkbase.query.store import Curriculum

    cur = Curriculum()
    valid = set(cur.knowledge_points)
    from chalkbase.query.retrieval import TfidfBaseline

    base = TfidfBaseline(cur)
    return cur, valid, {
        "baseline": lambda q, row: [h[0] for h in base.rank(q, 50)],
        "system": lambda q, row: [h.kp_id for h in cur.search(q, 50)],
    }


def metrics(split: str = "val") -> dict:
    """chalkbase.eval 的注册函数。split 为 val/test，对该划分评测；基线同划分一并计算。"""
    cur, valid, rankers = make_rankers()
    gold = load_gold(split)
    cur_s = summarize(evaluate(rankers["system"], gold, valid))
    base_s = summarize(evaluate(rankers["baseline"], gold, valid))
    a, b = cur_s["all"], base_s["all"]
    headline = {}
    for name, key, thr in (("recall@5（封顶）", "recall@5", THRESHOLDS["recall@5"]), ("MRR", "mrr", THRESHOLDS["mrr"])):
        headline[name] = {"value": a[key], "ci": a[key + "_ci"], "n": a["n"], "baseline": b[key], "threshold": thr, "pass": a[key] >= thr}
    headline["hit@5"] = {"value": a["hit@5"]["p"], "ci": [a["hit@5"]["lo"], a["hit@5"]["hi"]], "n": a["n"], "baseline": b["hit@5"]["p"], "threshold": None, "pass": None}
    headline["recall@10（封顶）"] = {"value": a["recall@10"], "ci": a["recall@10_ci"], "n": a["n"], "baseline": b["recall@10"], "threshold": None, "pass": None}
    lines = [
        "",
        f"检索探针（{split}，n={a['n']}；recall 为封顶口径 |top-k∩core|/min(|core|,k)，CI：recall/MRR 为 bootstrap，hit@5 为 Wilson；"
        f"排除 D14 三条（p08/p35/p47）后 recall@5 {cur_s['excl_d14'].get('recall@5')}、MRR {cur_s['excl_d14'].get('mrr')}；"
        f"D14 三条首个核心命中名次：{cur_s['d14'] or '（不在本划分）'}）。",
        "",
        "| 探针类型 | n | recall@5 | MRR | 基线 recall@5 | 基线 MRR |",
        "|---|---|---|---|---|---|",
    ]
    if split == "val":
        lines.insert(2, "val 是开发集（含已用于调参与错误分析的探针），不作验收；验收以 `--split test`（新验收集）为准，结果见 `eval/specs/stage7.md` 5.3。")
        lines.insert(2, "")
    for t, r in cur_s["by_type"].items():
        bt = base_s["by_type"].get(t, {})
        lines.append(f"| {t} | {r['n']} | {r['recall@5']} | {r['mrr']} | {bt.get('recall@5')} | {bt.get('mrr')} |")
    return {
        "implemented": True,
        "split": split,
        "headline": headline,
        "current": {"retrieval": cur_s},
        "baseline": {"retrieval": base_s},
        "report_lines": lines,
    }


def main() -> None:
    import argparse
    import json

    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="val", choices=["val", "test", "all"])
    ap.add_argument("--system", default="system", choices=["system", "baseline"])
    ap.add_argument("--errors", type=int, default=0, help="打印 recall@5 最低的 N 条探针")
    args = ap.parse_args()
    cur, valid, rankers = make_rankers()
    res = evaluate(rankers[args.system], load_gold(args.split), valid)
    s = summarize(res)
    print(json.dumps({k: v for k, v in s.items() if k != "by_type"}, ensure_ascii=False, indent=1))
    for t, r in s["by_type"].items():
        print(t, r["n"], r["recall@5"], r["mrr"])
    if args.errors:
        for r in sorted(res["per_probe"], key=lambda r: (r["recall@5"], r["mrr"]))[: args.errors]:
            print(f"\n{r['id']} [{r['type']}] rank1={r['first_rank']} r@5={r['recall@5']:.2f} |core|={r['n_core']}  {r['query']}")
            for k in r["top5"]:
                print("   ", k, cur.kp(k).name)


if __name__ == "__main__":
    main()
