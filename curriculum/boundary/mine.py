"""从教材习题实例（operand_features + text 原文）确定性地得到 `ItemFeatures`——「教材实际用到什么」的观测证据。

挖掘规则保守：只解析 `数 运算符 数` 的二元片段（尊重运算优先级，不解析括号外的片段），单位只在有数字/空格/括号
前缀时才计入，几何词汇只取 ≥2 字且不在泛用词表中的词。解析不到的实例对该维度无约束。
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Iterable, Optional

from curriculum.boundary.vocab import (
    ANY_FRACTION,
    GEOMETRY_MINE_EXCLUDE,
    GEOMETRY_SEED,
    UNIT_ALIASES,
    analyze_operation,
    norm_term,
    parse_num,
    fraction_type_of,
)
from curriculum.models import ExerciseInstance, ItemFeatures, OperationUse

# Stage 1 把「编码」当成了数量：公民身份号码是 18 位编码，不是一个 18 位整数（D21）
NON_QUANTITY_INTEGER_INSTANCES = {"ex.g4a.u12.l04.01"}

_NUM = r"(?:\d+又\d+/\d+|\d+(?:\.\d+)?(?:/\d+)?)(?!%)"
_OPS = "+\\-×÷*"
_CHAIN = re.compile(rf"(?<![\d./])({_NUM})((?:\s*[{_OPS}]\s*{_NUM})+)(?![\d./])")
_PIECE = re.compile(rf"\s*([{_OPS}])\s*({_NUM})")
_PREC = {"+": 1, "-": 1, "×": 2, "÷": 2, "*": 2}
_OPNAME = {"+": "加法", "-": "减法", "×": "乘法", "*": "乘法", "÷": "除法"}


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text).replace("−", "-").replace("–", "-").replace("—", "-")


def mine_operations(text: str) -> list[OperationUse]:
    """从题面文字里解析出具体的二元运算（尊重优先级；只取两个操作数都是原子的片段）。"""
    text = _nfkc(text)
    out: list[OperationUse] = []
    for m in _CHAIN.finditer(text):
        if re.match(r"[一-鿿]", text[m.end(): m.end() + 1]) and not re.match(r"[余…]", text[m.end(): m.end() + 1]):
            continue  # 「10×10方格」「3×4的积」这类描述不是在做运算
        nums = [m.group(1)]
        ops = []
        for pm in _PIECE.finditer(m.group(2)):
            ops.append(pm.group(1))
            nums.append(pm.group(2))
        for i, op in enumerate(ops):
            if i > 0 and _PREC[ops[i - 1]] >= _PREC[op]:
                continue  # 左操作数是前一步的结果
            if i + 1 < len(ops) and _PREC[ops[i + 1]] > _PREC[op]:
                continue  # 右操作数是后一步的结果
            mode = None
            if op == "÷":
                a, b = parse_num(nums[i]), parse_num(nums[i + 1])
                mode = "unknown"
                if a and b and a.kind == "int" and b.kind == "int" and b.value != 0 and a.value % b.value != 0:
                    tail = text[m.end(): m.end() + 14]
                    if "…" in tail or "..." in tail or "余" in tail:
                        mode = "remainder"
                    elif re.match(r"\s*=\s*\d+\.\d+", tail):
                        mode = "decimal_quotient"
                else:
                    mode = None
            out.append(OperationUse(op=_OPNAME[op], operands=[nums[i], nums[i + 1]], mode=mode))
    return out


# ------------------------------------------------------------------ 单位挖掘

_EXEMPT_SINGLE_AFTER = {"角": "形", "时": "间候刻"}  # 「三角形」「时间」之类的复合词


@lru_cache(maxsize=1)
def _unit_regex() -> tuple[re.Pattern, dict[str, str]]:
    toks: dict[str, str] = {}
    for canon, aliases in UNIT_ALIASES.items():
        if canon == "分(货币)":
            continue  # 「分」与「分钟」同形，不从文本挖
        if canon == "度":
            toks["°"] = canon  # 裸「度」太泛，只认符号
            continue
        for a in [canon, *aliases]:
            k = unicodedata.normalize("NFKC", a).lower()
            if "(" in k or "（" in k or "，" in k or k in ("分钟(时间)",):
                continue
            toks[k] = canon
    toks.pop("分钟", None)
    toks["分钟"] = "分钟"
    toks.pop("时", None)
    items = sorted(toks, key=len, reverse=True)
    parts = []
    for t in items:
        esc = re.escape(t)
        latin = bool(re.fullmatch(r"[a-z0-9²³/]+", t))
        if latin:  # 拉丁缩写要求前面是数字
            parts.append(rf"(?<=\d)\s?{esc}(?![a-z0-9])")
        elif len(t) == 1:  # 单字单位要求前面是数字/下划线/括号/空格
            parts.append(rf"(?:(?<=\d)|(?<=[_)\s])){esc}")
        else:
            parts.append(esc)
    parts.append(r"(?<=\d)\s?小时")
    toks["小时"] = "时"
    return re.compile("|".join(parts)), toks


def mine_units(text: str) -> set[str]:
    text = _nfkc(text).lower()
    rx, toks = _unit_regex()
    out = set()
    for m in rx.finditer(text):
        tok = m.group(0).strip()
        canon = toks.get(tok)
        if not canon:
            continue
        nxt = text[m.end(): m.end() + 1]
        if tok in _EXEMPT_SINGLE_AFTER and nxt and nxt in _EXEMPT_SINGLE_AFTER[tok]:
            continue
        out.add(canon)
    return out


# ------------------------------------------------------------------ 几何词汇挖掘


@lru_cache(maxsize=8)
def _geo_regex(vocab_key: tuple[str, ...]) -> tuple[re.Pattern, dict[str, str]]:
    norm2orig = {}
    for t in vocab_key:
        n = norm_term(t)
        if len(n) >= 2 and t not in GEOMETRY_MINE_EXCLUDE and n not in GEOMETRY_MINE_EXCLUDE:
            norm2orig[n] = t
    items = sorted(norm2orig, key=len, reverse=True)
    return re.compile("|".join(re.escape(i) for i in items)), norm2orig


def mine_geometry(text: str, vocab: Iterable[str] = ()) -> set[str]:
    key = tuple(sorted(set(GEOMETRY_SEED) | set(vocab)))
    rx, n2o = _geo_regex(key)
    t = norm_term(_nfkc(text))
    return {n2o[m.group(0)] for m in rx.finditer(t)}


# ------------------------------------------------------------------ 分数类型挖掘

_FRAC_TOKEN = re.compile(r"(?<![\d./])(\d+又\d+/\d+|\d+/\d+)(?![\d./])")


def mine_fraction_types(text: str) -> set[str]:
    out = set()
    for m in _FRAC_TOKEN.finditer(_nfkc(text)):
        n = parse_num(m.group(1))
        t = fraction_type_of(n) if n else None
        if t:
            out.add(t)
    if re.search(r"带分数", text):
        out.add("带分数")
    if re.search(r"假分数", text):
        out.add("假分数")
    if re.search(r"真分数", text):
        out.add("真分数")
    return out


# ------------------------------------------------------------------ 实例 -> 特征


def instance_features(e: ExerciseInstance | dict, kp_names: Optional[dict[str, str]] = None, geo_vocab: Iterable[str] = ()) -> ItemFeatures:
    """教材习题实例的结构化特征（观测证据）。

    整数数域：按位数取下界 10^(d-1)（实例只记录了位数，不知道具体值；下界保证不把合法实例误判为越界）。
    """
    d = e if isinstance(e, dict) else e.model_dump(mode="json")
    of = d.get("operand_features") or {}
    text = d.get("text") or ""
    types = set(of.get("number_types") or [])
    f = ItemFeatures()
    digits = of.get("integer_digits")
    if d.get("id") in NON_QUANTITY_INTEGER_INSTANCES:
        digits = None
    if digits:
        f.integer_max = 0 if digits <= 1 else 10 ** (digits - 1)
    dp = of.get("decimal_places")
    if dp:
        f.decimal_places = dp
    elif "小数" in types:
        f.decimal_places = 1  # 用了小数但没记录位数：至少一位
    ft = mine_fraction_types(text) if "分数" in types else set()  # 只在 Stage 1 判定涉及分数时才挖（避免「1/2/3」「日期」误报）
    if "分数" in types and not ft:
        ft = {ANY_FRACTION}
    f.fraction_types = ft
    f.operations = mine_operations(text)
    if of.get("requires_carry_or_borrow"):
        f.requires_carry_or_borrow = True
    f.units_of_measure = mine_units(text)
    f.geometry_vocab = mine_geometry(text, geo_vocab)
    if kp_names and d.get("primary_knowledge_point_id") in kp_names:
        f.concepts = {kp_names[d["primary_knowledge_point_id"]]}
    return f
