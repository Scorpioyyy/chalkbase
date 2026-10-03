"""Stage 6 · 校验函数：给定题目的结构化特征与课时 ID，判断是否越界、越在哪一维。

全部确定性：不调用语言模型。词表外的取值不算越界，列入 `unknown`。
对外接口：`boundary(lesson_id)`、`check_item(features, lesson_id)`、`not_yet_learned(lesson_id)`（Stage 7 查询接口调用）。
"""
from __future__ import annotations

import bisect
from functools import lru_cache
from pathlib import Path
from typing import Optional

from curriculum.boundary.fold import lesson_key
from curriculum.boundary.vocab import (
    ANY_FRACTION,
    FRACTION_TYPES,
    GEOMETRY_SEED,
    OP_TAGS,
    analyze_operation,
    fraction_closure,
    norm_op,
    norm_set,
    norm_term,
    norm_unit,
    op_closure,
)
from curriculum.common import DATA_DIR, read_json
from curriculum.models import BoundaryReport, BoundaryViolation, CapabilityBoundary, ItemFeatures

BOUNDARIES_PATH = DATA_DIR / "boundaries.json"
# 日常时间单位与计数单位：不参与判定（既不算越界也不算未知）
EVERYDAY_UNITS = {"年", "月", "日", "天", "周", "星期", "世纪", "个", "只", "本", "支", "张", "人", "次", "页", "条", "件", "份", "组", "块", "辆", "棵"}
_GEO_SEED_NORM = norm_set(GEOMETRY_SEED)
# 日常用语里的方位/度量单字词：不当作几何词汇判定
EVERYDAY_GEOMETRY = {"上", "下", "左", "右", "前", "后", "长", "宽", "高", "边", "面", "点", "线", "底"}


class BoundaryStore:
    """data/boundaries.json 的内存视图（也可由 `build_store` 直接从折叠结果构造，不落盘）。"""

    def __init__(self, data: dict):
        self.lessons: list[str] = data["lessons"]
        self._rank = {l: i for i, l in enumerate(self.lessons)}
        self._raw = data["boundaries"]
        self._delta = data.get("format") == "delta_v1"
        self._full: dict[str, dict] = {}
        if self._delta:  # 逐课时累加还原完整边界
            sets = {d: set() for d in ("fraction_types", "concepts", "units_of_measure", "geometry_vocab")}
            ops: dict[str, set[str]] = {}
            for lid in self.lessons:
                row = self._raw[lid]
                for d in sets:
                    sets[d] |= set(row["add"].get(d, []))
                for op, forms in row["add"].get("operation_operand_forms", {}).items():
                    ops.setdefault(op, set()).update(forms)
                self._full[lid] = {"lesson_id": lid, "integer_domain_max": row["integer_domain_max"],
                                   "decimal_max_places": row["decimal_max_places"],
                                   **{d: set(v) for d, v in sets.items()}, "operation_operand_forms": {k: set(v) for k, v in ops.items()}}
        self.introduced_at: dict = data["introduced_at"]
        self.kp_intro: list[list] = data.get("kp_intro", [])  # [[课时, [kp_id, ...]], ...]，按课时顺序
        self._cache: dict[str, CapabilityBoundary] = {}
        self._norm_cache: dict[tuple[str, str], set[str]] = {}
        self._intro_norm = {
            "concepts": {norm_term(k): v for k, v in self.introduced_at.get("concepts", {}).items()},
            "geometry_vocab": {norm_term(k): v for k, v in self.introduced_at.get("geometry_vocab", {}).items()},
        }

    def has_lesson(self, lesson_id: str) -> bool:
        return lesson_id in self._rank

    def rank(self, lesson_id: str) -> int:
        return self._rank[lesson_id]

    def boundary(self, lesson_id: str) -> CapabilityBoundary:
        if lesson_id not in self._raw:
            raise KeyError(f"未知课时：{lesson_id}")
        b = self._cache.get(lesson_id)
        if b is None:
            b = CapabilityBoundary(**(self._full[lesson_id] if self._delta else self._raw[lesson_id]))
            self._cache[lesson_id] = b
        return b

    def norm_set(self, lesson_id: str, dim: str) -> set[str]:
        k = (lesson_id, dim)
        if k not in self._norm_cache:
            self._norm_cache[k] = norm_set(getattr(self.boundary(lesson_id), dim))
        return self._norm_cache[k]

    def not_yet_learned(self, lesson_id: str) -> list[str]:
        """尚未学习：首次引入课时晚于 lesson_id 的全部知识点 ID。"""
        r = self.rank(lesson_id)
        out: list[str] = []
        for lid, kps in self.kp_intro:
            if self._rank.get(lid, -1) > r:
                out.extend(kps)
        return out

    def _first_reaching(self, key: str, need: int) -> Optional[str]:
        for lid, v in self.introduced_at.get(key, []):
            if v >= need:
                return lid
        return None


