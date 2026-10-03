"""题型实例化：在题型卡片的槽位约束内按种子采样参数，渲染题面，对 `program` 类在沙箱中求解。

对外入口是 `Curriculum.instantiate / instantiate_many / instantiate_with`（`chalkbase.query.store`），本模块是其实现：
不依赖 Curriculum 的数据加载，只需要一张 `ItemArchetype` 和一个可选的边界校验函数。

- 采样与求解都在沙箱子进程中完成（`chalkbase.runtime.sandbox`），调用方进程不执行卡片里的任何代码；
- 同一 (卡片, 种子) 的结果完全确定：每个种子对应一条独立的随机流，取其第一组满足 `constraints` 的参数；
- 数值一律 int / Decimal / Fraction，没有浮点。
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction
from typing import Any, Callable, Optional, Sequence

from chalkbase.models import BoundaryReport, ItemArchetype, ItemFeatures, VerifiableType
from chalkbase.runtime.features import params_features, slot_types
from chalkbase.runtime.sandbox import run_solver

NO_SOLVER = "def solve(**kw):\n    return None"  # rule / human 类没有求解器：沙箱只负责采样与约束检查
DEFAULT_MAX_TRIES = 50  # 边界过滤时每个种子最多检查的候选参数组数
FALLBACK_CANDIDATES = 12  # 不做边界过滤时，首组候选求解失败（如除零）或无解（返回 None）后顺延的候选数
BLANK = "______"
_PLACEHOLDER = re.compile(r"\{\{|\}\}|\{([^{}]+)\}")
_EXPR_CHARS = re.compile(r"[\w\s+\-*/%()]+")
# 模板里不属于槽位、名字表明是待作答空位的占位符，渲染为横线
_BLANK_LIKE = re.compile(r"^(blank|ans|answer|result|quotient|remainder)(_?\w*)$")


class InstantiationError(RuntimeError):
    """题型无法实例化（约束不可满足、求解程序出错、超时等）。"""


class NoInBoundsSample(InstantiationError):
    """在重试上限内没有采到满足边界要求的参数。"""


@dataclass(frozen=True)
class Problem:
    """一道由题型卡片实例化出的题目。

    answer / answer_value：`program` 类为程序求得的答案（显示串 / 精确值），`rule`、`human` 类为 None。
    answer_value 的类型为 int、Decimal、Fraction、bool、str，或它们组成的 list / dict。
    features：由数值参数推出的结构化特征（不含答案，不含概念/单位/几何词汇）。
    boundary：给定 lesson_id 时 `check_item` 的完整报告，其 `verdict` 为 in / borderline / out。
    warnings：渲染时发现的卡片缺陷，如题面里仍有未绑定到槽位的占位符。
    """

    archetype_id: str
    problem: str
    answer: Optional[str]
    answer_value: Any
    solution: list[str]
    params: dict[str, Any]
    features: ItemFeatures
    verifiable_type: str
    seed: Optional[int]
    knowledge_point_id: str = ""
    item_form: str = ""
    difficulty: int = 0
    lesson_id: Optional[str] = None
    boundary: Optional[BoundaryReport] = None
    warnings: list[str] = field(default_factory=list)

    @property
    def verdict(self) -> Optional[str]:
        """边界判定 in / borderline / out；未给 lesson_id 时为 None。"""
        return self.boundary.verdict if self.boundary else None

    @property
    def violated_dimensions(self) -> list[str]:
        return [v.dimension for v in self.boundary.violations] if self.boundary else []

    def to_dict(self) -> dict:
        """JSON 友好的字典：Decimal / Fraction 写成字符串（Fraction 为 "a/b"）。"""
        return {
            "archetype_id": self.archetype_id, "problem": self.problem, "answer": self.answer,
            "answer_value": _jsonable(self.answer_value), "solution": list(self.solution),
            "params": {k: _jsonable(v) for k, v in self.params.items()}, "features": self.features.model_dump(mode="json"),
            "verifiable_type": self.verifiable_type, "seed": self.seed, "knowledge_point_id": self.knowledge_point_id,
            "item_form": self.item_form, "difficulty": self.difficulty, "lesson_id": self.lesson_id,
            "boundary": self.boundary.model_dump(mode="json") if self.boundary else None, "warnings": list(self.warnings),
        }


# ---------------------------------------------------------------- 值的编码与显示


def _jsonable(v):
    if isinstance(v, Fraction):
        return f"{v.numerator}/{v.denominator}"
    if isinstance(v, Decimal):
        return format(v, "f")
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    return v


def _decode(t):
    """沙箱的带类型标签结果 → Python 值。"""
    kind, v = t
    if kind == "int":
        return int(v)
    if kind == "dec":
        return Decimal(v)
    if kind == "frac":
        return Fraction(v)
    if kind == "list":
        return [_decode(x) for x in v]
    if kind == "dict":
        return {k: _decode(x) for k, x in v.items()}
    return v  # bool / str / none


def display(v) -> Optional[str]:
    """答案的显示串：分数写 a/b，小数不带指数，布尔写 正确/错误，列表用「，」连接。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return "正确" if v else "错误"
    if isinstance(v, Fraction):
        return str(v.numerator) if v.denominator == 1 else f"{v.numerator}/{v.denominator}"
    if isinstance(v, Decimal):
        return format(v, "f")
    if isinstance(v, (list, tuple)):
        return "，".join(display(x) or "" for x in v)
    if isinstance(v, dict):
        return "；".join(f"{k}={display(x)}" for k, x in v.items())
    return str(v)


