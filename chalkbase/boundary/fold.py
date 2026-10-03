"""Stage 6 · 单调折叠：课时 L 的能力边界 = L 及之前引入的所有知识点 grants 的半格合并。

半格合并：整数数域上限、小数位数取 max；分数类型、操作数形态、概念、计量单位、几何词汇取并集。
这里的函数都是纯函数（不读磁盘），输入显式传入，便于性质测试。
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

from chalkbase.common import book_index
from chalkbase.models import CapabilityBoundary, CapabilityGrant

_LESSON_RE = re.compile(r"^(g\d[ab])\.u(\d+)\.l(\d+)$")
SET_DIMS = ("fraction_types", "concepts", "units_of_measure", "geometry_vocab")
MAX_DIMS = ("integer_domain_max", "decimal_max_places")


def lesson_key(lesson_id: str) -> tuple[int, int, int]:
    m = _LESSON_RE.match(lesson_id)
    if not m:
        raise ValueError(f"非法课时 ID：{lesson_id}")
    return (book_index(m.group(1)), int(m.group(2)), int(m.group(3)))


def order_lessons(lesson_ids: Iterable[str]) -> list[str]:
    return sorted(set(lesson_ids), key=lesson_key)


class Accumulator:
    """能力状态半格：只能 merge（合并只增不减），snapshot 得到某一时刻的 CapabilityBoundary。"""

    def __init__(self) -> None:
        self.integer_domain_max: Optional[int] = None
        self.decimal_max_places: Optional[int] = None
        self.sets: dict[str, set[str]] = {d: set() for d in SET_DIMS}
        self.ops: dict[str, set[str]] = {}

    def merge(self, g: CapabilityGrant) -> dict:
        """合并一个 grant，返回本次**新增**的内容（用于记录「何时引入」）。"""
        added: dict = {}
        for d in MAX_DIMS:
            v = getattr(g, d)
            cur = getattr(self, d)
            if v is not None and (cur is None or v > cur):
                setattr(self, d, v)
                added[d] = v
        for d in SET_DIMS:
            new = set(getattr(g, d)) - self.sets[d]
            if new:
                self.sets[d] |= new
                added[d] = new
        for op, forms in g.operation_operand_forms.items():
            new = set(forms) - self.ops.get(op, set())
            if new:
                self.ops.setdefault(op, set()).update(new)
                added.setdefault("operation_operand_forms", {})[op] = new
        return added

    def snapshot(self, lesson_id: str) -> CapabilityBoundary:
        return CapabilityBoundary(
            lesson_id=lesson_id,
            integer_domain_max=self.integer_domain_max,
            decimal_max_places=self.decimal_max_places,
            fraction_types=set(self.sets["fraction_types"]),
            operation_operand_forms={k: set(v) for k, v in self.ops.items()},
            concepts=set(self.sets["concepts"]),
            units_of_measure=set(self.sets["units_of_measure"]),
            geometry_vocab=set(self.sets["geometry_vocab"]),
        )


def fold_boundaries(
    ordered_lessons: list[str], grants_at: dict[str, list[CapabilityGrant]]
) -> tuple[dict[str, CapabilityBoundary], dict]:
    """沿课时顺序折叠。grants_at[课时] = 在该课时引入的所有 grants（顺序无关）。

    返回 (课时 → 边界, introduced_at)。introduced_at 记录每个能力最早被引入的课时：
      {"integer_domain": [[课时, 新上限], ...], "decimal_places": [...],
       "fraction_types": {类型: 课时}, "operation_forms": {运算: {标签: 课时}},
       "concepts": {概念: 课时}, "units_of_measure": {...}, "geometry_vocab": {...}}
    """
    acc = Accumulator()
    out: dict[str, CapabilityBoundary] = {}
    intro: dict = {"integer_domain": [], "decimal_places": [], "fraction_types": {}, "operation_forms": {},
                   "concepts": {}, "units_of_measure": {}, "geometry_vocab": {}}
    for lid in ordered_lessons:
        for g in grants_at.get(lid, []):
            added = acc.merge(g)
            if "integer_domain_max" in added:
                intro["integer_domain"].append([lid, added["integer_domain_max"]])
            if "decimal_max_places" in added:
                intro["decimal_places"].append([lid, added["decimal_max_places"]])
            for d, key in (("fraction_types", "fraction_types"), ("concepts", "concepts"),
                           ("units_of_measure", "units_of_measure"), ("geometry_vocab", "geometry_vocab")):
                for t in added.get(d, ()):
                    intro[key].setdefault(t, lid)
            for op, forms in added.get("operation_operand_forms", {}).items():
                for t in forms:
                    intro["operation_forms"].setdefault(op, {}).setdefault(t, lid)
        out[lid] = acc.snapshot(lid)
    # 同一课时内多次增大的上限，只保留该课时最终值
    for k in ("integer_domain", "decimal_places"):
        last: dict[str, int] = {}
        for lid, v in intro[k]:
            last[lid] = max(v, last.get(lid, v))
        intro[k] = [[lid, v] for lid, v in last.items()]
    return out, intro


def boundary_leq(a: CapabilityBoundary, b: CapabilityBoundary) -> bool:
    """半格序：a ≤ b 当且仅当 b 在每个维度上都不小于 a。"""
    if (a.integer_domain_max or 0) > (b.integer_domain_max or 0):
        return False
    if (a.decimal_max_places or 0) > (b.decimal_max_places or 0):
        return False
    for d in SET_DIMS:
        if not getattr(a, d) <= getattr(b, d):
            return False
    for op, forms in a.operation_operand_forms.items():
        if not set(forms) <= set(b.operation_operand_forms.get(op, set())):
            return False
    return True