@lru_cache(maxsize=1)
def default_store() -> BoundaryStore:
    if not BOUNDARIES_PATH.exists():
        raise FileNotFoundError("data/boundaries.json 不存在，先运行 `python -m curriculum.boundary build`")
    return BoundaryStore(read_json(BOUNDARIES_PATH))


def reload_default_store() -> None:
    default_store.cache_clear()


def boundary(lesson_id: str, store: Optional[BoundaryStore] = None) -> CapabilityBoundary:
    """课时 lesson_id 结束时学生已具备的能力边界。"""
    return (store or default_store()).boundary(lesson_id)


def not_yet_learned(lesson_id: str, store: Optional[BoundaryStore] = None) -> list[str]:
    return (store or default_store()).not_yet_learned(lesson_id)


def _fmt_set(s, limit: int = 12) -> str:
    s = sorted(s)
    return "、".join(s[:limit]) + (f"…（共{len(s)}项）" if len(s) > limit else "") if s else "（无）"


def check_item(features: ItemFeatures | dict, lesson_id: str, store: Optional[BoundaryStore] = None) -> BoundaryReport:
    """逐维度校验。越界 = 题目用到了该课时边界之外的能力。"""
    if isinstance(features, dict):
        features = ItemFeatures(**features)
    st = store or default_store()
    b = st.boundary(lesson_id)
    vios: list[BoundaryViolation] = []
    unknown: list[str] = []

    # ---- 先把具体运算展开成形态/数值事实
    forms: dict[str, set[str]] = {}
    for op, tags in features.operation_forms.items():
        name = norm_op(op)
        if name is None:
            unknown.append(f"运算:{op}")
            continue
        forms.setdefault(name, set()).update(tags)
    int_vals: list[int] = [features.integer_max] if features.integer_max is not None else []
    places = features.decimal_places or 0
    ftypes = set(features.fraction_types)
    for use in features.operations:
        ana = analyze_operation(use.op, use.operands[0], use.operands[1], use.mode)
        if ana is None:
            unknown.append(f"运算无法解析:{use.op}{use.operands}")
            continue
        forms.setdefault(norm_op(use.op) or use.op, set()).update(ana.forms)
        int_vals += ana.int_values
        places = max(places, ana.places)
        ftypes |= ana.fraction_types

    # ---- 整数数域
    need = max(int_vals, default=0)
    if need > (b.integer_domain_max or 0):
        vios.append(BoundaryViolation(
            dimension="integer_domain", item_value=str(need), allowed=str(b.integer_domain_max or 0),
            introduced_at=st._first_reaching("integer_domain", need),
            detail=f"题目出现整数 {need}，超过该课时已学数域上限 {b.integer_domain_max or 0}"))
    # ---- 小数位数
    if places > (b.decimal_max_places or 0):
        vios.append(BoundaryViolation(
            dimension="decimal_places", item_value=str(places), allowed=str(b.decimal_max_places or 0),
            introduced_at=st._first_reaching("decimal_places", places),
            detail=f"题目用到 {places} 位小数，超过该课时已学的最多 {b.decimal_max_places or 0} 位"))
    # ---- 分数类型
    allowed_f = fraction_closure(set(b.fraction_types))
    for t in sorted(ftypes):
        if t == ANY_FRACTION:
            if not b.fraction_types:
                vios.append(BoundaryViolation(dimension="fraction_types", item_value=t, allowed=_fmt_set(b.fraction_types),
                                              introduced_at=min(st.introduced_at.get("fraction_types", {}).values(), key=lesson_key, default=None),
                                              detail="题目用到分数，但该课时尚未学习分数"))
        elif t not in FRACTION_TYPES:
            unknown.append(f"分数类型:{t}")
        elif t not in allowed_f:
            vios.append(BoundaryViolation(dimension="fraction_types", item_value=t, allowed=_fmt_set(b.fraction_types),
                                          introduced_at=st.introduced_at.get("fraction_types", {}).get(t),
                                          detail=f"分数类型「{t}」尚未学习"))
    # ---- 运算操作数形态
    for op, tags in sorted(forms.items()):
        known = set(OP_TAGS.get(op, []))
        for t in sorted(tags - known):
            unknown.append(f"{op}形态:{t}")
        allowed = op_closure(op, set(b.operation_operand_forms.get(op, set())))
        miss = (tags & known) - allowed
        if miss:
            intro = [st.introduced_at.get("operation_forms", {}).get(op, {}).get(t) for t in sorted(miss)]
            intro = [x for x in intro if x]
            vios.append(BoundaryViolation(
                dimension="operation_forms", item_value=f"{op}：{'、'.join(sorted(miss))}",
                allowed=f"{op}：{_fmt_set(b.operation_operand_forms.get(op, set()))}",
                introduced_at=max(intro, key=lesson_key) if intro else None,
                detail=f"{op}的操作数形态尚未学习：{'、'.join(sorted(miss))}"))
    if features.requires_carry_or_borrow:
        if "进位" not in op_closure("加法", set(b.operation_operand_forms.get("加法", set()))) and \
           "退位" not in op_closure("减法", set(b.operation_operand_forms.get("减法", set()))):
            ia = st.introduced_at.get("operation_forms", {})
            cands = [x for x in (ia.get("加法", {}).get("进位"), ia.get("减法", {}).get("退位")) if x]
            vios.append(BoundaryViolation(dimension="operation_forms", item_value="进位/退位", allowed="（尚无进位/退位）",
                                          introduced_at=min(cands, key=lesson_key) if cands else None,
                                          detail="题目需要进位或退位，该课时尚未学习"))
    # ---- 计量单位
    for u in sorted(features.units_of_measure):
        c = norm_unit(u)
        if c is None:
            if u not in EVERYDAY_UNITS:
                unknown.append(f"单位:{u}")
        elif c not in b.units_of_measure:
            vios.append(BoundaryViolation(dimension="units_of_measure", item_value=c, allowed=_fmt_set(b.units_of_measure),
                                          introduced_at=st.introduced_at.get("units_of_measure", {}).get(c),
                                          detail=f"计量单位「{c}」尚未学习"))
    # ---- 几何词汇 / 概念（规范化后精确匹配）
    for dim, terms in (("geometry_vocab", features.geometry_vocab), ("concepts", features.concepts)):
        have = st.norm_set(lesson_id, dim)
        for t in sorted(terms):
            n = norm_term(t)
            if not n or n in have or (dim == "geometry_vocab" and n in EVERYDAY_GEOMETRY):
                continue
            intro = st._intro_norm[dim].get(n)
            if intro is None and not (dim == "geometry_vocab" and n in _GEO_SEED_NORM):
                unknown.append(f"{'几何词汇' if dim == 'geometry_vocab' else '概念'}:{t}")
                continue
            vios.append(BoundaryViolation(dimension=dim, item_value=t, allowed=f"（共 {len(getattr(b, dim))} 项）", introduced_at=intro,
                                          detail=f"{'几何词汇' if dim == 'geometry_vocab' else '概念'}「{t}」尚未学习"
                                                 + ("" if intro else "（教材未引入）")))
    return BoundaryReport(lesson_id=lesson_id, in_bounds=not vios, verdict=_verdict(vios, lesson_id, st), violations=vios, unknown=unknown)


def _unit_of(lesson_id: str) -> str:
    return lesson_id.rsplit(".", 1)[0]


def _verdict(vios: list[BoundaryViolation], lesson_id: str, st: BoundaryStore) -> str:
    """边界附近的模糊：教材里同一单元内相邻课时讲的常是同一类内容，习题也常在前一课时先行探索（教材自身有 23 个这样的实例），
    所以「全部违例都是在同一单元内稍后才引入」的题判 borderline（建议人工复核），其余越界判 out。"""
    if not vios:
        return "in"
    r0 = st.rank(lesson_id) if st.has_lesson(lesson_id) else 0
    soft = all(v.introduced_at and st.has_lesson(v.introduced_at) and st.rank(v.introduced_at) > r0
               and _unit_of(v.introduced_at) == _unit_of(lesson_id) for v in vios)
    return "borderline" if soft else "out"