def _typed_params(raw: dict, slots: dict) -> dict[str, Any]:
    out = {}
    for k, v in raw.items():
        t = slots.get(k, {}).get("type")
        if t == "int":
            out[k] = int(v)
        elif t == "decimal":
            out[k] = Decimal(str(v))
        elif t == "fraction":
            out[k] = Fraction(str(v))
        else:
            out[k] = v
    return out


def _raw_param(v) -> str:
    if isinstance(v, Fraction):
        return f"{v.numerator}/{v.denominator}"
    if isinstance(v, Decimal):
        return format(v, "f")
    return str(v)


def render(template: str, params: dict[str, Any]) -> tuple[str, list[str]]:
    """把 `{槽位}` 代入模板。返回 (题面, warnings)。

    模板里没有对应槽位的占位符：名字形如 blank / ans / answer / result / quotient / remainder 的渲染为横线（待作答空位），
    其余原样保留并记入 warnings。模板含 `{{` 时按 str.format 的转义规则处理 `{{` `}}`。"""
    unescape = "{{" in template
    unresolved: list[str] = []

    def sub(m: re.Match) -> str:
        if m.group(1) is None:
            return m.group(0)[0] if unescape else m.group(0)
        name = m.group(1).strip()
        if _literal(name, m):
            return m.group(0)
        if name in params:
            return display(params[name]) if not isinstance(params[name], str) else params[name]
        if _BLANK_LIKE.match(name):
            return BLANK
        if _is_expression(name, params):
            try:
                v = eval_expression(name, params)
                return display(v) if not isinstance(v, str) else v
            except (ValueError, ArithmeticError, TypeError, SyntaxError):
                pass
        if name not in unresolved:
            unresolved.append(name)
        return m.group(0)

    text = template
    for _ in range(3):  # choice 槽位的选项文本里可能还含其他槽位的占位符：再代入至多两轮，直到题面不再变化
        unresolved.clear()
        new = _PLACEHOLDER.sub(sub, text)
        unescape = False
        if new == text:
            break
        text = new
    warns = [f"模板占位符 {{{n}}} 没有对应槽位，题面里保留原样" for n in unresolved]
    return text, warns


def _literal(name: str, m: re.Match) -> bool:
    """花括号里是字面内容而非占位符：纯数字（LaTeX 公式里的分子分母），或紧跟在 LaTeX 命令后（如 text 命令的参数）。"""
    return name.isdigit() or re.search(r"[\\][a-zA-Z]+$", m.string[: m.start()]) is not None


def _is_expression(src: str, names) -> bool:
    """占位符内容是含运算符、且至少引用一个槽位的算术表达式（排除 LaTeX 里的 `{2}`、`{x}` 之类）。"""
    if not _EXPR_CHARS.fullmatch(src) or not re.search(r"[+\-*/%]", src):
        return False
    return any(i in names for i in re.findall(r"[^\W\d]\w*", src))


def unresolved_placeholders(template: str, slot_names) -> list[str]:
    """模板里既不是槽位、也不是空位（blank 等）、也不是由槽位组成的算术表达式的占位符。"""
    names = {n: 1 for n in slot_names}
    out: list[str] = []
    for m in _PLACEHOLDER.finditer(template):
        n = m.group(1)
        if n is None:
            continue
        n = n.strip()
        if n in names or _BLANK_LIKE.match(n) or _literal(n, m):
            continue
        if _is_expression(n, names) and all(i in names for i in re.findall(r"[^\W\d]\w*", n)):
            continue
        if n not in out:
            out.append(n)
    return out


