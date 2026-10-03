"""Stage 3 不变量（eval/specs/stage3.md §2）。data/archetypes.json 尚未生成时跳过。"""
from collections import Counter

import pytest

from chalkbase.common import DATA_DIR, load_work_books, read_json
from chalkbase.models import Context, ExerciseInstance, GlossaryEntry, ItemArchetype
from chalkbase.stage3.contexts import _norm
from chalkbase.stage3.generate import answers_equal, ngram_overlap, slot_types
from chalkbase.stage3.sandbox import run_solver

pytestmark = pytest.mark.skipif(not (DATA_DIR / "archetypes.json").exists(), reason="Stage 3 产物尚未生成")


@pytest.fixture(scope="module")
def arch():
    return read_json(DATA_DIR / "archetypes.json")


@pytest.fixture(scope="module")
def ex():
    return {e["id"]: e for e in read_json(DATA_DIR / "exercises.json")}


def test_exercises_schema_and_refs(ex):
    kps = {k["id"] for k in read_json(DATA_DIR / "knowledge_points.json")}
    lessons = {l["id"] for l in read_json(DATA_DIR / "lessons.json")}
    assert len(ex) == sum(len(b["exercises"]) for b in load_work_books().values())
    for e in ex.values():
        ExerciseInstance(**e)
        assert e["lesson_id"] in lessons
        assert e["primary_knowledge_point_id"] in kps
        assert set(e["secondary_knowledge_point_ids"]) <= kps


def test_reconciled_archetypes_have_no_source_instances_but_textbook_ones_do(arch):
    """缺口知识点的 reconciled 题型没有教材实例（D26/CHANGELOG）；教材题型必须有源实例。"""
    assert all(a["source_instance_ids"] for a in arch if a["provenance"] == "textbook")
    assert all(not a["source_instance_ids"] for a in arch if a["provenance"] == "reconciled")
    kps = {k["id"] for k in read_json(DATA_DIR / "knowledge_points.json")}
    assert all(a["primary_knowledge_point_id"] in kps for a in arch if a["provenance"] == "reconciled")


def test_every_instance_in_exactly_one_archetype(arch, ex):
    cnt = Counter(i for a in arch for i in a["source_instance_ids"])
    assert set(cnt) == set(ex), f"未归属题型的实例：{sorted(set(ex) - set(cnt))[:10]}"
    assert max(cnt.values()) == 1, f"实例属于多个题型：{[i for i, c in cnt.items() if c > 1][:10]}"


def test_archetype_instances_share_primary_kp_and_form(arch, ex):
    """v2 分组只在 (主知识点, 题目形式) 内细分，不跨知识点、不跨形式（eval/specs/stage3.md §3）。"""
    bad = [a["id"] for a in arch
           if a["provenance"] == "textbook" and {(ex[i]["primary_knowledge_point_id"], ex[i]["item_form"]) for i in a["source_instance_ids"]}
           != {(a["primary_knowledge_point_id"], a["item_form"])}]
    assert not bad, f"题型的实例与其主知识点/形式不一致：{bad[:5]}"


def test_archetype_schema_and_refs(arch):
    kps = {k["id"] for k in read_json(DATA_DIR / "knowledge_points.json")}
    ctx = {c["id"] for c in read_json(DATA_DIR / "contexts.json")}
    ids = [a["id"] for a in arch]
    assert len(ids) == len(set(ids)), "题型 ID 不唯一"
    for a in arch:
        ItemArchetype(**a)
        assert a["id"].startswith("at.")
        assert a["primary_knowledge_point_id"] in kps
        assert set(a["secondary_knowledge_point_ids"]) <= kps
        assert set(a["allowed_contexts"]) <= ctx, f"{a['id']} 引用了情境库中不存在的情境"
        if a["verifiable_type"] == "program":
            assert a["solver_program"], f"{a['id']} 是 program 类但没有求解程序"


def test_program_examples_recompute(arch):
    """program 类改写示例答案必须等于程序重算结果（没有容忍度）。"""
    bad = []
    for a in arch:
        if a["verifiable_type"] != "program":
            continue
        pc = a["parameter_constraints"]
        res = run_solver(a["solver_program"], slot_types(pc["slots"]), [e["params"] for e in a["rewritten_examples"]], pc["constraints"])
        if not res["ok"]:
            bad.append((a["id"], res["error"]))
            continue
        for e, r in zip(a["rewritten_examples"], res["results"]):
            if not (r["ok"] and r["constraints_ok"] and answers_equal(r["result"], e["answer_value"])):
                bad.append((a["id"], e["answer_value"], r))
    assert not bad, f"{len(bad)} 个示例重算不一致，例如 {bad[:3]}"


def test_slots_within_observed_envelope(arch):
    bad = []
    for a in arch:
        env = a["parameter_constraints"]["observed"]
        for k, s in a["parameter_constraints"]["slots"].items():
            if s["type"] == "int" and env.get("integer_digits") and len(str(abs(int(s["max"])))) > env["integer_digits"][1]:
                bad.append((a["id"], k))
            if s["type"] == "decimal" and env.get("decimal_places") and int(s.get("places", 1)) > env["decimal_places"][1]:
                bad.append((a["id"], k))
    assert not bad, f"槽位约束超出观测包络：{bad[:5]}"


def test_examples_not_copied_from_textbook(arch, ex):
    bad = []
    for a in arch:
        texts = [ex[i]["text"] for i in a["source_instance_ids"]]
        for e in a["rewritten_examples"]:
            ov = max((ngram_overlap(e["problem"], t) for t in texts), default=0)
            if ov >= 0.5:
                bad.append((a["id"], round(ov, 2)))
    assert not bad, f"改写示例疑似复述原题：{bad[:5]}"


def test_difficulty_levels_spread_within_grade(arch):
    by_grade = {}
    for a in arch:
        by_grade.setdefault(a["difficulty_features"]["grade"], []).append(a["difficulty"])
    for g, levels in by_grade.items():
        if len(levels) >= 10:
            assert len(set(levels)) >= 3, f"{g} 年级难度等级塌缩：{Counter(levels)}"


def test_contexts_cover_instance_themes(ex):
    ctxs = read_json(DATA_DIR / "contexts.json")
    for c in ctxs:
        Context(**c)
    covered = {i for c in ctxs for i in c["source_instance_ids"]}
    from chalkbase.stage3.contexts import NONE_THEMES

    missing = [i for i, e in ex.items() if _norm(e.get("context_theme")) not in NONE_THEMES and _norm(e.get("context_theme")) and i not in covered]
    assert not missing, f"有情境主题的实例未进入情境库：{missing[:10]}"


def test_glossary_covers_observed_terms():
    gl = read_json(DATA_DIR / "glossary.json")
    names = set()
    for g in gl:
        GlossaryEntry(**g)
        names.add(g["term"])
        names.update(g["aliases"])
    for b in load_work_books().values():
        for g in b["glossary"]:
            assert _norm(g["term"]) in names, f"术语丢失：{g['term']}"


def test_every_assessable_kp_has_an_archetype():
    """下游按知识点出题：每个可考查的规范知识点至少有一个题型（无教材习题的由描述生成，provenance=reconciled）。"""
    kps = read_json(DATA_DIR / "knowledge_points.json")
    covered = {a["primary_knowledge_point_id"] for a in read_json(DATA_DIR / "archetypes.json")}
    missing = [k["id"] for k in kps if k["is_assessable"] and k["id"] not in covered]
    assert not missing, f"{len(missing)} 个可考查知识点没有题型：{missing[:5]}（python -m chalkbase.stage3.gaps uncovered）"
