"""题型实例化运行时（chalkbase.runtime）的接口契约：可复现性、答案正确性、边界过滤、rule/human 行为、沙箱隔离。

只依赖接口行为，不依赖具体题型的 ID 或数量；全量题型的 smoke 见 scripts/smoke_instantiate.py。"""
from __future__ import annotations

import random
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from fractions import Fraction

import pytest

from chalkbase import Curriculum, Problem
from chalkbase.runtime import InstantiationError, NoInBoundsSample, instantiate, run_solver, slot_types
from chalkbase.stage3.generate import answers_equal


@pytest.fixture(scope="module")
def cur():
    return Curriculum()


@pytest.fixture(scope="module")
def prog(cur):
    """一张只含整数槽位、模板无缺陷、有边界课时的 program 类题型（参数空间大，种子不同则题不同）。"""
    for a in sorted(cur.archetypes_by_id.values(), key=lambda a: a.id):
        slots = (a.parameter_constraints or {}).get("slots") or {}
        if a.verifiable_type.value == "program" and len(slots) >= 2 and all(s["type"] == "int" and s["max"] - s["min"] >= 20 for s in slots.values()):
            if not cur.instantiate(a.id, 0).warnings and cur.archetype_lessons(a.id):
                return a
    pytest.skip("没有合适的 program 题型")


def _no_float(v):
    assert not isinstance(v, float)
    if isinstance(v, (list, tuple)):
        for x in v:
            _no_float(x)
    if isinstance(v, dict):
        for x in v.values():
            _no_float(x)


# ---------------------------------------------------------------- 契约


def test_instantiate_returns_complete_problem(cur, prog):
    p = cur.instantiate(prog.id, seed=3)
    assert isinstance(p, Problem)
    assert p.archetype_id == prog.id and p.seed == 3 and p.verifiable_type == "program"
    assert p.problem and "{" not in p.problem
    assert p.answer is not None and p.answer_value is not None
    assert set(p.params) == set(prog.parameter_constraints["slots"])
    assert p.solution == prog.solution_steps
    assert p.features.integer_max is not None
    assert p.lesson_id is None and p.boundary is None and p.verdict is None
    d = p.to_dict()
    assert d["archetype_id"] == prog.id and d["boundary"] is None
    _no_float(p.answer_value)
    _no_float(list(p.params.values()))


def test_same_seed_reproducible(cur, prog):
    assert cur.instantiate(prog.id, 7).to_dict() == cur.instantiate(prog.id, 7).to_dict()
    a = cur.instantiate_many(prog.id, 4, 10)
    b = cur.instantiate_many(prog.id, 4, 10)
    assert [p.to_dict() for p in a] == [p.to_dict() for p in b]


def test_different_seeds_give_different_problems(cur, prog):
    ps = cur.instantiate_many(prog.id, 6, 0)
    assert len(ps) == 6
    assert len({p.problem for p in ps}) == 6
    assert len({p.seed for p in ps}) == 6
    # instantiate_many 的每道题都能用其 seed 单独复现
    for p in ps[:3]:
        assert cur.instantiate(prog.id, p.seed).to_dict() == p.to_dict()


def test_params_within_slot_ranges(cur, prog):
    slots = prog.parameter_constraints["slots"]
    for p in cur.instantiate_many(prog.id, 8, 0):
        for k, s in slots.items():
            assert s["min"] <= p.params[k] <= s["max"]


def test_answer_matches_card_examples(cur, prog):
    """用卡片示例的参数重新求解，答案与示例的 answer_value 一致（同一求解程序，经运行时接口）。"""
    for ex in prog.rewritten_examples:
        p = cur.instantiate_with(prog.id, ex.params)
        assert p.seed is None
        assert answers_equal(p.answer_value, ex.answer_value if ex.answer_value is not None else ex.answer)


def test_answer_values_are_exact_types(cur):
    """抽样各类 program 题型：答案只会是 int / Decimal / Fraction / bool / str 或其容器，没有浮点。"""
    rng = random.Random(0)
    arch = sorted((a for a in cur.archetypes_by_id.values() if a.verifiable_type.value == "program"), key=lambda a: a.id)
    sample = rng.sample(arch, 12)
    with ThreadPoolExecutor(6) as ex:
        probs = list(ex.map(lambda a: cur.instantiate(a.id, 1), sample))
    for p in probs:
        _no_float(p.answer_value)
        assert isinstance(p.answer, str)


