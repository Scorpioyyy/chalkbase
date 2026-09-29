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


REVERSE_INSTRUCTION = """
---
## 本次任务的形式（反向筛选）

下面给出一个「基础知识点 A」和知识库中全部其他知识点的清单（编号、首次引入的书、名称、领域/主线）。请逐条浏览**整个清单**，找出所有**直接建立在 A 之上**的知识点 B：不掌握 A 就学不会 B，且 A 是 B 直接依赖的那一层。
不要受清单中「引入书」先后的限制——教材版本混杂。本步只是初筛候选，宁多勿漏：拿不准的也请列出；不相关的不要列。
只输出 JSON：{"dependents": ["编号", ...], "confidence": 0.0~1.0, "reason": "不超过80字"}
"""


def screen_reverse(client: AnnotationClient) -> tuple[set[tuple[str, str]], list[dict]]:
    """反向筛选（D15 补充）：对每个 A 列出直接依赖它的 B，与正向筛选互补。"""
    kps, pos, _ = load_canonical()
    targets = sorted(kps, key=lambda k: (pos[k], k))
    rendered = {k: render_anchor({"anchor": k}) for k in targets}
    msgs = [(k, rendered[k][0].replace("【目标知识点 B】", "【基础知识点 A】")) for k in targets]
    val = lambda d: isinstance(d, dict) and isinstance(d.get("dependents"), list)
    res = call_models(client, PREREQ_TASK.system_prompt() + REVERSE_INSTRUCTION, msgs, PIPELINE, val, role="screen_rev")
    pairs, judgments = set(), []
    for k in targets:
        r = res[k]
        code2id = rendered[k][1]
        picked = [code2id[c] for c in (r.parsed or {}).get("dependents", []) if c in code2id] if r.ok else []
        for b in picked:
            pairs.add((k, b))
        judgments.append(judgment_record("prerequisite_judgment", f"screen_rev.{k}", "pipeline_screen_reverse", r,
                                         f"基础知识点 {k} + 全表清单", json.dumps(picked, ensure_ascii=False)))
    return pairs, judgments
