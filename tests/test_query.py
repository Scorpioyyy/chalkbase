"""Stage 7 查询接口测试（eval/specs/stage7.md F6–F9）：只断言接口行为与不变量，不依赖知识点/题型的具体数量，
Stage 5（补全知识点、隐含边）与 Stage 3（重写题型）改变数据后仍应通过。"""
from __future__ import annotations

import json

import pytest

from chalkbase.query import Curriculum
from chalkbase.query.evaluate import probe_scores
from chalkbase.query.retrieval import parse_query


@pytest.fixture(scope="module")
def cur():
    return Curriculum()


# ---------------------------------------------------------------- 加载与定位


def test_every_kp_is_locatable(cur):
    assert cur.knowledge_points
    for kid in cur.knowledge_points:
        loc = cur.locate(kid)
        assert 1 <= loc.grade <= 6 and loc.semester in ("a", "b")
        assert loc.lesson_id in cur.lessons


def test_lesson_order_is_total_and_follows_books(cur):
    ids = cur.lesson_ids()
    assert len(ids) == len(set(ids)) == len(cur.lessons)
    assert [cur.lesson_position(i) for i in ids] == list(range(len(ids)))
    grades = [cur.lesson_location(i).grade * 2 + (cur.lesson_location(i).semester == "b") for i in ids]
    assert grades == sorted(grades)  # 教学序列：按年级、学期递增（config/sequence.yaml 默认顺序）


def test_unknown_ids_raise(cur):
    with pytest.raises(KeyError):
        cur.lesson_position("g9z.u1.l01")
    with pytest.raises(KeyError):
        cur.chain("kp.no.such.kp")


# ---------------------------------------------------------------- 检索


def test_parse_query():
    pq = parse_query("四年级下学期，数与代数、图形与几何各出一套综合卷")
    assert pq.grade == 4 and pq.semester == "b" and set(pq.domains) == {"na", "gg"} and pq.scope
    pq = parse_query("乘法分配律的应用题")
    assert pq.grade is None and pq.semester is None and not pq.domains


def test_search_basic_properties(cur):
    hits = cur.search("三年级学乘法分配律的应用题，能不能换几种不同的生活场景出题", k=8)
    assert 0 < len(hits) <= 8
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True)
    for h in hits:
        assert h.kp_id in cur.knowledge_points
        assert h.grade == cur.kp_grade(h.kp_id) and h.lesson_id in cur.lessons
    assert any("分配律" in h.name for h in hits[:5])


def test_search_is_deterministic(cur):
    q = "五年级折线统计图，要能看出增减趋势的那种题"
    assert [h.kp_id for h in cur.search(q, 10)] == [h.kp_id for h in cur.search(q, 10)]


def test_search_filters_are_hard(cur):
    q = "小数加减法"
    assert all(h.grade == 4 for h in cur.search(q, 20, grade=4))
    assert all(cur.kp(h.kp_id).domain.value == "gg" for h in cur.search("周长 三角形", 20, domain="gg"))
    for h in cur.search("计算", 20, verifiable_type="program"):
        assert any(a.verifiable_type.value == "program" for a in cur.archetypes(h.kp_id))
    assert all(cur.kp(h.kp_id).is_assessable for h in cur.search("总复习", 20, assessable_only=True))


def test_search_by_alias_finds_kp(cur):
    k = next(k for k in cur.knowledge_points.values() if k.aliases)
    assert cur.find_kp(k.aliases[0]) and k.id in {x.id for x in cur.find_kp(k.aliases[0])}
    assert cur.find_kp(k.name)


def test_kps_filter(cur):
    for k in cur.kps(grade=3, domain="na"):
        assert cur.kp_grade(k.id) == 3 and k.domain.value == "na"
    ks = cur.kps()
    pos = [cur.locate(k.id).position for k in ks]
    assert pos == sorted(pos)


# ---------------------------------------------------------------- 关系图


