"""Stage 5 · 重算依赖引入位置的派生量：题型难度。

难度 = 解题步数、涉及知识点数、主次知识点引入位置的跨度（学期数）、是否逆向思考、是否读图的 z-score 加权和，
年级内分位数映射为 1～5（实现在 chalkbase/stage3/build.py 的 `difficulty`，这里直接复用，不复制算法）。
引入位置变动只影响「跨度」特征，其余特征从已保存的 difficulty_features 与参数包络读回，所以本命令只读 archetypes.json、
只改写难度字段（difficulty、difficulty_features），可重复运行；题型引入位置变动后以 `--write` 应用。

    python -m chalkbase.stage5 difficulty            # 只计算并报告变化，不写文件
    python -m chalkbase.stage5 difficulty --write    # 写回 data/archetypes.json
"""
from __future__ import annotations

import copy
from collections import Counter

from chalkbase.common import DATA_DIR, book_of, read_json, write_json
from chalkbase.stage3.build import difficulty as stage3_difficulty


def recompute_archetypes(archetypes: list[dict], kps: dict[str, dict], lessons_book: dict[str, str]) -> list[dict]:
    arch = copy.deepcopy(archetypes)
    for a in arch:
        a["_reverse"] = bool(a["difficulty_features"].get("reverse_thinking", 0))  # stage3.difficulty 会 pop 该键
    stage3_difficulty(arch, kps, lessons_book)
    return arch


def recompute(write: bool = False) -> dict:
    kps = {k["id"]: k for k in read_json(DATA_DIR / "knowledge_points.json")}
    lessons_book = {l["id"]: book_of(l["id"]) for l in read_json(DATA_DIR / "lessons.json")}
    old = read_json(DATA_DIR / "archetypes.json")
    new = recompute_archetypes(old, kps, lessons_book)
    span_changed = sum(1 for o, n in zip(old, new) if o["difficulty_features"].get("intro_span_semesters") != n["difficulty_features"]["intro_span_semesters"])
    level_changed = sum(1 for o, n in zip(old, new) if o["difficulty"] != n["difficulty"])
    out = []
    for o, n in zip(old, new):
        assert o["id"] == n["id"]
        o = dict(o)
        o["difficulty"] = n["difficulty"]
        o["difficulty_features"] = n["difficulty_features"]
        out.append(o)
    if write:
        write_json(DATA_DIR / "archetypes.json", out)
    return {"n_archetypes": len(old), "span_feature_changed": span_changed, "difficulty_level_changed": level_changed,
            "levels": dict(sorted(Counter(a["difficulty"] for a in out).items())), "written": write}


def stale_archetypes(archetypes: list[dict], kps: dict[str, dict], lessons_book: dict[str, str]) -> list[str]:
    """不变量用：返回难度特征与当前引入位置不一致的题型 ID。"""
    new = recompute_archetypes(archetypes, kps, lessons_book)
    return [o["id"] for o, n in zip(archetypes, new)
            if o["difficulty_features"].get("intro_span_semesters") != n["difficulty_features"]["intro_span_semesters"] or o["difficulty"] != n["difficulty"]]
