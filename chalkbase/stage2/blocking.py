"""Stage 2 · Blocking：高召回、低成本、确定性的候选对生成（KICKOFF §9 Stage 2 第 1 步）。

两路并集：
  (a) 同一领域内，对「名称 + 别名 + 描述」做字符 1～2 gram TF-IDF，每个知识点取余弦相似度前 k 个；
  (b) 同一主线上相邻学期（教学序列中书序号相差 ≤ 1，含同书）的知识点两两加入。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from chalkbase.common import book_index, local_kps

TOP_K = 10


def kp_text(k: dict) -> str:
    return " ".join([k["name"], *k.get("aliases", []), k.get("description", "")])


@dataclass(frozen=True)
class SimilarityIndex:
    keys: list[str]
    sim: np.ndarray  # 全体局部知识点两两余弦相似度（跨领域也算，供评测分层用）


def build_similarity() -> SimilarityIndex:
    kps = local_kps()
    vec = TfidfVectorizer(analyzer="char", ngram_range=(1, 2), sublinear_tf=True)
    X = vec.fit_transform([kp_text(k) for k in kps])
    sim = (X @ X.T).toarray()
    np.fill_diagonal(sim, 0.0)
    return SimilarityIndex(keys=[k["_key"] for k in kps], sim=sim)


def pair_key(a: str, b: str) -> tuple[str, str]:
    """无序对的规范表示：按教学先后（局部知识点列表顺序）排列，前者为较早引入者。"""
    order = _order()
    return (a, b) if order[a] <= order[b] else (b, a)


_ORDER: dict[str, int] | None = None


def _order() -> dict[str, int]:
    global _ORDER
    if _ORDER is None:
        _ORDER = {k["_key"]: i for i, k in enumerate(local_kps())}
    return _ORDER


def generate_candidates(top_k: int = TOP_K, index: SimilarityIndex | None = None) -> dict[tuple[str, str], dict]:
    """返回 {pair: {"sim": float, "routes": [...]}}。"""
    kps = local_kps()
    index = index or build_similarity()
    by_key = {k["_key"]: k for k in kps}
    pos = {key: i for i, key in enumerate(index.keys)}
    cands: dict[tuple[str, str], dict] = {}

    def add(a: str, b: str, route: str):
        p = pair_key(a, b)
        rec = cands.setdefault(p, {"sim": round(float(index.sim[pos[a], pos[b]]), 4), "routes": []})
        if route not in rec["routes"]:
            rec["routes"].append(route)

    # (a) 同领域 TF-IDF top-k
    for k in kps:
        i = pos[k["_key"]]
        same_domain = [j for j, key in enumerate(index.keys) if j != i and by_key[key]["domain"] == k["domain"]]
        ranked = sorted(same_domain, key=lambda j: -index.sim[i, j])[:top_k]
        for j in ranked:
            add(k["_key"], index.keys[j], "tfidf_topk")

    # (b) 同主线相邻学期
    for i, a in enumerate(kps):
        for b in kps[i + 1 :]:
            if a["domain"] == b["domain"] and a["thread"] == b["thread"] and abs(book_index(a["_book"]) - book_index(b["_book"])) <= 1:
                add(a["_key"], b["_key"], "thread_adjacent")
    return cands
