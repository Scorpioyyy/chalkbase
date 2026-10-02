"""Stage 5 不变量（eval/specs/stage5.md §1）。data/stage5_reconciliation.json 尚未生成时跳过。"""
import pytest

from curriculum.common import DATA_DIR, read_json
from curriculum.models import Edge, KnowledgePoint

pytestmark = pytest.mark.skipif(not (DATA_DIR / "stage5_reconciliation.json").exists(), reason="Stage 5 产物尚未生成")


@pytest.fixture(scope="module")
def inv():
    from curriculum.stage5.evaluate import check_invariants

    return check_invariants()


@pytest.mark.parametrize("name", [
    "order_consistency",        # 每条前置边 A→B：A 的引入不晚于 B
    "dangling_edge_refs",       # 边的两端都是存在的知识点
    "dangling_lesson_refs",     # 课时引用的知识点都存在
    "no_intro_position",        # 每个知识点都有可解析的引入课时
    "lesson_intro_mismatch",    # 课时 intro 与知识点的 first_introduced_lesson_id 一致，intro/practice 不重叠
    "kp_without_intro_lesson_listing",
    "review_before_intro",      # 复现课时不早于引入课时
    "prerequisite_cycle",       # prerequisite 子图无环
    "reduction_reachability",   # 约简不改变可达性（隐含边都有替代路径，直接边闭包等于全部边闭包）
    "reduction_minimal",        # 保留的直接边不可被其他路径取代
    "move_minimality",          # 前移恰好前移到某条出边终点的引入课时
    "reconciled_have_notes",    # reconciled 条目都有可读的修复说明
    "coverage_complete",        # 课标条目全部覆盖或逐条给出不适用理由
    "coverage_refs_valid",
])
def test_invariant(inv, name):
    assert not inv[name], f"{name} 违反 {len(inv[name])} 项，例如 {inv[name][:5]}"


def test_schema_valid():
    for k in read_json(DATA_DIR / "knowledge_points.json"):
        KnowledgePoint(**k)
    for e in read_json(DATA_DIR / "edges_relations.json"):
        Edge(**e)


def test_non_prerequisite_edges_untouched_by_reduction():
    """builds_on / related / confusable 边保持不变：全部 is_direct=True。"""
    for e in read_json(DATA_DIR / "edges_relations.json"):
        if e["type"] != "prerequisite":
            assert e["is_direct"] is True


def test_every_conflict_repair_is_logged():
    log = read_json(DATA_DIR / "stage5_reconciliation.json")
    kps = {k["id"]: k for k in read_json(DATA_DIR / "knowledge_points.json")}
    moved = {m["kp"] for m in log["moves"]}
    gaps = {g["kp"] for g in log["gaps"]}
    reconciled = {k for k, v in kps.items() if v["provenance"] == "reconciled"}
    assert reconciled == moved | gaps, f"reconciled 知识点与日志不一致：{reconciled ^ (moved | gaps)}"


def test_d14_seed_gaps_filled():
    from curriculum.stage5.evaluate import seed_probe

    assert all(seed_probe().values()), seed_probe()


@pytest.mark.xfail(reason="题型难度的重算由 `python -m curriculum.stage5 difficulty --write` 在 Stage 3 完成后触发（见 decisions.md）", strict=False)
def test_archetype_difficulty_not_stale():
    from curriculum.common import book_of
    from curriculum.stage5.difficulty import stale_archetypes

    if not (DATA_DIR / "archetypes.json").exists():
        pytest.skip("archetypes.json 不存在")
    kps = {k["id"]: k for k in read_json(DATA_DIR / "knowledge_points.json")}
    lessons_book = {l["id"]: book_of(l["id"]) for l in read_json(DATA_DIR / "lessons.json")}
    stale = stale_archetypes(read_json(DATA_DIR / "archetypes.json"), kps, lessons_book)
    assert not stale, f"{len(stale)} 个题型的难度基于过期的引入位置：{stale[:5]}"
