"""Stage 4 · 判定与输出：对全部候选对调用流水线模型，输出全部有关系的边（含与教学顺序相反的前置边）。"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any

from chalkbase.annotate.client import AnnotationClient
from chalkbase.annotate.gold import LabelTask, ModelConfig, call_models, judgment_record
from chalkbase.common import DATA_DIR, ROOT, read_json, write_json, write_jsonl, JUDGMENTS_DIR
from chalkbase.models import Edge
from chalkbase.stage4.candidates import generate_candidates
from chalkbase.stage4.render import render_pair

PIPELINE = ModelConfig("qwen3.7-plus", False)
# 判定为 prerequisite 但置信度低于此值的，边类型降级为 builds_on（有递进依赖、不确定是否严格必需），见 docs/design.md D16
PREREQ_MIN_CONFIDENCE = 0.95
WORK_DIR = ROOT / "work"
LABELS = ("prerequisite", "builds_on", "related", "confusable", "none")


def validate(d: Any) -> bool:
    if not isinstance(d, dict) or d.get("label") not in LABELS:
        return False
    try:
        float(d.get("confidence", 0))
    except (TypeError, ValueError):
        return False
    return True


PREREQ_TASK = LabelTask(name="prerequisite", render=lambda it: render_pair(it["a"], it["b"]), validate=validate, extract=lambda d: d["label"])


def judge(cands: dict, client: AnnotationClient, cfg: ModelConfig = PIPELINE) -> list[dict]:
    pairs = sorted(cands)
    msgs = [(f"{a}->{b}", render_pair(a, b, cands[(a, b)]["evidence"])) for a, b in pairs]
    res = call_models(client, PREREQ_TASK.system_prompt(), msgs, cfg, validate, role="pipe")
    kps = {k["id"]: k for k in read_json(DATA_DIR / "knowledge_points.json")}
    out = []
    for (a, b), (iid, msg) in zip(pairs, msgs):
        r = res[iid]
        # 输入摘要只存两个知识点名称；完整提示词可由 prompt_hash 从 .cache/ 重放（控制判定记录体积，D16 后 22k 条）
        summary = f"{kps[a]['name']}（{a}） → {kps[b]['name']}（{b}）"
        j = judgment_record("prerequisite_judgment", iid, "pipeline", r, summary, json.dumps(r.parsed, ensure_ascii=False) if r.ok else "ERROR")
        j["id"] = f"j.s4.{a}->{b}"
        j["a"], j["b"] = a, b
        j["label"] = r.parsed["label"] if r.ok else None
        out.append(j)
    return out


def is_review_umbrella(name: str) -> bool:
    """总复习/复习类伞形知识点（g6b 等）：汇总已学内容，本身不是具体知识点的前置（D17）。"""
    return "总复习" in name or name.endswith("（复习）")


def edge_type(j: dict, kp_names: dict[str, str] | None = None) -> str:
    if j["label"] in ("prerequisite", "builds_on") and kp_names and is_review_umbrella(kp_names[j["a"]]):
        return "related"
    if j["label"] == "prerequisite" and j["confidence"] < PREREQ_MIN_CONFIDENCE:
        return "builds_on"
    return j["label"]


def build_edges(cands: dict, judgments: list[dict]) -> list[dict]:
    names = {k["id"]: k["name"] for k in read_json(DATA_DIR / "knowledge_points.json")}
    edges = []
    for j in judgments:
        if j["label"] in (None, "none"):
            continue
        a, b = j["a"], j["b"]
        etype = edge_type(j, names)
        ev = dict(cands[(a, b)]["evidence"], routes=cands[(a, b)]["routes"], judged_label=j["label"], judged_confidence=j["confidence"])
        e = {
            "id": f"e.{etype}.{a}.{b}",
            "type": etype,
            "from_knowledge_point_id": a,
            "to_knowledge_point_id": b,
            "evidence": ev,
            "judgment_id": j["id"],
            "is_direct": True,  # 传递约简在 Stage 5 执行
            "provenance": "textbook",
        }
        Edge(**e)
        edges.append(e)
    return edges


def run(client: AnnotationClient | None = None) -> dict:
    from chalkbase.stage4.screen import screen_all, screen_reverse  # 避免与 screen.py 循环导入

    client = client or AnnotationClient(max_workers=48)
    _, screen_judgments = screen_all(client)
    write_jsonl(JUDGMENTS_DIR / "stage4_screen.jsonl", screen_judgments)
    _, rev_judgments = screen_reverse(client)
    write_jsonl(JUDGMENTS_DIR / "stage4_screen_reverse.jsonl", rev_judgments)
    cands = generate_candidates()
    judgments = judge(cands, client)
    failed = [j for j in judgments if j["label"] is None]
    if failed:
        raise SystemExit(f"{len(failed)} 个候选对判定失败，重跑以重试（成功的已缓存）")
    edges = build_edges(cands, judgments)
    write_json(WORK_DIR / "stage4" / "stage4_candidates.json", [{"a": a, "b": b, **rec} for (a, b), rec in sorted(cands.items())])
    write_jsonl(JUDGMENTS_DIR / "stage4_relations.jsonl", judgments)
    write_json(DATA_DIR / "edges_relations.json", edges)
    return {
        "candidates": len(cands),
        "routes": dict(Counter(r for rec in cands.values() for r in rec["routes"])),
        "labels": dict(Counter(j["label"] for j in judgments)),
        "edges": dict(Counter(e["type"] for e in edges)),
        "prerequisite_order_conflicts": sum(1 for e in edges if e["type"] == "prerequisite" and e["evidence"]["order_conflict"]),
    }
