"""Stage 5 · 主流程：`python -m curriculum.stage5`（确定性，不调用模型）。

读取 data/ 当前的知识点/课时/关系边，加上已落盘的判定（逆序边分诊、缺口定义），写回：
  data/knowledge_points.json  data/lessons.json  data/edges_relations.json
  data/stage5_reconciliation.json   修复日志（每处前移 / 补全 / 边改判的问题—证据—修复；reports/editions.md 的数据来源）
  data/gap_kps_pending_cards.json   补全知识点清单与待生成题型卡片的方向
幂等：对自己的输出再跑一次不产生变化。
"""
from __future__ import annotations

from curriculum.common import DATA_DIR, read_json, write_json
from curriculum.stage5.conflicts import load_triage
from curriculum.stage5.gaps import SPEC
from curriculum.stage5.reconcile import reconcile
from curriculum.stage5.review import load_rejections

LOG = DATA_DIR / "stage5_reconciliation.json"
PENDING = DATA_DIR / "gap_kps_pending_cards.json"


def _stage4_before() -> dict:
    from curriculum.stage4.evaluate import metrics

    out = {}
    for split in ("val", "test"):
        h = metrics(split)["headline"]
        out[split] = {k: h[k]["value"] for k in ("closure_f1", "closure_precision", "closure_recall", "anchor_closure_recall", "candidate_recall")}
    return out


def _coverage_before() -> dict:
    from curriculum.stage5.standard import load_coverage

    pre = load_coverage("pre")
    n = len(pre)
    c = sum(1 for j in pre if j["verdict"] == "covered")
    return {"covered": c, "n": n, "rate": round(c / n, 4) if n else None}


def run() -> dict:
    kps = read_json(DATA_DIR / "knowledge_points.json")
    lessons = read_json(DATA_DIR / "lessons.json")
    edges = read_json(DATA_DIR / "edges_relations.json")
    spec = read_json(SPEC) if SPEC.exists() else {"gaps": [], "edges": [], "sources": []}
    triage = load_triage()
    prev = read_json(LOG) if LOG.exists() else None
    pristine = not any(k["provenance"] == "reconciled" for k in kps)
    before = _stage4_before() if pristine else None  # 修复前的 Stage 4 指标（只在原始状态下计算一次，保存在日志里）
    kps2, lessons2, edges2, log = reconcile(kps, lessons, edges, spec, triage, prev, load_rejections())
    if before:
        log["stage4_before"] = before
        log["coverage_before"] = _coverage_before()
    elif prev:
        log["stage4_before"] = prev.get("stage4_before")
        log["coverage_before"] = prev.get("coverage_before")
    write_json(DATA_DIR / "knowledge_points.json", kps2)
    write_json(DATA_DIR / "lessons.json", lessons2)
    write_json(DATA_DIR / "edges_relations.json", edges2)
    write_json(LOG, log)
    by_id = {k["id"]: k for k in kps2}
    gap_ids = [g["kp"] for g in log["gaps"]]
    # 题型卡片方向：只给出提示，卡片由 Stage 3 的生成接口另行生成（见 reports/editions.md「待生成题型卡片」）
    write_json(PENDING, [{
        "kp_id": g, "name": by_id[g]["name"], "domain": by_id[g]["domain"], "thread": by_id[g]["thread"],
        "first_introduced_lesson_id": by_id[g]["first_introduced_lesson_id"], "mastery_level": by_id[g]["mastery_level"],
        "description": by_id[g]["description"], "grants": by_id[g]["grants"],
        "suggested_archetype_directions": next((x.get("archetype_directions", []) for x in (read_json(SPEC)["gaps"] if SPEC.exists() else []) if f"kp.{x['domain']}.{x['thread']}.{x['slug']}" == g), []),
        "cards_generated": False,
    } for g in gap_ids])
    return {"kps": len(kps2), "moves": len(log["moves"]), "gaps": len(log["gaps"]), "edge_drops": len(log["edge_drops"]), "reduction": log["reduction"]}
