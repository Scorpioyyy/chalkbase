"""Stage 3 · 情境库：汇总各书情境观测 → data/contexts.json（Context）。

1. 确定性收集：实例的 `context_theme` + 各书 `contexts_observed.json`（字段格式不统一，这里做适配）。
2. 语义归并（确实需要语义理解，交给流水线模型 qwen3.7-plus，Judgment 落盘）：
   (a) 从全部原始主题（附出现次数）归纳情境类目表；(b) 分批把每个原始主题归入一个类目。
3. 数值范围取观测包络：该情境下所有实例 `text` 中出现的数（Decimal 解析）的最小/最大值、最多小数位数，
   按年级分别统计；附各书原始数值备注。适用年级 = 源实例年级并集。
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation

from chalkbase.annotate.client import AnnotationClient, AnnotationRequest
from chalkbase.annotate.gold import ModelConfig, call_models, judgment_record
from chalkbase.common import grade_of, load_work_books
from chalkbase.models import Context

PIPELINE = ModelConfig("qwen3.7-plus", False, max_tokens=8192)
NONE_THEMES = {"无", "none", "null", "无情境", "纯计算", ""}
NUM_RE = re.compile(r"(?<![\d.])\d+(?:\.\d+)?")

TAXONOMY_PROMPT = """你在为小学数学出题系统整理「生活情境库」。下面是从 12 本北师大版小学数学教材习题中观测到的全部情境主题原始写法（每行：主题 | 出现次数）。很多写法其实是同一类情境（如「购物」「生活购物」「购买文具」），也有复合写法。

请归纳出一张情境类目表，要求：
- 类目以「出题时老师会说的情境」为粒度，如「购物与付钱」「交通出行与行程」「校园活动」「农业生产」「体育比赛与运动」「手工制作与折纸」「动物观察」……
- 类目数量**必须在 40～70 个之间**（少于 40 个视为不合格）；互不重叠；宁可细一些（如把「购物」分成「超市购物与找零」「文具/图书购买」「打折促销」），也不要出现吞并上百种写法的大杂烩类目。每个类目给出简短定义与常见数量（如价格、路程、人数）。
- 另设一个类目「纯数学/无生活情境」，只收纳确实没有生活背景的主题（如「数的规律」「图形操作」）；凡是带有真实社会或生活背景的主题（人口、调查、电视节目、节日……）都不得归入它，应归入或新设相应的生活类目（如「社会调查与人口统计」）。数学游戏、猜数游戏等单独成类目「数学游戏」。

只输出 JSON：{"categories": [{"name": "类目名（不超过10字）", "definition": "不超过30字", "typical_quantities": ["..."]}]}"""

ASSIGN_PROMPT = """把下面每个原始情境主题归入给定类目表中**最合适的一个**类目（必须用类目表里的原名）。复合主题按其主要情境归类。带有真实社会或生活背景的主题不要归入「纯数学/无生活情境」。

类目表：
{taxonomy}

原始主题（每行一个，行首是编号）：
{themes}

