"""Stage 5 · 缺口发现（第一路）：逐知识点枚举「被假定已学、但知识库中没有对应知识点」的先备概念。

被引用（显式回顾、次知识点、前置边起点）的知识点在结构化数据里都已存在（Stage 2~4 的不变量保证），
所以「被引用却无引入」只可能表现为：教材内容假定了某概念，却没有任何一本书教它（D14 的面积、面积单位）。
这类缺口无法靠结构化引用发现，需要语义判断：对每个知识点 B，流水线模型看 B 的完整信息、教材习题与全部知识点清单，
列出 B 所必需但清单里没有的先备概念。每次调用以 Judgment 落盘（work/judgments/stage5_gap_discovery.jsonl）。
"""
from __future__ import annotations

import json
from collections import defaultdict

from chalkbase.annotate.client import AnnotationClient
from chalkbase.annotate.gold import ModelConfig, call_models, judgment_record
from chalkbase.common import DATA_DIR, book_of, lesson_order, read_json, read_jsonl, write_jsonl, JUDGMENTS_DIR
from chalkbase.stage4.render import render_kp

PIPELINE = ModelConfig("qwen3.7-plus", False, max_tokens=1500)
CONF_MIN = 0.8
OUT = JUDGMENTS_DIR / "stage5_gap_discovery.jsonl"

SYSTEM = """你是小学数学课程专家，正在检查一套北师大版小学数学（1～6 年级）知识库是否有「缺口」。
背景：教材由新版（2022 课标：g1a～g3b、g4a、g5a、g6a）和旧版（2011 课标：g4b、g5b、g6b）混合而成，两个版本对内容的编排不同，所以有些内容在手头这 12 本书里「没有任何一本教过」，但后面的内容却假定学生已经会了。

任务：给定一个目标知识点 B 和知识库的全部知识点清单，找出 B 所**必需**、但清单里**没有对应知识点**的先备知识点（缺口）。

判定规则：
1. 只列 B 的**直接、必需**的先备：不会它，就无法学会 B 或无法做 B 的教材习题。
2. 缺口必须是小学数学里**独立的知识点**（老师会把它当作一个考点来讲），而不是生活常识、语文能力、几句话就能讲清的小提示。
3. 清单里已有的知识点（哪怕名称不同，或是它更一般/更特殊的形式、或是同一内容在别的年级的版本）已覆盖的，**不算缺口**；拿不准时在 nearest_existing 填最接近的编号。
4. 绝大多数目标知识点没有缺口，此时返回空列表。不要为了凑数而列出缺口，不要列「加减乘除」「数的认识」这类显然已被覆盖的基础内容。
5. 每个缺口给出：规范名称、一两句描述（该学会什么）、为什么 B 需要它（引用 B 的具体内容或习题）。
只输出 JSON：{"gaps": [{"name": "...", "description": "...", "why_needed": "不超过60字", "nearest_existing": "编号或null"}], "confidence": 0.0~1.0, "reason": "不超过60字"}"""


def kp_list_text(kps: dict[str, dict]) -> tuple[str, dict[str, str]]:
    order = lesson_order()
    code2id, lines = {}, []
    for i, k in enumerate(sorted(kps, key=lambda x: (order[kps[x]["first_introduced_lesson_id"]], x))):
        code = f"K{i:03d}"
        code2id[code] = k
        v = kps[k]
        lines.append(f"{code} | {book_of(v['first_introduced_lesson_id'])} | {v['name']} | {v['thread']}")
    return "\n".join(lines), code2id


def validate(d) -> bool:
    return isinstance(d, dict) and isinstance(d.get("gaps"), list) and all(isinstance(g, dict) and g.get("name") for g in d["gaps"])


def discover(client: AnnotationClient, only: list[str] | None = None) -> list[dict]:
    kps = {k["id"]: k for k in read_json(DATA_DIR / "knowledge_points.json") if k["provenance"] == "textbook"}
    listing, code2id = kp_list_text(kps)
    code_of = {v: k for k, v in code2id.items()}
    targets = only or sorted(kps)
    system = SYSTEM + "\n\n【知识库全部知识点清单：编号 | 首次引入的书 | 名称 | 主线】\n" + listing
    msgs = [(k, f"【目标知识点 B】（编号 {code_of[k]}）\n{render_kp(k)}\n\n只输出 JSON。") for k in targets]
    res = call_models(client, system, msgs, PIPELINE, validate, role="gapdisc")
    judgments = []
    for k in targets:
        r = res[k]
        gaps = (r.parsed or {}).get("gaps", []) if r.ok else []
        for g in gaps:
            g["nearest_existing"] = code2id.get(g.get("nearest_existing")) if g.get("nearest_existing") else None
        j = judgment_record("other", f"gapdisc.{k}", "pipeline_gap_discovery", r, f"缺口发现：目标知识点 {kps[k]['name']}（{k}）", json.dumps(gaps, ensure_ascii=False))
        j["target"] = k
        j["gaps"] = gaps
        judgments.append(j)
    if not only:
        write_jsonl(OUT, judgments)
    return judgments


def candidates(judgments: list[dict] | None = None) -> list[dict]:
    """展平：每个（目标知识点, 缺口名）一条候选，已过置信度阈值。"""
    judgments = judgments if judgments is not None else read_jsonl(OUT)
    out = []
    for j in judgments:
        if not j["ok"] or j["confidence"] < CONF_MIN:
            continue
        for g in j["gaps"]:
            out.append({"target": j["target"], "judgment_id": j["id"], "confidence": j["confidence"], **g})
    return out
