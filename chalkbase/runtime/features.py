"""由题型参数确定性推出题目的数值特征（`ItemFeatures`），供能力边界校验使用。"""
from __future__ import annotations

from chalkbase.boundary.vocab import fraction_type_of, parse_num
from chalkbase.models import ItemFeatures


def slot_types(slots: dict) -> dict[str, str]:
    """槽位类型表，沙箱据此把参数解析为 int / Decimal / Fraction；choice 槽位按字符串传入。"""
    return {k: ("str" if v.get("type") == "choice" else v.get("type", "str")) for k, v in slots.items()}


def params_features(params: dict, slots: dict, result=None) -> ItemFeatures:
    """参数 → `ItemFeatures`：最大整数、小数位数、分数类型。`choice` 槽位与布尔值不计。

    `result` 给定时其数值也计入（实例化默认不计：答案是求解结果，教材同类题的答案本来就可能比参数大）。
    概念、计量单位、几何词汇不在数值参数里，需要时由调用方补充（见 `chalkbase.boundary.extract`）。"""
    ints, places, ftypes = [], 0, set()
    vals = [(slots.get(k, {}).get("type"), v) for k, v in params.items()]
    if result is not None:
        vals += [(None, x) for x in (result if isinstance(result, list) else [result])]
    for t, v in vals:
        if t == "choice" or isinstance(v, bool):
            continue
        n = parse_num(str(v))
        if n is None:
            continue
        if n.kind == "int":
            ints.append(int(n.value))
        elif n.kind == "dec":
            ints.append(int(n.value))
            places = max(places, n.places)
        elif n.kind == "frac":
            if n.den == 1:
                ints.append(n.num)
            else:
                ft = fraction_type_of(n)
                if ft:
                    ftypes.add(ft)
    return ItemFeatures(integer_max=max(ints, default=None), decimal_places=places or None, fraction_types=ftypes)
