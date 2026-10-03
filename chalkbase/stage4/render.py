"""Stage 4 提示词渲染：规范知识点描述 + （仅流水线）确定性证据。"""
from __future__ import annotations

from functools import lru_cache

from chalkbase.common import DATA_DIR, book_of, grade_of, load_work_books, read_json

EDITION = {2022: "新版", 2011: "旧版"}


@lru_cache(maxsize=1)
def _kps() -> dict[str, dict]:
    return {k["id"]: k for k in read_json(DATA_DIR / "knowledge_points.json")}


@lru_cache(maxsize=1)
def _examples() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for e in read_json(DATA_DIR / "exercises.json"):
        out.setdefault(e["primary_knowledge_point_id"], []).append(e["text"][:90].replace("\n", " "))
    return out


def book_label(bid: str) -> str:
    year = load_work_books()[bid]["registry"]["book"]["curriculum_standard_year"]
    return f"{bid}（{grade_of(bid)}年级{'上册' if bid[2] == 'a' else '下册'}，{EDITION.get(year, year)}）"


def render_kp(kid: str) -> str:
    k = _kps()[kid]
    reviews = sorted({book_of(l) for l in k["review_lesson_ids"]} - {book_of(k["first_introduced_lesson_id"])})
    lines = [
        f"名称：{k['name']}" + (f"（又称：{'、'.join(k['aliases'][:4])}）" if k["aliases"] else ""),
        f"领域/主题/主线：{k['domain']} / {k['topic']} / {k['thread']}",
        f"首次引入：{book_label(book_of(k['first_introduced_lesson_id']))}" + (f"；复现：{'、'.join(reviews)}" if reviews else ""),
        f"描述：{k['description']}",
    ]
    exs = _examples().get(kid, [])[:2]
    if exs:
        lines.append("教材习题举例：" + " ‖ ".join(exs))
    return "\n".join(lines)


def render_evidence(ev: dict) -> str:
    bits = []
    if ev["cooccurrence_count"]:
        bits.append(f"B 的 {ev['b_instances']} 道教材习题中有 {ev['cooccurrence_count']} 道同时需要用到 A（比例 {ev['cooccurrence_ratio']:.0%}）")
    if ev["reverse_cooccurrence_count"]:
        bits.append(f"反过来，A 的习题中有 {ev['reverse_cooccurrence_count']} 道用到 B")
    if ev["same_thread"]:
        bits.append("A 与 B 属于同一主线" + ("，且在该主线中 A 紧挨在 B 之前引入" if ev["thread_adjacent"] else ""))
    elif ev["same_topic"]:
        bits.append("A 与 B 属于同一课标主题、不同主线")
    if ev["stage2_extends"]:
        bits.append("实体消解阶段判定 B 是 A 的螺旋扩展（B 在 A 基础上扩大范围）")
    if ev["stage2_extends_reverse"]:
        bits.append("实体消解阶段判定 A 是 B 的螺旋扩展（A 在 B 基础上扩大范围）")
    if ev["explicit_review_refs_b"]:
        bits.append("B 所在课时有显式回顾：" + "；".join(ev["explicit_review_refs_b"]))
    order = "A 在教材序列中晚于 B 引入（注意：教材版本混杂，顺序不能作为否定依据）" if ev["order_conflict"] else f"A 在教材序列中早于 B 引入（相隔 {ev['semester_gap']} 个学期）"
    bits.append(order)
    return "\n".join(f"- {b}" for b in bits)


def render_pair(a: str, b: str, evidence: dict | None = None) -> str:
    s = f"【知识点 A】\n{render_kp(a)}\n\n【知识点 B】\n{render_kp(b)}\n"
    if evidence is not None:
        s += f"\n【确定性证据（由教材数据统计得到，仅供参考）】\n{render_evidence(evidence)}\n"
    return s + "\n问题：不掌握 A，学生能否学会 B？请判断 A → B 的关系，只输出 JSON。"