def _sample_kps(cur, n=40):
    ids = sorted(cur.knowledge_points)
    return ids[:: max(1, len(ids) // n)]


def test_chain_depth_monotone_and_bounded(cur):
    for kid in _sample_kps(cur):
        d1 = {e.kp_id for e in cur.prerequisites(kid, depth=1)}
        d2 = cur.prerequisites(kid, depth=2)
        assert d1 <= {e.kp_id for e in d2}
        assert all(1 <= e.depth <= 2 for e in d2)
        full = cur.prerequisites(kid, depth=None)
        assert kid not in {e.kp_id for e in full}  # 无环
        assert len({e.kp_id for e in full}) == len(full)


def test_chain_direction_is_inverse(cur):
    checked = 0
    for kid in _sample_kps(cur, 80):
        for e in cur.prerequisites(kid, depth=1, edge_types=("prerequisite", "builds_on")):
            back = {x.kp_id for x in cur.dependents(e.kp_id, depth=1, edge_types=("prerequisite", "builds_on"))}
            assert kid in back
            checked += 1
    assert checked > 0


def test_chain_edge_type_and_implied_supersets(cur):
    for kid in _sample_kps(cur):
        strict = {e.kp_id for e in cur.prerequisites(kid, depth=None)}
        loose = {e.kp_id for e in cur.prerequisites(kid, depth=None, edge_types=("prerequisite", "builds_on"))}
        assert strict <= loose
        direct = {e.kp_id for e in cur.prerequisites(kid, depth=None)}
        with_implied = {e.kp_id for e in cur.prerequisites(kid, depth=None, include_implied=True)}
        assert direct <= with_implied
        for e in cur.prerequisites(kid, depth=None, edge_types=("prerequisite",)):
            assert e.edge_type == "prerequisite"


def test_relations_resolve(cur):
    for kid in _sample_kps(cur):
        for other, t, side in cur.relations(kid):
            assert other in cur.knowledge_points and side in ("in", "out")


# ---------------------------------------------------------------- 教学进度


def test_learned_before_is_monotone(cur):
    ids = cur.lesson_ids()
    prev: set[str] = set()
    for lid in ids[:: max(1, len(ids) // 25)]:
        cur_set = cur.learned_before(lid)
        assert prev <= cur_set
        assert cur_set <= cur.learned_before(lid, inclusive=True)
        for kid in cur_set:
            assert cur.locate(kid).position < cur.lesson_position(lid)
        prev = cur_set


def test_learned_and_not_yet_partition(cur):
    lid = cur.lesson_ids()[len(cur.lessons) // 2]
    learned, rest = cur.learned_before(lid, inclusive=True), cur.not_yet_learned(lid)
    assert not learned & rest and learned | rest == set(cur.knowledge_points)


def test_first_lesson_learns_nothing_before_it(cur):
    assert cur.learned_before(cur.lesson_ids()[0]) == set()


def test_learned_before_filters(cur):
    lid = cur.lesson_ids()[-1]
    assert all(cur.kp(k).domain.value == "sp" for k in cur.learned_before(lid, domain="sp"))
    assert all(cur.kp_grade(k) == 2 for k in cur.learned_before(lid, grade=2))


def test_review_candidates_are_already_learned(cur):
    lid = cur.lesson_ids()[-1]
    learned = cur.learned_before(lid)
    target = [k for k in sorted(cur.knowledge_points) if cur.locate(k).position > 0][-1]
    cands = cur.review_candidates(lid, [target])
    assert set(cands) <= learned and len(cands) == len(set(cands))
    assert set(cur.review_candidates(lid)) == learned


# ---------------------------------------------------------------- 题型卡片、情境、术语


def test_archetype_queries(cur):
    all_a = cur.archetypes()
    assert all_a
    for a in all_a[:: max(1, len(all_a) // 60)]:
        assert a.primary_knowledge_point_id in cur.knowledge_points
        assert len(a.rewritten_examples) >= 2 and 1 <= a.difficulty <= 5
        assert a in cur.archetypes(a.primary_knowledge_point_id)
    for vt in ("program", "rule", "human"):
        assert all(a.verifiable_type.value == vt for a in cur.archetypes(verifiable_type=vt))
    assert all(2 <= a.difficulty <= 3 for a in cur.archetypes(difficulty=(2, 3)))
    assert all(cur.kp(a.primary_knowledge_point_id).domain.value == "gg" for a in cur.archetypes(domain="gg"))
    assert all(3 in cur._archetype_grades(a) for a in cur.archetypes(grade=3))


def test_contexts_and_glossary(cur):
    for g in (1, 3, 6):
        for c in cur.contexts(grade=g):
            assert not c.applicable_grades or g in c.applicable_grades
    for a in cur.archetypes()[:200]:
        for c in cur.contexts_for(a.id):
            assert c.id in a.allowed_contexts
    if cur._contexts:
        c0 = next(iter(cur._contexts.values()))
        assert cur.context(c0.id) is c0
    g = cur._glossary[0]
    assert cur.glossary(g.term) is g
    assert cur.glossary("不存在的术语XYZ") is None


# ---------------------------------------------------------------- 能力边界与 CLI


def test_boundary_delegates_to_stage6(cur):
    """不依赖具体数值：只检查类型、单调性（后面课时的边界不小于前面）与未知课时报错。"""
    from chalkbase.models import BoundaryReport, CapabilityBoundary

    ids = cur.lesson_ids()
    first, last = ids[0], ids[-1]
    b0, b1 = cur.boundary(first), cur.boundary(last)
    assert isinstance(b0, CapabilityBoundary) and b0.lesson_id == first
    assert b0.concepts <= b1.concepts and b0.units_of_measure <= b1.units_of_measure
    assert (b0.integer_domain_max or 0) <= (b1.integer_domain_max or 0)
    rep = cur.check_item({}, last)
    assert isinstance(rep, BoundaryReport)
    with pytest.raises(KeyError):
        cur.boundary("g9z.u1.l01")
    with pytest.raises(KeyError):
        cur.check_item({}, "g9z.u1.l01")


def test_cli_smoke(capsys):
    from chalkbase.__main__ import main

    assert main(["search", "乘法分配律", "-k", "3"]) == 0
    assert "分配律" in capsys.readouterr().out
    assert main(["--json", "chain", "kp.gg.四边形.angle_sum", "--depth", "1"]) in (0,)
    json.loads(capsys.readouterr().out)
    assert main(["boundary", "g3a.u1.l01"]) == 0
    assert "lesson_id" in capsys.readouterr().out


# ---------------------------------------------------------------- 评测指标定义


def test_probe_scores_capped_recall():
    core = {f"k{i}" for i in range(10)}
    s = probe_scores([f"k{i}" for i in range(5)] + ["x"], core, set())
    assert s["recall@5"] == 1.0 and s["hit@5"] == 1.0 and s["mrr"] == 1.0
    s = probe_scores(["x", "y", "k1"], {"k1", "k2"}, set())
    assert s["recall@5"] == 0.5 and s["mrr"] == pytest.approx(1 / 3) and s["first_rank"] == 3
    assert probe_scores(["x"], {"k1"}, set())["mrr"] == 0.0