def test_fraction_and_decimal_params_are_exact(cur):
    for kind, typ in (("fraction", Fraction), ("decimal", Decimal)):
        a = next((a for a in sorted(cur.archetypes_by_id.values(), key=lambda a: a.id) if a.verifiable_type.value == "program"
                  and any(s["type"] == kind for s in a.parameter_constraints["slots"].values())), None)
        if a is None:
            continue
        p = cur.instantiate(a.id, 0)
        slot = next(k for k, s in a.parameter_constraints["slots"].items() if s["type"] == kind)
        assert isinstance(p.params[slot], typ)


# ---------------------------------------------------------------- 能力边界


def test_lesson_adds_boundary_report(cur, prog):
    lesson = cur.archetype_lessons(prog.id)[-1]
    p = cur.instantiate(prog.id, 0, lesson_id=lesson)
    assert p.lesson_id == lesson and p.boundary is not None
    assert p.verdict in ("in", "borderline", "out")
    assert p.verdict == "in" or p.violated_dimensions
    assert p.boundary.model_dump() == cur.check_item(p.features, lesson).model_dump()


def test_only_in_bounds_filters(cur, prog):
    lesson = cur.archetype_lessons(prog.id)[-1]
    for p in cur.instantiate_many(prog.id, 5, 0, lesson, only_in_bounds=True):
        assert p.verdict == "in"
    first = cur.lesson_ids()[0]  # 全书第一课时：数域极小，二位数参数必越界
    with pytest.raises(NoInBoundsSample):
        cur.instantiate(prog.id, 0, first, only_in_bounds=True, max_tries=10)
    p = cur.instantiate(prog.id, 0, first)  # 不过滤时仍返回，并如实标出越界
    assert p.verdict in ("borderline", "out") and "integer_domain" in p.violated_dimensions


def test_only_in_bounds_needs_lesson(cur, prog):
    with pytest.raises(ValueError):
        cur.instantiate(prog.id, 0, only_in_bounds=True)


def test_unknown_ids(cur, prog):
    with pytest.raises(KeyError):
        cur.instantiate("at.no.such.card")
    with pytest.raises(KeyError):
        cur.instantiate(prog.id, 0, "g9z.u1.l01")
    with pytest.raises(ValueError):
        cur.instantiate_with(prog.id, {"nope": 1})


def test_archetype_lessons_ordered(cur, prog):
    ls = cur.archetype_lessons(prog.id)
    assert ls and ls == sorted(ls, key=cur.lesson_position)
    assert all(l in cur.lessons for l in ls)


# ---------------------------------------------------------------- rule / human


@pytest.mark.parametrize("vt", ["rule", "human"])
def test_rule_and_human_have_no_answer(cur, vt):
    a = next(a for a in sorted(cur.archetypes_by_id.values(), key=lambda a: a.id) if a.verifiable_type.value == vt)
    p = cur.instantiate(a.id, 2)
    assert p.verifiable_type == vt and p.problem
    assert p.answer is None and p.answer_value is None
    assert cur.instantiate(a.id, 2).to_dict() == p.to_dict()


# ---------------------------------------------------------------- 沙箱隔离

EVIL = {
    "import": "import os\ndef solve(a):\n    return os.getcwd()",
    "dunder_import": "def solve(a):\n    return __import__('os').getcwd()",
    "open": "def solve(a):\n    return open('canary.txt', 'w')",
    "class_escape": "def solve(a):\n    return ().__class__.__bases__[0].__subclasses__()",
    "eval": "def solve(a):\n    return eval('1+1')",
    "float": "def solve(a):\n    return 0.5",
    "builtin_missing": "def solve(a):\n    return print(1)",
}


