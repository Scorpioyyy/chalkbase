"""Stage 4 · 判定与输出：对全部候选对调用流水线模型，输出全部有关系的边（含与教学顺序相反的前置边）。"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any

from curriculum.annotate.client import AnnotationClient
from curriculum.annotate.gold import LabelTask, ModelConfig, call_models, judgment_record
from curriculum.common import DATA_DIR, write_json, write_jsonl
from curriculum.models import Edge
from curriculum.stage4.candidates import generate_candidates
from curriculum.stage4.render import render_pair

PIPELINE = ModelConfig("qwen3.7-plus", False)
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
    out = []
    for (a, b), (iid, msg) in zip(pairs, msgs):
        r = res[iid]
        j = judgment_record("prerequisite_judgment", iid, "pipeline", r, msg, json.dumps(r.parsed, ensure_ascii=False) if r.ok else "ERROR")
        j["id"] = f"j.s4.{a}->{b}"
        j["a"], j["b"] = a, b
        j["label"] = r.parsed["label"] if r.ok else None
        out.append(j)
    return out


def build_edges(cands: dict, judgments: list[dict]) -> list[dict]:
    edges = []
    for j in judgments:
        if j["label"] in (None, "none"):
            continue
        a, b = j["a"], j["b"]
        ev = dict(cands[(a, b)]["evidence"], routes=cands[(a, b)]["routes"])
        e = {
            "id": f"e.{j['label']}.{a}.{b}",
            "type": j["label"],
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
    from curriculum.stage4.screen import screen_all  # 避免与 screen.py 循环导入

    client = client or AnnotationClient(max_workers=48)
    screened, screen_judgments = screen_all(client)
    write_jsonl(DATA_DIR / "judgments" / "stage4_screen.jsonl", screen_judgments)
    cands = generate_candidates()
    judgments = judge(cands, client)
    failed = [j for j in judgments if j["label"] is None]
    if failed:
        raise SystemExit(f"{len(failed)} 个候选对判定失败，重跑以重试（成功的已缓存）")
    edges = build_edges(cands, judgments)
    write_json(DATA_DIR / "stage4_candidates.json", [{"a": a, "b": b, **rec} for (a, b), rec in sorted(cands.items())])
    write_jsonl(DATA_DIR / "judgments" / "stage4_relations.jsonl", judgments)
    write_json(DATA_DIR / "edges_relations.json", edges)
    return {
        "candidates": len(cands),
        "routes": dict(Counter(r for rec in cands.values() for r in rec["routes"])),
        "labels": dict(Counter(j["label"] for j in judgments)),
        "edges": dict(Counter(e["type"] for e in edges)),
        "prerequisite_order_conflicts": sum(1 for e in edges if e["type"] == "prerequisite" and e["evidence"]["order_conflict"]),
    }
