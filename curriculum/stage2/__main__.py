"""`python -m curriculum.stage2`：运行 Stage 2 全流程（blocking → 成对判定 → 约束聚类 → 规范目录）。

判定结果命中 .cache/ 时重跑免费且确定。
"""
from __future__ import annotations

from collections import Counter

from curriculum.stage2.blocking import generate_candidates
from curriculum.stage2.build import build_canonical, build_extends_edges, build_structure, constrained_components, write_outputs
from curriculum.stage2.judge import judge_pairs


def run() -> None:
    cands = generate_candidates()
    pairs = sorted(cands)
    judgments = judge_pairs(pairs)
    for j in judgments:
        j["blocking"] = cands[(j["a"], j["b"])]
    failed = [j for j in judgments if j["label"] is None]
    if failed:
        raise SystemExit(f"{len(failed)} 个候选对判定失败，重跑以重试（成功的已缓存）")
    same = [(j["a"], j["b"], j["confidence"]) for j in judgments if j["label"] == "same"]
    not_same = {frozenset((j["a"], j["b"])) for j in judgments if j["label"] in ("extends", "different")}
    comps, removed = constrained_components(same, not_same)
    canon, l2c = build_canonical(comps)
    books, lessons = build_structure(l2c, canon)
    edges = build_extends_edges(judgments, l2c)
    write_outputs(judgments, removed, canon, l2c, books, lessons, edges)
    sizes = Counter(len(c) for c in comps)
    print(f"candidates={len(pairs)} labels={Counter(j['label'] for j in judgments)}")
    print(f"canonical_kps={len(canon)} cluster_sizes={dict(sorted(sizes.items()))} removed_same_edges={len(removed)} extends_edges={len(edges)}")


if __name__ == "__main__":
    run()
