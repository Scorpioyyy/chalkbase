"""Stage 5 · 逆序前置边分诊：对每条「A 的引入晚于 B」的前置边判断是版本冲突还是判定错误。

- move：A 确实是 B 的必需前置，逆序是教材编排差异（新旧版接缝，或同一本书先讲后补）造成的 → 修复 = 把 A 的引入前移到 B 的课时；
- drop：A 并非 B 的严格必需前置（伞形/总结性概念、只是相关）→ Stage 4 判定错误，边改记为 related，不前移。
分诊由流水线模型完成（qwen3.7-plus，思考模式；不在标注模型组内），以 Judgment 落盘：data/judgments/stage5_conflict_triage.jsonl。
"""
from __future__ import annotations

import json

from curriculum.annotate.client import AnnotationClient
from curriculum.annotate.gold import ModelConfig, call_models, judgment_record
from curriculum.common import DATA_DIR, book_of, lesson_order, read_json, read_jsonl, write_jsonl
from curriculum.stage4.render import EDITION, book_label

TRIAGE = DATA_DIR / "judgments" / "stage5_conflict_triage.jsonl"
CFG = ModelConfig("qwen3.7-plus", True, max_tokens=4000)

SYSTEM = """你是小学数学课程专家。北师大版小学数学 1～6 年级教材由新版（2022 课标，g1a～g3b、g4a、g5a、g6a）和旧版（2011 课标，g4b、g5b、g6b）混合而成，两版内容的编排顺序不同。
下面一条前置关系「A → B」（A 是 B 的前置）此前被判定成立，但 A 在教材序列中的首次引入**晚于** B，与教学顺序相反。请分诊：
- move（版本/编排冲突）：A 确实是 B 的必需前置（不会 A 就学不会 B），逆序是因为新旧版编排不同，或同一本书里先讲 B 后补讲 A。修复方式是把 A 的引入前移到 B 的课时。
- drop（判定错误）：A 并不是 B 的严格必需前置——例如 A 是总结性/概括性的伞形知识点（「自然数的认识」之类，只是给已学内容命名），或 A 与 B 只是相关、相互印证，没有「不会 A 就学不会 B」的依赖。这样的边应当取消前置属性，而不是硬把 A 前移。
判断时请看 A 与 B 各自的具体内容与引入位置，以及 A 在该课时的作用。
只输出 JSON：{"decision": "move|drop", "category": "version_conflict|within_book_order|judgment_error", "confidence": 0.0~1.0, "reason": "不超过100字"}
category：version_conflict = 新旧版编排不同；within_book_order = 同一本书内先后顺序偶然；judgment_error = 判定错误（decision 必须为 drop）。"""


def _card(k: dict, lesson_title: dict[str, str]) -> str:
    lid = k["first_introduced_lesson_id"]
    return "\n".join([
        f"名称：{k['name']}",
        f"领域/主线：{k['domain']} / {k['thread']}",
        f"首次引入：{book_label(book_of(lid))}，课时 {lid}「{lesson_title[lid]}」",
        f"描述：{k['description']}",
    ])


def _validate(d) -> bool:
    return isinstance(d, dict) and d.get("decision") in ("move", "drop") and d.get("category") in ("version_conflict", "within_book_order", "judgment_error")


def order_conflicts(kps: dict[str, dict], edges: list[dict]) -> list[dict]:
    order = lesson_order()
    return [e for e in edges if e["type"] == "prerequisite" and order[kps[e["from_knowledge_point_id"]]["first_introduced_lesson_id"]] > order[kps[e["to_knowledge_point_id"]]["first_introduced_lesson_id"]]]


def triage(client: AnnotationClient, kps: dict[str, dict], conflicts: list[dict]) -> list[dict]:
    lesson_title = {l["id"]: l["title"] for l in read_json(DATA_DIR / "lessons.json")}
    msgs = []
    for e in conflicts:
        a, b = e["from_knowledge_point_id"], e["to_knowledge_point_id"]
        msgs.append((f"{a}->{b}", f"【知识点 A】\n{_card(kps[a], lesson_title)}\n\n【知识点 B】\n{_card(kps[b], lesson_title)}\n\n问题：A → B 的逆序前置边应如何处理？只输出 JSON。"))
    res = call_models(client, SYSTEM, msgs, CFG, _validate, role="triage")
    out = []
    for e, (iid, msg) in zip(conflicts, msgs):
        r = res[iid]
        j = judgment_record("other", f"triage.{iid}", "pipeline_conflict_triage", r, f"逆序前置边分诊：{kps[e['from_knowledge_point_id']]['name']} → {kps[e['to_knowledge_point_id']]['name']}",
                            json.dumps(r.parsed, ensure_ascii=False) if r.ok else "ERROR")
        j.update({"a": e["from_knowledge_point_id"], "b": e["to_knowledge_point_id"], "decision": r.parsed["decision"] if r.ok else None,
                  "category": r.parsed["category"] if r.ok else None})
        out.append(j)
    write_jsonl(TRIAGE, out)
    return out


def load_triage() -> list[dict]:
    return read_jsonl(TRIAGE)