@pytest.mark.parametrize("name", sorted(EVIL))
def test_sandbox_rejects_unsafe_solvers(name, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = run_solver(EVIL[name], {"a": "int"}, [{"a": 1}])
    assert r["ok"] is False or not r["results"][0]["ok"]
    assert not (tmp_path / "canary.txt").exists()


def test_sandbox_timeout():
    r = run_solver("def solve(a):\n    while True:\n        pass", {"a": "int"}, [{"a": 1}], timeout=2.0)
    assert r == {"ok": False, "error": "timeout"}


def test_constraints_may_use_comprehensions():
    """Python 3.11 里 eval 的 locals 看不到推导式内部的名字；沙箱把参数放进全局命名空间。"""
    slots = {"a": {"type": "int", "min": 1, "max": 9}, "b": {"type": "int", "min": 1, "max": 9}}
    r = run_solver("def solve(a, b):\n    return a + b", {"a": "int", "b": "int"}, None, ["any([x > 3 for x in [a, b]])"],
                   sample={"slots": slots, "seeds": [0], "per_seed": 5})
    cands = r["samples"][0]["candidates"]
    assert len(cands) == 5 and all(c["ok"] and c["constraints_ok"] for c in cands)


def test_unsatisfiable_constraints_raise(prog):
    bad = prog.model_copy(update={"parameter_constraints": {**prog.parameter_constraints, "constraints": ["False"]}})
    with pytest.raises(InstantiationError):
        instantiate(bad, 0)


def test_slot_types_helper():
    assert slot_types({"a": {"type": "int"}, "c": {"type": "choice", "options": ["x"]}}) == {"a": "int", "c": "str"}


# ---------------------------------------------------------------- 模板占位符


def test_every_program_template_placeholder_resolves(cur):
    """不变量：program 题型模板里的每个占位符都能被槽位、由槽位组成的算术表达式或空位（blank/ans 开头）解析。"""
    from chalkbase.runtime.instantiate import unresolved_placeholders

    bad = {a.id: unresolved_placeholders(a.template, a.parameter_constraints["slots"]) for a in cur.archetypes_by_id.values()
           if a.verifiable_type.value == "program"}
    bad = {k: v for k, v in bad.items() if v}
    assert not bad, f"{len(bad)} 张卡片的模板有无法解析的占位符，例如 {dict(list(bad.items())[:3])}；运行 `python -m chalkbase.stage3.repair placeholders`"


def test_render_expressions_and_blanks():
    from chalkbase.runtime.instantiate import eval_expression, render, unresolved_placeholders

    text, warns = render("{a}÷{b}={a // b}……{a % b}，{a / b}，{blank1}，\frac{3}{4}", {"a": 17, "b": 5})
    assert text == "17÷5=3……2，17/5，______，\frac{3}{4}" and not warns
    text, warns = render("{x1}", {"a": 1})
    assert text == "{x1}" and len(warns) == 1
    assert eval_expression("a * b + 0.5", {"a": Decimal("1.5"), "b": 4}) == Decimal("6.5")
    assert eval_expression("a / b", {"a": 1, "b": 3}) == Fraction(1, 3)
    for evil in ("__import__('os')", "a.real", "abs(a)", "[a]", "a ** 9999999", "a ** -1"):
        with pytest.raises(ValueError):
            eval_expression(evil, {"a": 2})
    assert unresolved_placeholders("{a} {c} {a+b} {a+z} {blank2}", ["a", "b"]) == ["c", "a+z"]


# rule / human 卡片中仍无法实例化的题型（模板把 choice 选项当作子模板、槽位约束采不到参数）。名单只许缩短，不许增长。
KNOWN_UNRENDERABLE_NON_PROGRAM = {
    "at.data_organize_tally.04", "at.decimal_fraction_correspondence.09", "at.g5b_reciprocal.08", "at.估算_总复习.03",
}


def test_non_program_cards_instantiate_except_known(cur):
    from concurrent.futures import ThreadPoolExecutor

    cards = sorted((a for a in cur.archetypes_by_id.values() if a.verifiable_type.value != "program"), key=lambda a: a.id)

    def bad(a) -> bool:
        try:
            p = cur.instantiate(a.id, 0)
        except InstantiationError:
            return True
        return bool(p.warnings)

    with ThreadPoolExecutor(8) as ex:
        failing = {a.id for a, b in zip(cards, ex.map(bad, cards)) if b}
    assert failing <= KNOWN_UNRENDERABLE_NON_PROGRAM, f"新增的不可实例化 rule/human 题型：{sorted(failing - KNOWN_UNRENDERABLE_NON_PROGRAM)}"


def test_program_cards_instantiate_without_warnings_sample(cur):
    """抽样版的全量 smoke（全量见 scripts/smoke_instantiate.py）：无 warnings、答案非空、题面不含 Python 占位符残留。"""
    from concurrent.futures import ThreadPoolExecutor

    arch = sorted((a for a in cur.archetypes_by_id.values() if a.verifiable_type.value == "program"), key=lambda a: a.id)[::20]

    def run(a):
        return cur.instantiate_many(a.id, 3, 0, unique=False)

    with ThreadPoolExecutor(8) as ex:
        for a, ps in zip(arch, ex.map(run, arch)):
            assert len(ps) == 3, a.id
            for p in ps:
                assert p.answer is not None and not p.warnings, (a.id, p.warnings)