def eval_expression(src: str, params: dict[str, Any]):
    """安全求值算术表达式：只允许 + - * / // % 与括号、整数/小数常量、槽位名；不允许调用与属性访问。
    运算全程 int / Decimal / Fraction，没有浮点；`/` 对整数得 Fraction。"""
    tree = ast.parse(src, mode="eval")

    def num(v):
        if isinstance(v, bool) or not isinstance(v, (int, Decimal, Fraction)):
            raise ValueError(f"非数值: {v!r}")
        return v

    def to_fr(x):
        return Fraction(x) if isinstance(x, Decimal) else x

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant):
            if isinstance(n.value, bool) or not isinstance(n.value, (int, float)):
                raise ValueError("不支持的常量")
            return n.value if isinstance(n.value, int) else Decimal(repr(n.value))
        if isinstance(n, ast.Name):
            if n.id not in params:
                raise ValueError(f"未知名字 {n.id}")
            v = params[n.id]
            if isinstance(v, str) and re.fullmatch(r"-?\d+", v):  # choice 槽位的整数选项（如步长 "10"）按整数参与运算
                v = int(v)
            return num(v)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            v = ev(n.operand)
            return -v if isinstance(n.op, ast.USub) else v
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Pow):
            base, ex = ev(n.left), ev(n.right)
            if not isinstance(ex, int) or not 0 <= ex <= 8:  # 指数限定为小的非负整数，防止构造巨大数
                raise ValueError("指数须为 0～8 的整数")
            return base ** ex
        if isinstance(n, ast.BinOp):
            a, b = ev(n.left), ev(n.right)
            if isinstance(a, Decimal) != isinstance(b, Decimal) and (isinstance(a, Fraction) or isinstance(b, Fraction)):
                a, b = to_fr(a), to_fr(b)
            elif isinstance(a, Decimal) and isinstance(b, (int, Fraction)) and not isinstance(b, Decimal):
                a, b = (a, Decimal(b)) if isinstance(b, int) else (to_fr(a), b)
            elif isinstance(b, Decimal) and isinstance(a, int):
                a = Decimal(a)
            op = type(n.op)
            if op is ast.Add:
                return a + b
            if op is ast.Sub:
                return a - b
            if op is ast.Mult:
                return a * b
            if op is ast.FloorDiv:
                return a // b
            if op is ast.Mod:
                return a % b
            if op is ast.Div:
                return Fraction(a, b) if isinstance(a, int) and isinstance(b, int) else a / b
        raise ValueError("不支持的表达式")

    v = ev(tree)
    if isinstance(v, Fraction) and v.denominator == 1:
        return v.numerator
    return v


# ---------------------------------------------------------------- 沙箱调用


def _solver_of(arch: ItemArchetype) -> str:
    if arch.verifiable_type == VerifiableType.PROGRAM:
        if not arch.solver_program:
            raise InstantiationError(f"{arch.id} 是 program 类但没有 solver_program")
        return arch.solver_program
    return NO_SOLVER


def _slots_of(arch: ItemArchetype) -> tuple[dict, list[str]]:
    pc = arch.parameter_constraints or {}
    return pc.get("slots") or {}, pc.get("constraints") or []


def sample_candidates(arch: ItemArchetype, seeds: Sequence[int], per_seed: int = 1, timeout: Optional[float] = None) -> list[list[dict]]:
    """每个种子一条随机流，返回其前 `per_seed` 组满足约束的参数及求解结果（沙箱原始条目，`typed` 为带类型标签的答案）。"""
    slots, cons = _slots_of(arch)
    r = run_solver(_solver_of(arch), slot_types(slots), None, cons, timeout=timeout or 20.0 + 0.02 * len(seeds) * per_seed,
                   sample={"slots": slots, "seeds": list(seeds), "per_seed": per_seed})
    if not r.get("ok"):
        raise InstantiationError(f"{arch.id}: {r.get('error')}")
    return [s["candidates"] for s in r["samples"]]


def _build(arch: ItemArchetype, cand: dict, seed: Optional[int], lesson_id: Optional[str],
           check: Optional[Callable[[ItemFeatures], BoundaryReport]]) -> Problem:
    slots, _ = _slots_of(arch)
    raw = cand["params"]
    params = _typed_params(raw, slots)
    text, warns = render(arch.template, params)
    value = _decode(cand["typed"]) if arch.verifiable_type == VerifiableType.PROGRAM else None
    feats = params_features(raw, slots)
    report = check(feats) if (check is not None and lesson_id is not None) else None
    return Problem(
        archetype_id=arch.id, problem=text, answer=display(value), answer_value=value, solution=list(arch.solution_steps),
        params=params, features=feats, verifiable_type=arch.verifiable_type.value, seed=seed,
        knowledge_point_id=arch.primary_knowledge_point_id, item_form=arch.item_form.value, difficulty=arch.difficulty,
        lesson_id=lesson_id, boundary=report, warnings=warns)


