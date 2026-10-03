"""Stage 6 不变量（eval/specs/stage6.md §2）：边界单调、实例不越界、词表受控。data/boundaries.json 尚未生成时跳过。"""
import random

import pytest

from curriculum.boundary import check_item
from curriculum.boundary.check import BoundaryStore, default_store
from curriculum.boundary.fold import boundary_leq, fold_boundaries, order_lessons
from curriculum.boundary.mine import instance_features
from curriculum.boundary.vocab import FRACTION_TYPES, OP_TAGS, UNITS, derive_forms, norm_unit, op_closure
from curriculum.common import DATA_DIR, read_json
from curriculum.models import CapabilityBoundary, CapabilityGrant, ItemFeatures, OperationUse

pytestmark = pytest.mark.skipif(not (DATA_DIR / "boundaries.json").exists(), reason="Stage 6 产物尚未生成")

# 「开篇探索」习题：教材有意在同一单元内先让学生尝试、下一课时才教方法（如「队列表演（一）」先算 12×15，竖式下一课时才教），
# Stage 1 把这些习题挂到了后一课时才引入的主知识点上。决策见 docs/decisions.md D29：不前移知识点引入位置（那会把"先探索后讲授"的
# 教学顺序改写成"先讲授"，也会扰动已冻结的 Stage 4/5 产物），而是容忍「同一单元内、仅早于引入课时」的情形（边界判定对此给出 borderline），
# 并用上限防止回归：此类实例当前为 23 条（占 4319 条的 0.5%），不得增加。
EXPLORATION_FIRST_MAX = 23


@pytest.fixture(scope="module")
def store() -> BoundaryStore:
    return default_store()


def test_boundaries_schema_and_coverage(store):
    lessons = {l["id"] for l in read_json(DATA_DIR / "lessons.json")}
    assert set(store.lessons) == lessons
    for lid in store.lessons:
        CapabilityBoundary(**store.boundary(lid).model_dump())


def test_monotone(store):
    prev = None
    for lid in store.lessons:
        b = store.boundary(lid)
        if prev is not None:
            assert boundary_leq(prev, b), f"边界在 {lid} 变小"
        prev = b


def test_fold_is_order_independent_and_monotone():
    rng = random.Random(0)
    lessons = [f"g1a.u1.l0{i}" for i in range(1, 6)]
    pool = [CapabilityGrant(integer_domain_max=rng.choice([None, 5, 10, 20]), decimal_max_places=rng.choice([None, 1, 2]),
                            concepts={f"c{rng.randint(0, 9)}"}, operation_operand_forms={"加法": {rng.choice(OP_TAGS["加法"])}}) for _ in range(12)]
    for _ in range(20):
        at = {l: [] for l in lessons}
        for g in pool:
            at[rng.choice(lessons)].append(g)
        a, _ = fold_boundaries(lessons, at)
        at2 = {l: rng.sample(v, len(v)) for l, v in at.items()}
        b, _ = fold_boundaries(lessons, at2)
        assert all(a[l] == b[l] for l in lessons)
        assert all(boundary_leq(a[x], a[y]) for x, y in zip(lessons, lessons[1:]))


def test_every_exercise_within_its_lesson_boundary(store):
    """教材习题实例的特征（整数位数、小数位数、分数类型、运算形态、单位、几何词汇）不超出所在课时边界。"""
    ex = read_json(DATA_DIR / "exercises.json")
    geo_vocab = set(store.introduced_at["geometry_vocab"])
    bad = []
    for e in ex:
        rep = check_item(instance_features(e, None, geo_vocab), e["lesson_id"], store)
        if not rep.in_bounds:
            bad.append((e["id"], [v.dimension + ":" + v.item_value for v in rep.violations]))
    assert not bad, f"{len(bad)} 个实例越界，例如 {bad[:5]}"


def test_primary_kp_not_used_before_introduced(store):
    """实例使用的主知识点必须已在该课时或更早引入；唯一容忍「同一单元内的开篇探索习题」（数量有上限，见文件顶部）。"""
    kps = {k["id"]: k["first_introduced_lesson_id"] for k in read_json(DATA_DIR / "knowledge_points.json")}
    early = [e for e in read_json(DATA_DIR / "exercises.json")
             if store.rank(kps[e["primary_knowledge_point_id"]]) > store.rank(e["lesson_id"])]
    cross_unit = [e["id"] for e in early if kps[e["primary_knowledge_point_id"]].rsplit(".", 1)[0] != e["lesson_id"].rsplit(".", 1)[0]]
    assert not cross_unit, f"{len(cross_unit)} 个实例使用了更晚单元才引入的主知识点：{cross_unit[:5]}"
    assert len(early) <= EXPLORATION_FIRST_MAX, f"单元内开篇探索实例增至 {len(early)}（上限 {EXPLORATION_FIRST_MAX}）"


def test_controlled_vocab(store):
    for lid in store.lessons:
        b = store.boundary(lid)
        assert b.fraction_types <= set(FRACTION_TYPES)
        assert b.units_of_measure <= set(UNITS)
        for op, forms in b.operation_operand_forms.items():
            assert forms <= set(OP_TAGS[op]), (lid, op, forms)


def test_not_yet_learned_partition(store):
    kps = read_json(DATA_DIR / "knowledge_points.json")
    first, last = store.lessons[0], store.lessons[-1]
    assert len(store.not_yet_learned(last)) == 0
    learned_first = [k for k in kps if k["first_introduced_lesson_id"] == first]
    assert len(store.not_yet_learned(first)) == len(kps) - len(learned_first)


def test_derive_forms_and_normalization():
    assert derive_forms("加法", "28", "15") == {"整数", "进位"}
    assert derive_forms("加法", "3", "7") == {"整数"}  # 凑十不算进位
    assert derive_forms("减法", "52", "27") == {"整数", "退位"}
    assert derive_forms("乘法", "12", "5") == {"整数·乘数一位数"}
    assert derive_forms("除法", "13", "4") == {"整数·表内", "整数·有余数"}
    assert derive_forms("加法", "1/4", "3/4") == {"分数·同分母"}
    assert derive_forms("加法", "1/2", "1/3") == {"分数·异分母"}
    assert derive_forms("除法", "1.5", "0.3") == {"小数·除数是小数"}
    assert op_closure("乘法", {"整数·乘数两位数"}) >= {"整数·乘数一位数", "整数·表内"}
    assert norm_unit("㎡") == "平方米" and norm_unit("升(L)") == "升" and norm_unit("km/h") == "速度单位"


def test_check_item_reports_each_dimension(store):
    f = ItemFeatures(operations=[OperationUse(op="乘法", operands=["356", "24"])], decimal_places=2,
                     units_of_measure={"公顷"}, geometry_vocab={"扇形"}, concepts={"质数"}, fraction_types={"带分数"})
    rep = check_item(f, "g1a.u1.l01", store)
    dims = {v.dimension for v in rep.violations}
    assert {"integer_domain", "decimal_places", "operation_forms", "units_of_measure", "geometry_vocab", "concepts", "fraction_types"} <= dims
    last = check_item(f, store.lessons[-1], store)
    assert last.in_bounds, last.violations
