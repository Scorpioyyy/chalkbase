"""Stage 4 不变量（eval/specs/stage4.md §2 失败模式 6、7）。data/edges_relations.json 尚未生成时跳过。"""
from collections import Counter

import pytest

from chalkbase.common import DATA_DIR, read_json, read_jsonl, JUDGMENTS_DIR
from chalkbase.models import Edge, Judgment

pytestmark = pytest.mark.skipif(not (DATA_DIR / "edges_relations.json").exists(), reason="Stage 4 产物尚未生成")


@pytest.fixture(scope="module")
def edges():
    return read_json(DATA_DIR / "edges_relations.json")


def test_edges_schema_refs_no_self_loop_unique(edges):
    kps = {k["id"] for k in read_json(DATA_DIR / "knowledge_points.json")}
    keys = Counter()
    for e in edges:
        Edge(**e)
        assert e["type"] in ("prerequisite", "builds_on", "related", "confusable")
        assert e["from_knowledge_point_id"] in kps and e["to_knowledge_point_id"] in kps
        assert e["from_knowledge_point_id"] != e["to_knowledge_point_id"], f"自环：{e['id']}"
        keys[(e["from_knowledge_point_id"], e["to_knowledge_point_id"], e["type"])] += 1
    assert max(keys.values()) == 1, "存在重复边"


def test_all_positive_judgments_emitted_including_order_conflicts(edges):
    """本阶段不得删改判定结果：每个非 none 判定都有对应边；顺序冲突边带 order_conflict 标记。"""
    from chalkbase.stage4.build import edge_type

    js = read_jsonl(JUDGMENTS_DIR / "stage4_relations.jsonl")
    names = {k["id"]: k["name"] for k in read_json(DATA_DIR / "knowledge_points.json")}
    positive = {(j["a"], j["b"], edge_type(j, names)) for j in js if j["label"] != "none"}
    # Stage 5 的改动：补全产生的新边（gap_edge）不在 Stage 4 判定中；被取消前置的边按原类型比较（原判定保留在 evidence.stage5.was_type）
    emitted = set()
    for e in edges:
        s5 = e["evidence"].get("stage5") or {}
        if s5.get("action") == "gap_edge":
            continue
        emitted.add((e["from_knowledge_point_id"], e["to_knowledge_point_id"], s5.get("was_type", e["type"])))
    assert positive == emitted
    for e in edges:
        assert e["evidence"]["judged_label"] != "none"
    for e in edges:
        assert "order_conflict" in e["evidence"]


def test_judgments_valid():
    for r in read_jsonl(JUDGMENTS_DIR / "stage4_relations.jsonl"):
        Judgment(**{k: v for k, v in r.items() if k in Judgment.model_fields})