def _pick(arch: ItemArchetype, seed: int, cands: list[dict], lesson_id: Optional[str], check, only_in_bounds: bool,
          accept_borderline: bool) -> Problem:
    ok = [c for c in cands if c.get("ok") and c.get("constraints_ok", True)]
    if arch.verifiable_type == VerifiableType.PROGRAM:  # program 类求解返回 None 表示这组参数无解，不是有效的题
        ok = [c for c in ok if c["typed"][0] != "none"] or ok[:0]
    if not ok:
        raise InstantiationError(f"{arch.id} seed={seed}: " + (cands[0].get("error", "无可用候选") if cands else "无候选"))
    if not only_in_bounds:
        return _build(arch, ok[0], seed, lesson_id, check)
    accepted = {"in", "borderline"} if accept_borderline else {"in"}
    for c in ok:
        p = _build(arch, c, seed, lesson_id, check)
        if p.verdict in accepted:
            return p
    raise NoInBoundsSample(f"{arch.id} seed={seed}: {len(ok)} 组候选参数在课时 {lesson_id} 的边界下均不满足 {sorted(accepted)}")


def instantiate(arch: ItemArchetype, seed: int = 0, *, lesson_id: Optional[str] = None,
                check: Optional[Callable[[ItemFeatures], BoundaryReport]] = None, only_in_bounds: bool = False,
                accept_borderline: bool = False, max_tries: int = DEFAULT_MAX_TRIES) -> Problem:
    if only_in_bounds and (check is None or lesson_id is None):
        raise ValueError("only_in_bounds=True 需要同时给出 lesson_id")
    per = max_tries if only_in_bounds else FALLBACK_CANDIDATES
    cands = sample_candidates(arch, [seed], per)[0]
    return _pick(arch, seed, cands, lesson_id, check, only_in_bounds, accept_borderline)


def instantiate_many(arch: ItemArchetype, n: int, seed0: int = 0, *, lesson_id: Optional[str] = None,
                     check: Optional[Callable[[ItemFeatures], BoundaryReport]] = None, only_in_bounds: bool = False,
                     accept_borderline: bool = False, max_tries: int = DEFAULT_MAX_TRIES, unique: bool = True) -> list[Problem]:
    """从 seed0 起依次取种子，凑够 n 道题。`unique=True` 时参数组合重复的种子被跳过（`Problem.seed` 记录实际使用的种子）。
    参数空间太小或边界太严导致凑不够 n 道时返回已得到的题（至少一道，否则抛异常）。"""
    if only_in_bounds and (check is None or lesson_id is None):
        raise ValueError("only_in_bounds=True 需要同时给出 lesson_id")
    per = max_tries if only_in_bounds else FALLBACK_CANDIDATES
    out: list[Problem] = []
    seen: set = set()
    first_err: Optional[Exception] = None
    seed, limit = seed0, seed0 + max(60, n * 20)
    while len(out) < n and seed < limit:
        chunk = list(range(seed, min(seed + max(n - len(out), 1), limit)))
        seed = chunk[-1] + 1
        for sd, cands in zip(chunk, sample_candidates(arch, chunk, per)):
            if len(out) >= n:
                break
            try:
                p = _pick(arch, sd, cands, lesson_id, check, only_in_bounds, accept_borderline)
            except InstantiationError as e:
                first_err = first_err or e
                continue
            key = tuple(sorted((k, _raw_param(v)) for k, v in p.params.items()))
            if unique and key in seen:
                continue
            seen.add(key)
            out.append(p)
    if not out and first_err is not None:
        raise first_err
    return out


def instantiate_with(arch: ItemArchetype, params: dict[str, Any], *, lesson_id: Optional[str] = None,
                     check: Optional[Callable[[ItemFeatures], BoundaryReport]] = None) -> Problem:
    """用调用方给定的参数渲染并求解（`seed=None`）。参数键必须与槽位一致；不满足卡片 constraints 时仍求解，并在 warnings 里提示。"""
    slots, cons = _slots_of(arch)
    if set(params) != set(slots):
        raise ValueError(f"{arch.id} 的槽位是 {sorted(slots)}，收到 {sorted(params)}")
    raw = {k: _raw_param(v) for k, v in params.items()}
    r = run_solver(_solver_of(arch), slot_types(slots), [raw], cons)
    if not r.get("ok"):
        raise InstantiationError(f"{arch.id}: {r.get('error')}")
    cand = r["results"][0]
    if not cand.get("ok"):
        raise InstantiationError(f"{arch.id}: {cand.get('error')}")
    p = _build(arch, cand, None, lesson_id, check)
    if not cand.get("constraints_ok", True):
        p.warnings.append("参数不满足题型卡片的 constraints")
    return p
