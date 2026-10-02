"""Stage 3 · 主流程：实例规范化 → 情境库/表述规范 → 签名分组 → 题型卡片生成与校验 → 难度 → data/。"""
from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict

from curriculum.annotate.client import AnnotationClient
from curriculum.annotate.gold import judgment_record
from curriculum.common import DATA_DIR, book_index, book_of, read_json, read_jsonl, write_json, write_jsonl
from curriculum.models import ItemArchetype
from curriculum.stage3.generate import USER_TMPL, generate_cards, ngram_overlap
from curriculum.stage3.glossary import build_glossary
from curriculum.stage3.grouping import canonical_exercises, envelope, singleton_stats
from curriculum.stage3.partition import partition_instances

ANSWER_FORM_DEFAULT = "free_text"
DIFFICULTY_WEIGHTS = {"steps": 1.0, "n_knowledge_points": 1.0, "intro_span_semesters": 1.0, "reverse_thinking": 1.0, "requires_figure": 1.0}


def kp_slugs(kps: dict[str, dict]) -> dict[str, str]:
    """题型 ID 用的知识点 slug：规范 ID 末段；末段在不同知识点间重名时用「主线_末段」。"""
    last = Counter(k.rsplit(".", 1)[1] for k in kps)
    out = {}
    for k in kps:
        dom, thread, slug = k.split(".", 3)[1:]
        out[k] = slug if last[slug] == 1 else f"{thread}_{slug}"
    return out


def build_jobs(groups, ex_by_id, kps, inst_to_ctx, ctx_names) -> list[dict]:
    jobs = []
    for gi, g in enumerate(groups):
        inst = [ex_by_id[i] for i in g["instance_ids"]]
        kp = kps[g["signature"][0]]
        env = envelope(inst)
        secs = sorted({s for e in inst for s in e["secondary_knowledge_point_ids"]})
        ctxs = sorted({c for e in inst for c in inst_to_ctx.get(e["id"], ())})
        texts = [e["text"] for e in inst]
        env_for_prompt = {k: v for k, v in env.items() if k not in ("answer_forms", "n_instances") and v not in (None, [], "")}
        msg = USER_TMPL.format(
            kp_name=kp["name"],
            kp_desc=kp["description"][:120],
            secondary="、".join(kps[s]["name"] for s in secs) or "无",
            item_form=g["signature"][1],
            answer_forms=json.dumps(env["answer_forms"], ensure_ascii=False),
            grades="、".join(map(str, env["grades"])),
            envelope=json.dumps(env_for_prompt, ensure_ascii=False),
            contexts="、".join(ctx_names[c] for c in ctxs) or "无",
            n=len(inst),
            texts="\n".join(f"{i + 1}. {t[:220]}" for i, t in enumerate(texts[:6])),
        )
        jobs.append({"id": f"p{gi:04d}", "group": g, "user_msg": msg, "envelope": env, "source_texts": texts, "seed": 1000 + gi,
                     "secondary": secs, "contexts": ctxs})
    return jobs


def difficulty(archetypes: list[dict], kps: dict[str, dict], lessons_book: dict[str, str]) -> None:
    """可解释特征 → z-score 加权和 → 年级内分位数映射为 1～5（原地写入 difficulty / difficulty_features）。"""
    feats = []
    for a in archetypes:
        env = a["parameter_constraints"]["observed"]
        steps = env["operation_steps"][1] if env.get("operation_steps") else len(a["solution_steps"])
        involved = [a["primary_knowledge_point_id"], *a["secondary_knowledge_point_ids"]]
        idx = [book_index(lessons_book[kps[k]["first_introduced_lesson_id"]]) for k in involved]
        f = {
            "steps": steps,
            "n_knowledge_points": len(involved),
            "intro_span_semesters": max(idx) - min(idx),
            "reverse_thinking": int(bool(a.pop("_reverse", False))),
            "requires_figure": int(env["requires_figure_ratio"] >= 0.5),
        }
        a["difficulty_features"] = dict(f, grade=min(env["grades"]))
        feats.append(f)
    stats = {k: (statistics.mean(f[k] for f in feats), statistics.pstdev(f[k] for f in feats) or 1.0) for k in DIFFICULTY_WEIGHTS}
    for a, f in zip(archetypes, feats):
        a["difficulty_features"]["score"] = round(sum(DIFFICULTY_WEIGHTS[k] * (f[k] - stats[k][0]) / stats[k][1] for k in DIFFICULTY_WEIGHTS), 4)
    by_grade = defaultdict(list)
    for a in archetypes:
        by_grade[a["difficulty_features"]["grade"]].append(a)
    for g, arr in by_grade.items():
        arr.sort(key=lambda a: (a["difficulty_features"]["score"], a["id"]))
        n = len(arr)
        # 分数相同的题型取同一等级（按该分数首次出现的秩计算分位）
        first_rank = {}
        for r, a in enumerate(arr):
            first_rank.setdefault(a["difficulty_features"]["score"], r)
        for a in arr:
            a["difficulty"] = min(5, 1 + int(5 * first_rank[a["difficulty_features"]["score"]] / n))


