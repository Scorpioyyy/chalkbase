"""Stage 2 · 成对判定：流水线模型对每个候选对给出 same / extends / different，写入 Judgment。

流水线模型（qwen3.7-plus）不出现在实体消解的标注模型组中（CLAUDE.md 4.3.7），见 decisions.md D13。
提示词主体与标注指南相同（指南定义任务本身；独立性由模型不同保证）。
"""
from __future__ import annotations

import json

from curriculum.annotate.client import AnnotationClient
from curriculum.annotate.gold import ModelConfig, call_models, judgment_record
from curriculum.stage2.gold import PAIR_TASK

PIPELINE_MODEL = ModelConfig("qwen3.7-plus", False)


def judge_pairs(pairs: list[tuple[str, str]], cfg: ModelConfig = PIPELINE_MODEL, client: AnnotationClient | None = None) -> list[dict]:
    """返回 Judgment 记录列表（附 a/b/label/narrower），顺序与输入一致。"""
    client = client or AnnotationClient(max_workers=48)
    items = [{"id": f"{a}||{b}", "a": a, "b": b} for a, b in pairs]
    msgs = [(it["id"], PAIR_TASK.render(it)) for it in items]
    results = call_models(client, PAIR_TASK.system_prompt(), msgs, cfg, PAIR_TASK.validate, role="pipe")
    out = []
    for it, (_, msg) in zip(items, msgs):
        res = results[it["id"]]
        j = judgment_record("pairwise_entity_resolution", it["id"], "pipeline", res, msg, "")
        j["id"] = f"j.s2.{it['a']}||{it['b']}"
        j["a"], j["b"] = it["a"], it["b"]
        if res.ok:
            j["label"] = res.parsed["label"]
            j["narrower"] = res.parsed.get("narrower") if res.parsed["label"] == "extends" else None
            j["conclusion"] = json.dumps({"label": j["label"], "narrower": j["narrower"]}, ensure_ascii=False)
        else:
            j["label"], j["narrower"] = None, None
            j["conclusion"] = "ERROR"
        out.append(j)
    return out
