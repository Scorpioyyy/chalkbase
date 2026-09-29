"""Stage 4 · 第三路候选：模型筛选路（decisions.md D15）。

动机（val 锚点金标上观测到的失败）：KICKOFF 的两路候选（同主线/同主题时间约束 + 共现）召回只有 0.83，
漏掉的全是跨主题 / 跨领域的前置（如「10以内加减法」→「总量与分量的关系」、「千米的认识」→「速度、时间与路程」）。
做法：对每个知识点 B，流水线模型（qwen3.7-plus，不在标注模型组中）看 B 的完整信息与全部知识点清单，
挑出可能的直接前置，作为候选（不是结论——仍要经过逐对判定）。每次调用以 Judgment 落盘。
"""
from __future__ import annotations

import json

from curriculum.annotate.client import AnnotationClient
from curriculum.annotate.gold import ModelConfig, call_models, judgment_record
from curriculum.stage4.build import PREREQ_TASK
from curriculum.stage4.candidates import load_canonical
from curriculum.stage4.gold import ANCHOR_INSTRUCTION, render_anchor

PIPELINE = ModelConfig("qwen3.7-plus", False, max_tokens=2048)
SCREEN_SUFFIX = "\n（本步只是初筛候选，宁多勿漏：拿不准是否直接前置的，也请列出；不相关的不要列。）"


def screen_all(client: AnnotationClient) -> tuple[set[tuple[str, str]], list[dict]]:
    kps, pos, _ = load_canonical()
    targets = sorted(kps, key=lambda k: (pos[k], k))
    rendered = {k: render_anchor({"anchor": k}) for k in targets}
    system = PREREQ_TASK.system_prompt() + ANCHOR_INSTRUCTION + SCREEN_SUFFIX
    val = lambda d: isinstance(d, dict) and isinstance(d.get("prerequisites"), list)
    res = call_models(client, system, [(k, rendered[k][0]) for k in targets], PIPELINE, val, role="screen")
    pairs, judgments = set(), []
    for k in targets:
        r = res[k]
        code2id = rendered[k][1]
        picked = [code2id[c] for c in (r.parsed or {}).get("prerequisites", []) if c in code2id] if r.ok else []
        for a in picked:
            pairs.add((a, k))
        j = judgment_record("prerequisite_judgment", f"screen.{k}", "pipeline_screen", r, f"目标知识点 {k} + 全表清单", json.dumps(picked, ensure_ascii=False))
        judgments.append(j)
    return pairs, judgments
