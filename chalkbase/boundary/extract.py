"""Stage 6 · 从自然语言题面抽取 `ItemFeatures`（验证闭环里「题目 → 结构化特征」的辅助）。

只负责「读题」：把题目里出现的数、需要的运算、概念、单位、几何词汇列出来；是否越界由确定性的 `check_item` 判断。
模型：流水线模型 qwen3.7-plus 非思考（D13），不在标注模型组内。概念以知识点名称为候选菜单（边界里每个知识点名称自动是概念）。
"""
from __future__ import annotations

import json
from typing import Any, Optional

from chalkbase.annotate.client import AnnotationClient, AnnotationRequest
from chalkbase.boundary.vocab import FRACTION_TYPES, GEOMETRY_SEED, OPS, UNITS, norm_op, norm_unit
from chalkbase.common import DATA_DIR, read_json
from chalkbase.models import ItemFeatures, OperationUse

MODEL = "qwen3.7-plus"

FEATURE_SPEC = f"""输出一个 JSON 对象，字段：
- "integer_max"：整数或 null。题目中出现的最大整数，包括解题所需运算的结果与小数的整数部分；不含年份、编号、身份证号等编码。
- "decimal_places"：整数或 null。题目中出现的小数的最大小数位数（没有小数写 null）。
- "fraction_types"：数组，从 {json.dumps(FRACTION_TYPES, ensure_ascii=False)} 中选（几分之一=分子为1；几分之几=分子大于1的真分数；真分数/假分数/带分数仅当题目直接涉及这些名称或数）。无分数写 []。
- "operations"：数组，解题所需的**全部二元运算**，按解题步骤拆开，每项 {{"op": {json.dumps(OPS, ensure_ascii=False)} 之一, "operands": ["操作数1", "操作数2"], "mode": null}}。操作数用字符串写具体数值：整数 "356"、小数 "0.25"、分数 "3/4"、带分数 "1又1/2"。整数除法除不尽时 mode 填 "remainder"（商和余数）或 "decimal_quotient"（商写成小数），否则 null。多步运算要写出每一步的实际操作数（含中间结果）。不涉及计算的题写 []。
- "concepts"：数组，解此题的**核心考点**，只能从给定的「知识点名称菜单」中原样选取 0～2 个：只选最贴切、最具体的那个（题目考「求平均数」就选平均数的知识点）；不要把基础运算、情境背景、更基础的前置知识也列上；菜单里没有合适的写 []。
- "units_of_measure"：数组，题目用到的计量单位，只能从 {json.dumps(UNITS, ensure_ascii=False)} 中选；年、月、日、个、只等日常单位不写。
- "geometry_vocab"：数组，题目用到的几何词汇，优先用这些规范词：{' '.join(GEOMETRY_SEED)}；没有写 []。"""

SYSTEM = "你是小学数学题目的结构化分析助手。你只读题、列特征，不判断题目难不难、超不超纲。\n\n" + FEATURE_SPEC + "\n\n只输出 JSON，不要多余文字。"


def kp_name_menu() -> list[str]:
    return [k["name"] for k in read_json(DATA_DIR / "knowledge_points.json")]


def render_user(problem: str, menu: list[str]) -> str:
    return f"【知识点名称菜单】\n{'；'.join(menu)}\n\n【题目】\n{problem}"


def validate(d: Any) -> bool:
    return isinstance(d, dict) and isinstance(d.get("operations", []), list)


def to_features(d: dict, menu: Optional[set[str]] = None) -> ItemFeatures:
    """模型输出 → ItemFeatures（容错：丢弃不合法的项）。"""
    f = ItemFeatures()

    def _int(v):
        try:
            return int(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None

    f.integer_max = _int(d.get("integer_max"))
    f.decimal_places = _int(d.get("decimal_places")) or None
    f.fraction_types = {t for t in d.get("fraction_types") or [] if t in FRACTION_TYPES}
    for o in d.get("operations") or []:
        try:
            op = norm_op(str(o.get("op", "")))
            ops = [str(x) for x in o.get("operands", [])]
            if op and len(ops) == 2:
                mode = o.get("mode") if o.get("mode") in ("remainder", "decimal_quotient") else None
                f.operations.append(OperationUse(op=op, operands=ops, mode=mode))
        except (AttributeError, TypeError):
            continue
    f.concepts = {str(c) for c in d.get("concepts") or [] if not menu or str(c) in menu}
    f.units_of_measure = {u for u in (norm_unit(str(x)) for x in d.get("units_of_measure") or []) if u}
    f.geometry_vocab = {str(g) for g in d.get("geometry_vocab") or []}
    return f


THINKING = True  # 思考模式抽取：val 上概念查全 0.87→0.96（费用约 0.02 元/题），D28


def extract_features_batch(problems: dict[str, str], client: Optional[AnnotationClient] = None) -> dict[str, tuple[ItemFeatures, dict]]:
    """{id: 题面} → {id: (features, 原始模型输出)}。失败的条目不在结果里。"""
    client = client or AnnotationClient(max_workers=24)
    menu = kp_name_menu()
    reqs = [AnnotationRequest(request_id=f"ext:{i}", model=MODEL, thinking=THINKING,
                              messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": render_user(p, menu)}],
                              response_schema_validator=validate, max_tokens=6000 if THINKING else 1200)
            for i, p in problems.items()]
    out = {}
    for r in client.run_batch(reqs, label="stage6-extract"):
        if r.ok:
            out[r.request_id.split(":", 1)[1]] = (to_features(r.parsed, set(menu)), r.parsed)
    return out


def extract_features(problem: str, client: Optional[AnnotationClient] = None) -> ItemFeatures:
    res = extract_features_batch({"x": problem}, client)
    if "x" not in res:
        raise RuntimeError("特征抽取失败")
    return res["x"][0]