只输出 JSON：{{"assign": {{"编号": "类目名", ...}}}}，覆盖全部编号。"""


def _norm(t: str) -> str:
    return re.sub(r"\s+", "", t or "").strip()


def collect_observations() -> tuple[Counter, dict[str, list[str]], dict[str, list[str]]]:
    """返回（主题计数，主题→实例 ID 列表，主题→原始数值备注）。"""
    counts: Counter = Counter()
    inst: dict[str, list[str]] = defaultdict(list)
    notes: dict[str, list[str]] = defaultdict(list)
    for bid, b in load_work_books().items():
        for e in b["exercises"]:
            t = _norm(e.get("context_theme"))
            if t and t not in NONE_THEMES:
                counts[t] += 1
                inst[t].append(e["id"])
        for c in b["contexts"]:
            t = _norm(c.get("theme"))
            if not t or t in NONE_THEMES:
                continue
            counts[t] += 0
            for iid in c.get("source_instance_ids", []) or []:
                if iid not in inst[t]:
                    inst[t].append(iid)
            for k in ("value_notes", "value_ranges", "value_range", "note"):
                v = c.get(k)
                if v:
                    notes[t].append(f"[{bid}] " + (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)))
    return counts, inst, notes


def _validate_tax(d) -> bool:
    return isinstance(d, dict) and isinstance(d.get("categories"), list) and 40 <= len(d["categories"]) <= 80 and all(
        isinstance(c, dict) and c.get("name") for c in d["categories"]
    )


def build_contexts(client: AnnotationClient | None = None, batch: int = 80) -> tuple[list[dict], dict[str, str], list[dict]]:
    """返回（Context 列表，原始主题→情境 ID，Judgment 列表）。"""
    client = client or AnnotationClient(max_workers=48)
    counts, inst, notes = collect_observations()
    themes = sorted(counts, key=lambda t: (-counts[t], t))
    listing = "\n".join(f"{t} | {counts[t]}" for t in themes)
    tax_res = call_models(client, "你是严谨的小学数学课程专家。", [("taxonomy", TAXONOMY_PROMPT + "\n\n" + listing)], PIPELINE, _validate_tax, role="ctx")["taxonomy"]
    if not tax_res.ok:
        raise RuntimeError(f"情境类目归纳失败：{tax_res.error}")
    judgments = [judgment_record("other", "context_taxonomy", "pipeline", tax_res, "全部原始情境主题（见 contexts.py）", json.dumps(tax_res.parsed, ensure_ascii=False))]
    cats = tax_res.parsed["categories"]
    names = [c["name"] for c in cats]
    tax_text = "\n".join(f"- {c['name']}：{c.get('definition', '')}" for c in cats)

    items = []
    for i in range(0, len(themes), batch):
        chunk = themes[i : i + batch]
        lines = "\n".join(f"T{j:03d} {t}" for j, t in enumerate(chunk))
        items.append((f"assign{i // batch:02d}", ASSIGN_PROMPT.format(taxonomy=tax_text, themes=lines), chunk))

    def _val_assign(chunk):
        return lambda d: isinstance(d, dict) and isinstance(d.get("assign"), dict) and all(
            d["assign"].get(f"T{j:03d}") in names for j in range(len(chunk))
        )

    reqs = [
        AnnotationRequest(request_id=iid, model=PIPELINE.model, thinking=PIPELINE.thinking,
                          messages=[{"role": "system", "content": "你是严谨的小学数学课程专家。"}, {"role": "user", "content": msg}],
                          response_schema_validator=_val_assign(chunk), max_tokens=PIPELINE.max_tokens)
        for iid, msg, chunk in items
    ]
    theme_to_cat: dict[str, str] = {}
    for (iid, msg, chunk), res in zip(items, client.run_batch(reqs)):
        if not res.ok:
            raise RuntimeError(f"情境归类失败 {iid}：{res.error}")
        judgments.append(judgment_record("other", f"context_assign.{iid}", "pipeline", res, f"{len(chunk)} 个原始主题", json.dumps(res.parsed, ensure_ascii=False)))
        for j, t in enumerate(chunk):
            theme_to_cat[t] = res.parsed["assign"][f"T{j:03d}"]

    ex_by_id = {e["id"]: (bid, e) for bid, b in load_work_books().items() for e in b["exercises"]}
    cat_members: dict[str, list[str]] = defaultdict(list)
    for t, c in theme_to_cat.items():
        cat_members[c].append(t)
    contexts, theme_to_ctx = [], {}
    for c in cats:
        members = cat_members.get(c["name"], [])
        if not members:
            continue
        cid = f"ctx.{c['name']}"
        src = sorted({i for t in members for i in inst[t] if i in ex_by_id})
        grades = sorted({grade_of(ex_by_id[i][0]) for i in src})
        by_grade: dict[int, list[Decimal]] = defaultdict(list)
        for i in src:
            bid, e = ex_by_id[i]
            for m in NUM_RE.findall(e["text"]):
                try:
                    by_grade[grade_of(bid)].append(Decimal(m))
                except InvalidOperation:
                    pass
        envelope = {
            str(g): {
                "min": str(min(v)),
                "max": str(max(v)),
                "max_decimal_places": max(max(0, -d.as_tuple().exponent) for d in v),
                "n_numbers": len(v),
            }
            for g, v in sorted(by_grade.items())
            if v
        }
        ctx = {
            "id": cid,
            "theme": c["name"],
            "applicable_grades": grades,
            "value_ranges": {
                "definition": c.get("definition", ""),
                "typical_quantities": c.get("typical_quantities", []),
                "numbers_in_texts_by_grade": envelope,
                "observed_theme_variants": sorted(members, key=lambda t: -counts[t])[:30],
                "observed_value_notes": [n for t in members for n in notes.get(t, [])][:20],
            },
            "source_instance_ids": src,
            "provenance": "textbook",
        }
        Context(**ctx)
        contexts.append(ctx)
        for t in members:
            theme_to_ctx[t] = cid
    return contexts, theme_to_ctx, judgments