def _reusable_cards() -> dict[tuple, dict]:
    """上一版 data/archetypes.json 里已通过校验（或已如实降级）的卡片，按源实例集合索引。

    实例集合没变的题型直接复用卡片，不重新生成（增量重生成，D20）；不依赖 .cache/。
    """
    path = DATA_DIR / "archetypes.json"
    if not path.exists():
        return {}
    return {tuple(sorted(a["source_instance_ids"])): a for a in read_json(path)}


def run(client: AnnotationClient | None = None) -> dict:
    client = client or AnnotationClient(max_workers=48)
    kps = {k["id"]: k for k in read_json(DATA_DIR / "knowledge_points.json")}
    lessons_book = {l["id"]: book_of(l["id"]) for l in read_json(DATA_DIR / "lessons.json")}
    exercises = canonical_exercises()
    ex_by_id = {e["id"]: e for e in exercises}
    write_json(DATA_DIR / "exercises.json", exercises)

    # 情境库与表述规范沿用已落盘的 data/contexts.json、glossary（D20：分组变化不影响情境归并，不重跑）
    contexts = read_json(DATA_DIR / "contexts.json")
    write_json(DATA_DIR / "glossary.json", build_glossary())
    ctx_names = {c["id"]: c["theme"] for c in contexts}
    inst_to_ctx: dict[str, list[str]] = defaultdict(list)
    for c in contexts:
        for i in c["source_instance_ids"]:
            inst_to_ctx[i].append(c["id"])

    old = _reusable_cards()
    groups, part_judgments = partition_instances(exercises, kps, client)
    write_jsonl(DATA_DIR / "judgments" / "stage3_partition.jsonl", part_judgments)
    jobs = build_jobs(groups, ex_by_id, kps, inst_to_ctx, ctx_names)
    new_jobs = [j for j in jobs if tuple(j["group"]["instance_ids"]) not in old]
    print(f"[增量] 题型 {len(jobs)} 个，复用 {len(jobs) - len(new_jobs)}，需生成 {len(new_jobs)}", flush=True)
    state = generate_cards(new_jobs, client)

    slugs = kp_slugs(kps)
    seq = Counter()
    archetypes, failures, gen_judgments = [], [], []
    n_reused = 0
    for j in jobs:
        g = j["group"]
        kp_id, form = g["signature"][0], g["signature"][1]
        seq[kp_id] += 1
        aid = f"at.{slugs[kp_id]}.{seq[kp_id]:02d}"
        prev = old.get(tuple(g["instance_ids"]))
        if prev is not None and j["id"] not in state:
            a = json.loads(json.dumps(prev))
            a.update(id=aid, primary_knowledge_point_id=kp_id, secondary_knowledge_point_ids=j["secondary"], item_form=form,
                     allowed_contexts=j["contexts"], source_instance_ids=g["instance_ids"])
            a["parameter_constraints"]["observed"] = j["envelope"]
            a["parameter_constraints"].pop("signature_level", None)
            a["_reverse"] = bool((prev.get("difficulty_features") or {}).get("reverse_thinking"))
            if a.get("provenance_note"):
                failures.append({"archetype_id": aid, "group": "reused", "errors": [a["provenance_note"]], "rounds": 6, "resolution": "downgraded_to_human"})
            archetypes.append(a)
            n_reused += 1
            continue
        s = state[j["id"]]
        for rnd, res in enumerate(s["calls"]):
            jr = judgment_record("other", f"{j['id']}.r{rnd + 1}", "pipeline", res, j["user_msg"], json.dumps(res.parsed, ensure_ascii=False)[:2000] if res.ok else "ERROR")
            jr["task"] = "archetype_generation"
            gen_judgments.append(jr)
        card = s["card"]
        downgraded = False
        if s["errors"] or card is None:
            failures.append({"archetype_id": aid, "group": j["id"], "errors": s["errors"], "rounds": s["rounds"],
                             "resolution": "downgraded_to_human" if card else "missing"})
            if card is None:
                continue
            # 修复轮次用尽仍未通过程序校验：如实降级为需人工核验（不带求解程序），保证实例有归属（D18）。
            # 从历次版本中取最后一个「示例不复述原题、互不相同」的版本（降级后与程序相关的校验不再适用）
            for res in reversed(s["calls"]):
                c = res.parsed if res.ok else None
                if c and len({e["problem"] for e in c["examples"]}) == len(c["examples"]) and all(
                    max((ngram_overlap(e["problem"], t) for t in j["source_texts"]), default=0) < 0.5 for e in c["examples"]):
                    card = c
                    break
            card = dict(card, verifiable_type="human", solver=None)
            downgraded = True
        env = j["envelope"]
        answer_form = max(env["answer_forms"], key=env["answer_forms"].get) if env["answer_forms"] else ANSWER_FORM_DEFAULT
        a = {
            "id": aid,
            "primary_knowledge_point_id": kp_id,
            "secondary_knowledge_point_ids": j["secondary"],
            "item_form": form,
            "template": card["template"],
            "parameter_constraints": {
                "observed": env,
                "slots": card["slots"],
                "constraints": card.get("constraints") or [],
                "answer_format": card.get("answer_format", ""),
            },
            "answer_form": answer_form,
            "solution_steps": [str(x) for x in card.get("solution_steps", [])],
            "allowed_contexts": j["contexts"],
            "figure_types": env["figure_types"],
            "verifiable_type": card["verifiable_type"],
            "solver_program": card.get("solver") if card["verifiable_type"] == "program" else None,
            "difficulty": 1,
            "source_instance_ids": g["instance_ids"],
            "rewritten_examples": [
                {"problem": e["problem"], "answer": str(e["answer"]), "solution": str(e.get("solution", "")), "params": e.get("params") or {},
                 "answer_value": e.get("answer_value")}
                for e in card["examples"]
            ],
            "typical_errors": [str(x) for x in card.get("typical_errors", [])],
            "provenance": "textbook",
            "provenance_note": ("程序校验在 6 轮修复后仍未通过（" + "；".join(s["errors"])[:200] + "），降级为 human 类，答案需人工核验") if downgraded else None,
            "_reverse": card.get("requires_reverse_thinking", False),
        }
        archetypes.append(a)
    difficulty(archetypes, kps, lessons_book)
    for a in archetypes:
        ItemArchetype(**a)
    write_json(DATA_DIR / "archetypes.json", archetypes)
    # Judgment 记录只追加不删除：旧版（签名分组）的生成记录保留为历史，本版新增记录接在后面
    jpath = DATA_DIR / "judgments" / "stage3_generation.jsonl"
    prior = read_jsonl(jpath) if jpath.exists() else []
    new_ids = {r["id"]: r for r in gen_judgments}
    # 同 ID 的旧记录若是失败调用（网络/欠费等基础设施故障，非真实结论），由重跑的新记录取代
    prior = [r for r in prior if not (r["id"] in new_ids and not r["ok"])]
    seen = {r["id"] for r in prior}
    write_jsonl(jpath, prior + [r for r in gen_judgments if r["id"] not in seen])
    write_json(DATA_DIR / "judgments" / "stage3_generation_failures.json", failures)
    summary = {
        "n_instances": len(exercises),
        "n_groups": len(groups),
        "n_archetypes": len(archetypes),
        "n_reused": n_reused,
        "n_generated": len(new_jobs),
        "n_failures": len(failures),
        "verifiable_types": dict(Counter(a["verifiable_type"] for a in archetypes)),
        "singletons": singleton_stats(groups, exercises),
        "n_contexts": len(contexts),
    }
    return summary
