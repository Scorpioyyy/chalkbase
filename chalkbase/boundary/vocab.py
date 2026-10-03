"""Stage 6 受控词表与确定性匹配（决策见 docs/design.md D21）。

能力边界的每个维度都必须能被校验函数确定性地比对：
  * 整数数域 / 小数位数：整数；
  * 分数类型：5 个枚举，带蕴含闭包；
  * 运算操作数形态：每个运算一张标签表，形态由**具体操作数**确定性推出（`derive_forms`），不靠语言模型判断；
  * 计量单位：规范名 + 别名表；日常时间单位（年月日周天）与计数单位不进词表；
  * 几何词汇、概念：开放词表，但统一经 `norm_term` 规范化后做精确匹配。
小数、分数一律用 Decimal / Fraction，禁止浮点。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction
from typing import Optional

# ------------------------------------------------------------------ 分数类型

FRACTION_TYPES = ["几分之一", "几分之几", "真分数", "假分数", "带分数"]
ANY_FRACTION = "分数"  # 特征侧的泛指：只知道用了分数、不知道类型；边界只要有任一分数类型即放行
# 包含关系：学会右侧类型以左侧为前提。假分数、带分数以真分数为前提（先认识真分数，才有「分子不小于分母」与整数部分加真分数部分）
FRACTION_IMPLIES = {
    "真分数": {"几分之几", "几分之一"},
    "几分之几": {"几分之一"},
    "假分数": {"真分数", "几分之几", "几分之一"},
    "带分数": {"真分数", "几分之几", "几分之一"},
}


def fraction_closure(types: set[str]) -> set[str]:
    out = set(types)
    for t in list(types):
        out |= FRACTION_IMPLIES.get(t, set())
    return out


# ------------------------------------------------------------------ 运算操作数形态

OPS = ["加法", "减法", "乘法", "除法"]
OP_TAGS: dict[str, list[str]] = {
    "加法": ["整数", "进位", "小数", "小数·位数不同", "分数·同分母", "分数·异分母", "分数·带分数"],
    "减法": ["整数", "退位", "小数", "小数·位数不同", "分数·同分母", "分数·异分母", "分数·带分数"],
    "乘法": ["整数·表内", "整数·乘数一位数", "整数·乘数两位数", "整数·乘数三位数及以上", "小数·乘整数", "小数·乘小数", "分数·乘整数", "分数·乘分数"],
    "除法": ["整数·表内", "整数·除数一位数", "整数·除数两位数", "整数·除数三位数及以上", "整数·有余数", "整数·商为小数",
            "小数·除以整数", "小数·除数是小数", "分数·除以整数", "分数·除数是分数"],
}
# 有序族：族内高位蕴含低位（学会乘数两位数即会乘数一位数与表内）
OP_ORDERED_FAMILIES: dict[str, list[str]] = {
    "乘法": ["整数·表内", "整数·乘数一位数", "整数·乘数两位数", "整数·乘数三位数及以上"],
    "除法": ["整数·表内", "整数·除数一位数", "整数·除数两位数", "整数·除数三位数及以上"],
}
OP_IMPLIES: dict[str, dict[str, set[str]]] = {
    "加法": {"进位": {"整数"}, "小数·位数不同": {"小数"}, "分数·异分母": {"分数·同分母"}},
    "减法": {"退位": {"整数"}, "小数·位数不同": {"小数"}, "分数·异分母": {"分数·同分母"}},
    "乘法": {"小数·乘小数": {"小数·乘整数"}, "分数·乘分数": {"分数·乘整数"}},
    "除法": {"小数·除数是小数": {"小数·除以整数"}, "分数·除数是分数": {"分数·除以整数"}},
}


# 有序族里「高位」标签必须有知识点名称/描述里的关键词支撑（语义补全容易把习题里顺带出现的算式当成新教的）
ORDERED_TAG_SUPPORT: dict[tuple[str, str], tuple[str, ...]] = {
    ("乘法", "整数·乘数两位数"): ("两位数乘两位数", "三位数乘两位数", "乘数是两位数", "两位数的乘法", "乘数为两位数", "多位数乘多位数", "三位数乘三位数"),
    ("乘法", "整数·乘数三位数及以上"): ("三位数乘三位数", "乘数是三位数", "乘数为三位数", "多位数乘多位数", "多位数乘三位数"),
    ("除法", "整数·除数两位数"): ("除数是两位数", "除数为两位数", "除以两位数", "两位数除", "多位数除以多位数"),
    ("除法", "整数·除数三位数及以上"): ("除数是三位数", "除数为三位数", "除以三位数", "三位数除", "多位数除以多位数", "除数是多位数", "除数为多位数"),
}


def op_closure(op: str, tags: set[str]) -> set[str]:
    out = set(tags)
    fam = OP_ORDERED_FAMILIES.get(op, [])
    top = max((fam.index(t) for t in tags if t in fam), default=-1)
    out |= set(fam[: top + 1])
    for t in list(out):
        out |= OP_IMPLIES.get(op, {}).get(t, set())
    return out


OP_ALIASES = {"加": "加法", "减": "减法", "乘": "乘法", "除": "除法", "+": "加法", "-": "减法", "×": "乘法", "÷": "除法"}


def norm_op(op: str) -> Optional[str]:
    op = op.strip()
    if op in OPS:
        return op
    return OP_ALIASES.get(op)


# ------------------------------------------------------------------ 数的解析与形态推导


@dataclass(frozen=True)
class Num:
    kind: str  # int / dec / frac / mixed
    value: Fraction
    places: int = 0  # 小数位数
    den: int = 1  # 分数的分母（未约分的书写形式）
    num: int = 0  # 分数的分子（带分数为真分数部分的分子）
    whole: int = 0  # 带分数的整数部分


_MIXED = re.compile(r"^(\d+)\s*(?:又|\s|\+)?\s*(\d+)\s*/\s*(\d+)$")
_FRAC = re.compile(r"^(\d+)\s*/\s*(\d+)$")
_DEC = re.compile(r"^(\d+)\.(\d+)$")
_INT = re.compile(r"^\d+$")


def parse_num(s: str) -> Optional[Num]:
    s = unicodedata.normalize("NFKC", str(s)).strip().replace(",", "").lstrip("-")
    if _INT.match(s):
        return Num("int", Fraction(int(s)))
    m = _DEC.match(s)
    if m:
        return Num("dec", Fraction(Decimal(s)), places=len(m.group(2)))
    m = _FRAC.match(s)
    if m and int(m.group(2)) > 0:
        n, d = int(m.group(1)), int(m.group(2))
        return Num("frac", Fraction(n, d), den=d, num=n)
    m = _MIXED.match(s)
    if m and int(m.group(3)) > 0:
        w, n, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return Num("mixed", w + Fraction(n, d), den=d, num=n, whole=w)
    return None


def _digits(n: int) -> int:
    """有效位数：去掉末尾的 0（230×400 按 23×4 的位数算，教材把「乘数末尾有 0」当作单独的简便算法）。"""
    return len(str(abs(n)).rstrip("0") or "0")


def _has_carry(a: int, b: int) -> bool:
    """是否需要进位。教学口径：和不超过 10 的（凑十）、整十数相加不超过 100 的不算进位。"""
    if a + b <= 10 or (a % 10 == 0 and b % 10 == 0 and a + b <= 100):
        return False
    carry = 0
    while a or b:
        s = a % 10 + b % 10 + carry
        if s >= 10:
            return True
        a //= 10
        b //= 10
    return False


def _has_borrow(a: int, b: int) -> bool:
    """是否需要退位。教学口径：被减数不超过 10、整十数相减不算退位。"""
    if a < b:
        a, b = b, a
    if a <= 10 or (a % 10 == 0 and b % 10 == 0):
        return False
    while a or b:
        if a % 10 < b % 10:
            return True
        a //= 10
        b //= 10
    return False


def fraction_type_of(x: Num) -> Optional[str]:
    if x.kind == "mixed":
        return "带分数"
    if x.kind != "frac":
        return None
    if x.num >= x.den:
        return "假分数"
    return "几分之一" if x.num == 1 else "几分之几"


@dataclass
class OpAnalysis:
    forms: set[str] = field(default_factory=set)
    int_values: list[int] = field(default_factory=list)  # 出现的整数（含结果、小数的整数部分）
    places: int = 0
    fraction_types: set[str] = field(default_factory=set)


def analyze_operation(op: str, a: str, b: str, mode: Optional[str] = None) -> Optional[OpAnalysis]:
    """由具体操作数确定性推出该次运算的形态标签与数值事实。无法解析返回 None。"""
    op = norm_op(op) or op
    x, y = parse_num(a), parse_num(b)
    if x is None or y is None or op not in OPS:
        return None
    r = OpAnalysis()
    for z in (x, y):
        if z.kind == "int":
            r.int_values.append(int(z.value))
        elif z.kind == "dec":
            r.int_values.append(int(z.value))
            r.places = max(r.places, z.places)
        else:
            t = fraction_type_of(z)
            if t:
                r.fraction_types.add(t)
            if z.kind == "mixed":
                r.int_values.append(z.whole)
    kinds = {x.kind, y.kind}
    is_frac = bool(kinds & {"frac", "mixed"})
    is_dec = (not is_frac) and "dec" in kinds
    # 结果（用于整数数域：20 以内加法的和也不能超过 20）
    val: Optional[Fraction] = None
    if op == "加法":
        val = x.value + y.value
    elif op == "减法":
        val = abs(x.value - y.value)
    elif op == "乘法":
        val = x.value * y.value
    elif op == "除法" and y.value != 0:
        val = x.value / y.value
    if val is not None and val.denominator == 1:
        r.int_values.append(int(val))
    elif val is not None and op == "除法" and not (is_frac or is_dec):
        r.int_values.append(int(val))  # 整数除法的商（取整）

    if op in ("加法", "减法"):
        if is_frac:
            if "mixed" in kinds:
                r.forms.add("分数·带分数")
            dens = {z.den for z in (x, y) if z.kind in ("frac", "mixed")}
            r.forms.add("分数·异分母" if len(dens) > 1 else "分数·同分母")
        elif is_dec:
            r.forms.add("小数")
            if x.places != y.places:
                r.forms.add("小数·位数不同")
        else:
            r.forms.add("整数")
            ia, ib = int(x.value), int(y.value)
            if op == "加法" and _has_carry(ia, ib):
                r.forms.add("进位")
            if op == "减法" and _has_borrow(ia, ib):
                r.forms.add("退位")
    elif op == "乘法":
        if is_frac:
            r.forms.add("分数·乘分数" if {x.kind, y.kind} <= {"frac", "mixed"} else "分数·乘整数")
        elif is_dec:
            r.forms.add("小数·乘小数" if x.kind == "dec" and y.kind == "dec" else "小数·乘整数")
        else:
            ia, ib = int(x.value), int(y.value)
            dmin = min(_digits(ia), _digits(ib))
            if ia <= 9 and ib <= 9:
                r.forms.add("整数·表内")
            elif dmin <= 1:
                r.forms.add("整数·乘数一位数")
            elif dmin == 2:
                r.forms.add("整数·乘数两位数")
            else:
                r.forms.add("整数·乘数三位数及以上")
    else:  # 除法
        if is_frac:
            r.forms.add("分数·除数是分数" if y.kind in ("frac", "mixed") else "分数·除以整数")
        elif is_dec:
            r.forms.add("小数·除数是小数" if y.kind == "dec" else "小数·除以整数")
        else:
            ia, ib = int(x.value), int(y.value)
            if ib == 0:
                return None
            if ia <= 81 and ib <= 9:
                r.forms.add("整数·表内")
            else:
                d = _digits(ib)
                r.forms.add("整数·除数一位数" if d <= 1 else "整数·除数两位数" if d == 2 else "整数·除数三位数及以上")
            if ia % ib != 0:
                if mode == "decimal_quotient":
                    r.forms.add("整数·商为小数")
                elif mode == "remainder" or mode is None:
                    r.forms.add("整数·有余数")
    return r


def derive_forms(op: str, a: str, b: str, mode: Optional[str] = None) -> set[str]:
    r = analyze_operation(op, a, b, mode)
    return r.forms if r else set()


# ------------------------------------------------------------------ 计量单位

# 规范名 -> 别名（含符号写法）。日常时间单位（年月日周天）与计数单位（个、只、本……）不进词表，不参与越界判定。
UNIT_ALIASES: dict[str, list[str]] = {
    "毫米": ["mm", "公厘"],
    "厘米": ["cm", "公分"],
    "分米": ["dm"],
    "米": ["m"],
    "千米": ["km", "公里"],
    "克": ["g"],
    "千克": ["kg", "公斤"],
    "吨": ["t"],
    "毫升": ["ml"],
    "升": ["l", "公升"],
    "秒": ["s", "秒钟"],
    "分钟": ["min", "分(时间)", "分（时间）"],
    "时": ["小时", "h"],
    "元": ["圆", "¥", "￥"],
    "角": ["角(货币)", "角（货币）"],
    "分(货币)": ["分币", "分（货币）"],
    "平方毫米": ["mm²", "mm2"],
    "平方厘米": ["cm²", "cm2", "㎠"],
    "平方分米": ["dm²", "dm2"],
    "平方米": ["m²", "m2", "㎡", "平米"],
    "公顷": ["ha", "hm²"],
    "平方千米": ["km²", "km2", "平方公里", "k㎡"],
    "立方厘米": ["cm³", "cm3", "㎤", "立方公分"],
    "立方分米": ["dm³", "dm3"],
    "立方米": ["m³", "m3", "㎥"],
    "度": ["°", "度(角度)", "度（角度）", "度(角度单位)", "度（角度单位，°）"],
    "摄氏度": ["℃", "°c"],
    "速度单位": ["千米/时", "米/秒", "米/分", "km/h", "m/s", "千米每小时", "千米/小时", "公里/小时", "米每秒", "米每分", "速度复合单位",
             "速度复合单位（如米/秒、千米/时）"],
}
UNITS = list(UNIT_ALIASES)


def _unit_key(s: str) -> str:
    return unicodedata.normalize("NFKC", s).strip().lower().replace(" ", "")


_UNIT_LOOKUP: dict[str, str] = {}
for _c, _al in UNIT_ALIASES.items():
    _UNIT_LOOKUP[_unit_key(_c)] = _c
    for _a in _al:
        _UNIT_LOOKUP[_unit_key(_a)] = _c


def norm_unit(s: str) -> Optional[str]:
    k = _unit_key(s)
    if k in _UNIT_LOOKUP:
        return _UNIT_LOOKUP[k]
    k2 = re.sub(r"[（(].*?[）)]", "", k)  # 升(L) -> 升
    if k2 in _UNIT_LOOKUP:
        return _UNIT_LOOKUP[k2]
    m = re.search(r"[（(]([^）)]+)[）)]", k)
    if m and m.group(1) in _UNIT_LOOKUP:
        return _UNIT_LOOKUP[m.group(1)]
    return None


# ------------------------------------------------------------------ 几何词汇（种子，开放扩充）与概念规范化

GEOMETRY_SEED = """
上 下 左 右 前 后 东 南 西 北 东南 东北 西南 西北 北偏东 北偏西 南偏东 南偏西 观测点 方向 数对 位置
点 线 线段 射线 直线 端点 角 顶点 边 直角 锐角 钝角 平角 周角 平行 垂直 相交 平行线 垂线 垂足
三角形 直角三角形 锐角三角形 钝角三角形 等腰三角形 等边三角形 底 高 腰 底角 顶角 内角和 三边关系
四边形 长方形 正方形 平行四边形 梯形 菱形 筝形 对角线 对边 邻边 上底 下底 多边形 五边形 六边形
圆 圆心 半径 直径 弧 扇形 圆周率 同心圆
长方体 正方体 圆柱 圆锥 球 棱 面 棱长 底面 侧面 展开图 圆台 棱柱 三棱柱 表面积 体积 容积
对称轴 轴对称图形 对称 平移 旋转 旋转中心 旋转角度 顺时针 逆时针 放大 缩小 比例尺 图上距离 实际距离
三视图 正面 左面 右面 上面 长 宽 高 周长 面积 棱长总和 对称点 镶嵌
""".split()

# 文本挖掘（教材实例/抽取）时过滤掉的泛用词：它们在日常汉语里太常见，不能作为「用到了几何词汇」的证据
GEOMETRY_GENERIC = {"形状", "大小", "特征", "性质", "关系", "图案"}  # 太泛，不当作几何词汇（grants 里丢弃，文本挖掘里忽略）
GEOMETRY_MINE_EXCLUDE = GEOMETRY_GENERIC | {"数对", "东北", "东南", "西北", "西南", "上", "下", "左", "右", "前", "后", "东", "南", "西", "北", "上面", "下面", "左面", "右面", "正面", "方向", "位置",
                         "长", "宽", "高", "边", "面", "底", "角", "点", "线", "对称", "体积", "容积", "面积", "周长", "放大", "缩小", "圆", "球"}


_PUNCT = re.compile(r"[\s、，,。.;；:：·・\-_/\\()（）\[\]【】《》<>〈〉“”\"'‘’「」『』!！?？]+")


def norm_term(s: str) -> str:
    """概念/几何词汇的规范化：NFKC、小写、去空白与标点。匹配一律在规范形上精确比较。"""
    return _PUNCT.sub("", unicodedata.normalize("NFKC", str(s))).lower()


def norm_set(items) -> set[str]:
    return {t for t in (norm_term(x) for x in items) if t}
