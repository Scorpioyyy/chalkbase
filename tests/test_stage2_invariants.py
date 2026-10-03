"""Stage 2 不变量（eval/specs/stage2.md §2 失败模式 6/7/8 及 3b 的监控）。data/ 尚未生成时跳过。"""
import re
from collections import defaultdict

import pytest

from chalkbase.common import DATA_DIR, lesson_order, load_work_books, local_key, local_kps, read_json, read_jsonl, JUDGMENTS_DIR
from chalkbase.models import Edge, EdgeType, Judgment, KnowledgePoint, Lesson

pytestmark = pytest.mark.skipif(not (DATA_DIR / "kp_local_map.json").exists(), reason="Stage 2 产物尚未生成")

KP_ID_RE = re.compile(r"^kp\.(na|gg|sp|ip)\.[^.\s]+\.[^.\s]+$")


@pytest.fixture(scope="module")
def canon():
    return {k["id"]: k for k in read_json(DATA_DIR / "knowledge_points.json")}


@pytest.fixture(scope="module")
def l2c():
    return {local_key(r["book_id"], r["local_id"]): r["canonical_id"] for r in read_json(DATA_DIR / "kp_local_map.json")}


def test_canonical_schema_and_ids(canon):
    for cid, k in canon.items():
        KnowledgePoint(**k)
        assert KP_ID_RE.match(cid), f"规范 ID 不符合约定：{cid}"
    assert len(canon) == len(read_json(DATA_DIR / "knowledge_points.json")), "规范 ID 不唯一"


def test_every_local_kp_mapped(l2c, canon):
    keys = {k["_key"] for k in local_kps()}
    assert keys == set(l2c), f"映射缺失/多余：缺 {keys - set(l2c)}，多 {set(l2c) - keys}"
    assert set(l2c.values()) <= set(canon), "映射指向不存在的规范 ID"
    # Stage 5 补全的缺口知识点（provenance=reconciled）按定义没有教材局部成员
    orphan = set(canon) - set(l2c.values())
    assert all(canon[k]["provenance"] == "reconciled" for k in orphan), "存在没有任何局部成员的规范知识点（非 Stage 5 补全）"


def test_cannot_link_respected(l2c):
    for bid, b in load_work_books().items():
        for l in b["lessons"]:
            ids = l.get("intro_knowledge_point_ids", [])
            mapped = [l2c[local_key(bid, k)] for k in ids]
            assert len(set(mapped)) == len(set(ids)), f"{l['id']} 同一课时引入的不同知识点被合并：{ids} → {mapped}"


def test_canonical_attributes_merged(canon, l2c):
    order = lesson_order()
    members = defaultdict(list)
    kps = {k["_key"]: k for k in local_kps()}
    for key, cid in l2c.items():
        members[cid].append(kps[key])
    for cid, mem in members.items():
        k = canon[cid]
        earliest = min((m["first_introduced_lesson_id"] for m in mem), key=lambda l: order[l])
        if k["provenance"] == "reconciled":  # Stage 5 顺序冲突前移：引入只会比成员中最早者更早，原位置记为复现
            assert order[k["first_introduced_lesson_id"]] <= order[earliest], f"{cid} 前移后引入课时反而更晚"
        else:
            assert k["first_introduced_lesson_id"] == earliest, f"{cid} 引入课时不是成员中最早的"
        covered = {k["first_introduced_lesson_id"], *k["review_lesson_ids"]}
        for m in mem:
            assert {m["first_introduced_lesson_id"], *m.get("review_lesson_ids", [])} <= covered, f"{cid} 丢失成员 {m['_key']} 的课时"
            assert m["name"] == k["name"] or m["name"] in k["aliases"], f"{cid} 丢失成员名称 {m['name']}"


def test_lessons_reference_canonical(canon):
    lessons = read_json(DATA_DIR / "lessons.json")
    assert len(lessons) == sum(len(b["lessons"]) for b in load_work_books().values())
    intro_of = {}
    for l in lessons:
        Lesson(**l)
        for k in l["intro_knowledge_point_ids"] + l["practice_knowledge_point_ids"]:
            assert k in canon, f"{l['id']} 引用了不存在的规范知识点 {k}"
        assert not set(l["intro_knowledge_point_ids"]) & set(l["practice_knowledge_point_ids"]), f"{l['id']} intro 与 practice 重叠"
        for k in l["intro_knowledge_point_ids"]:
            assert canon[k]["first_introduced_lesson_id"] == l["id"], f"{l['id']} 的 intro {k} 并非首次引入于此"
            intro_of[k] = l["id"]


def test_extends_edges_valid(canon):
    for e in read_json(DATA_DIR / "edges_extends.json"):
        Edge(**e)
        assert e["type"] == EdgeType.EXTENDS.value
        assert e["from_knowledge_point_id"] in canon and e["to_knowledge_point_id"] in canon
        assert e["from_knowledge_point_id"] != e["to_knowledge_point_id"]


def test_judgments_valid():
    rows = read_jsonl(JUDGMENTS_DIR / "stage2_pairwise.jsonl")
    assert rows
    for r in rows:
        Judgment(**{k: v for k, v in r.items() if k in Judgment.model_fields})
        assert r["label"] in ("same", "extends", "different")
