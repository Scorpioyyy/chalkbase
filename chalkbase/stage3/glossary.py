"""Stage 3 · 表述规范：汇总各书 glossary_observed.json → data/glossary.json（GlossaryEntry）。

Stage 1 各书的术语观测字段不统一（phrasing_pattern / phrasing_patterns、unit、source_lesson_id 等并存），
这里先做确定性字段适配，再按规范化后的 term 完全相同归并（术语本身即规范名称，不做语义合并，
避免把「验算」与「检验」这类教材刻意区分的说法混为一谈）。
"""
from __future__ import annotations

import re

from chalkbase.common import load_work_books
from chalkbase.models import GlossaryEntry


def _norm_term(t: str) -> str:
    return re.sub(r"\s+", "", t).strip()


def _as_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v if x]
    return [str(v)] if v else []


def build_glossary() -> list[dict]:
    merged: dict[str, dict] = {}
    for bid, b in load_work_books().items():
        for g in b["glossary"]:
            term = _norm_term(g["term"])
            e = merged.setdefault(term, {"term": term, "aliases": [], "notations": [], "phrasing_patterns": [], "notes": [], "books": []})
            for a in _as_list(g.get("aliases")):
                if a not in e["aliases"] and a != term:
                    e["aliases"].append(a)
            for n in _as_list(g.get("notation")):
                if n not in e["notations"]:
                    e["notations"].append(n)
            for p in _as_list(g.get("phrasing_patterns")) + _as_list(g.get("phrasing_pattern")):
                if p not in e["phrasing_patterns"]:
                    e["phrasing_patterns"].append(p)
            note_bits = _as_list(g.get("notes")) + [f"单位：{u}" for u in _as_list(g.get("unit"))]
            for n in note_bits:
                tagged = f"[{bid}] {n}"
                if tagged not in e["notes"]:
                    e["notes"].append(tagged)
            if bid not in e["books"]:
                e["books"].append(bid)
    out = []
    for term, e in merged.items():
        notation = e["notations"][0] if e["notations"] else None
        extra = [f"其他记号：{n}" for n in e["notations"][1:]]
        notes = "；".join([f"出现于：{'、'.join(e['books'])}", *extra, *e["notes"]])
        entry = {"term": term, "aliases": e["aliases"], "notation": notation, "phrasing_patterns": e["phrasing_patterns"], "notes": notes}
        GlossaryEntry(**entry)
        out.append(entry)
    out.sort(key=lambda x: x["term"])
    return out
