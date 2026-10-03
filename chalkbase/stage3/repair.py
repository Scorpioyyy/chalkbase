"""Stage 3 · 已有题型卡片的定向修复：对 `data/archetypes.json` 中未通过当前校验的 program 卡片，
带着校验错误让流水线模型修正 template / slots / solver / 示例，修复后重新走 `verify_card`（D18 的全部校验）。

目前用于 V7（模板占位符必须能渲染）与 V8（求解程序对随机参数不能多数返回 None）：
    python -m chalkbase.stage3.repair placeholders|none [--limit N] [--dry-run]
修复成功的卡片原地更新（其余字段、ID、难度不变）；用尽轮次仍未通过的卡片保持原样并列入失败清单。
每次模型调用写入 work/judgments/stage3_repair.jsonl。
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor

from chalkbase.annotate.client import AnnotationClient, AnnotationRequest
from chalkbase.annotate.gold import judgment_record
from chalkbase.common import DATA_DIR, JUDGMENTS_DIR, read_json, read_jsonl, write_json, write_jsonl
from chalkbase.stage3.generate import PIPELINE, REPAIR_THINKING, SYSTEM, USER_TMPL, validate_card, verify_card

MAX_ROUNDS = 4
LATE = "\n再次提醒：占位符只能是槽位名；已知量写成槽位并让 solve 接收它；待作答的空写 ______；题面和示例题面里不得出现答案；answer_value 必须与 solve(**params) 一致。"


def card_of(a: dict) -> dict:
    """题型卡片（archetypes.json 条目）→ 生成器的卡片格式。"""
    pc = a["parameter_constraints"]
    return {"template": a["template"], "slots": pc["slots"], "constraints": pc.get("constraints") or [],
            "verifiable_type": a["verifiable_type"], "solver": a["solver_program"], "answer_format": pc.get("answer_format", ""),
            "solution_steps": a["solution_steps"], "typical_errors": a["typical_errors"],
            "examples": [{"params": e["params"], "problem": e["problem"], "answer": e["answer"], "answer_value": e.get("answer_value"),
                          "solution": e["solution"]} for e in a["rewritten_examples"]]}


def user_msg(a: dict, kps: dict, ex_text: dict[str, str], ctx_names: dict[str, str]) -> str:
    obs = a["parameter_constraints"]["observed"]
    env = {k: v for k, v in obs.items() if k not in ("answer_forms", "n_instances") and v not in (None, [], "")}
    kp = kps[a["primary_knowledge_point_id"]]
    texts = [ex_text[i] for i in a["source_instance_ids"] if i in ex_text]
    return USER_TMPL.format(
        kp_name=kp["name"], kp_desc=kp["description"][:120],
        secondary="、".join(kps[s]["name"] for s in a["secondary_knowledge_point_ids"] if s in kps) or "无",
        item_form=a["item_form"], answer_forms=json.dumps(obs.get("answer_forms", {}), ensure_ascii=False),
        grades="、".join(map(str, obs.get("grades", []))), envelope=json.dumps(env, ensure_ascii=False),
        contexts="、".join(ctx_names.get(c, c) for c in a["allowed_contexts"]) or "无", n=len(texts),
        texts="\n".join(f"{i + 1}. {t[:220]}" for i, t in enumerate(texts[:6])))


def repair_one(a: dict, ctx: dict, client: AnnotationClient, seed: int) -> dict:
    card = card_of(a)
    src = [ctx["ex_text"][i] for i in a["source_instance_ids"] if i in ctx["ex_text"]]
    env = a["parameter_constraints"]["observed"]
    msg0 = user_msg(a, ctx["kps"], ctx["ex_text"], ctx["ctx_names"])
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": msg0}]
    errs = verify_card(card, env, src, seed)
    out = {"id": a["id"], "initial_errors": errs, "rounds": 0, "errors": errs, "card": None, "calls": []}
    if not errs:
        return out
    for rnd in range(MAX_ROUNDS):
        messages = messages + [{"role": "assistant", "content": json.dumps(card, ensure_ascii=False)},
                               {"role": "user", "content": "程序校验没有通过，请修正后输出完整的新 JSON：\n- " + "\n- ".join(errs[:8]) + (LATE if rnd >= 1 else "")}]
        cfg = REPAIR_THINKING if rnd >= 2 else PIPELINE
        req = AnnotationRequest(request_id=a["id"], model=cfg.model, thinking=cfg.thinking, messages=list(messages),
                                response_schema_validator=validate_card, max_tokens=cfg.max_tokens)
        res = client._call_one(req)
        out["rounds"] = rnd + 1
        out["calls"].append((res, messages[-1]["content"]))
        if not res.ok:
            errs = [f"模型输出不合法：{res.error}"]
            messages = messages[:-2]
            continue
        card = res.parsed
        try:
            errs = verify_card(card, env, src, seed)
        except Exception as e:  # 约束字段格式异常等
            errs = [f"校验时异常：{type(e).__name__}: {e}"]
        if card["verifiable_type"] != a["verifiable_type"]:
            errs = errs + [f"verifiable_type 必须保持 {a['verifiable_type']}" + ("，并提供 solver" if a["verifiable_type"] == "program" else "")]
        if not errs:
            out["card"] = card
            break
    out["errors"] = errs
    return out


def apply_card(a: dict, card: dict) -> None:
    pc = a["parameter_constraints"]
    a["template"] = card["template"]
    pc["slots"], pc["constraints"], pc["answer_format"] = card["slots"], card.get("constraints") or [], card.get("answer_format", "")
    a["solution_steps"] = [str(x) for x in card.get("solution_steps", [])]
    a["solver_program"] = card["solver"] if a["verifiable_type"] == "program" else None
    a["rewritten_examples"] = [{"problem": e["problem"], "answer": str(e["answer"]), "solution": str(e.get("solution", "")),
                                "params": e.get("params") or {}, "answer_value": e.get("answer_value")} for e in card["examples"]]
    a["typical_errors"] = [str(x) for x in card.get("typical_errors", [])]


def _returns_none(a: dict) -> bool:
    """V8：求解程序对多数随机参数返回 None（无解）。"""
    from chalkbase.runtime.sandbox import run_solver
    from chalkbase.runtime.features import slot_types

    pc = a["parameter_constraints"]
    r = run_solver(a["solver_program"], slot_types(pc["slots"]), None, pc.get("constraints"), timeout=60.0,
                   probe={"slots": pc["slots"], "n": 20, "seed": 7000})
    return bool(r.get("ok")) and sum(1 for x in r["results"] if x["ok"] and x["typed"][0] == "none") > 5


def _not_renderable(a: dict) -> bool:
    from chalkbase.runtime.instantiate import InstantiationError, sample_candidates, unresolved_placeholders
    from chalkbase.models import ItemArchetype

    if unresolved_placeholders(a["template"], a["parameter_constraints"]["slots"]):
        return True
    try:
        c = sample_candidates(ItemArchetype(**a), [0], 3, timeout=60.0)[0]
    except InstantiationError:
        return True
    return not c or not all(x.get("ok") for x in c)


def run_placeholders(client: AnnotationClient | None = None, limit: int | None = None, dry_run: bool = False, workers: int = 16,
                     mode: str = "placeholders") -> dict:
    from chalkbase.runtime.instantiate import unresolved_placeholders

    client = client or AnnotationClient(max_workers=workers)
    archetypes = read_json(DATA_DIR / "archetypes.json")
    kps = {k["id"]: k for k in read_json(DATA_DIR / "knowledge_points.json")}
    ctx = {"kps": kps, "ex_text": {e["id"]: e["text"] for e in read_json(DATA_DIR / "exercises.json")},
           "ctx_names": {c["id"]: c["theme"] for c in read_json(DATA_DIR / "contexts.json")}}
    if mode == "placeholders":
        todo = [a for a in archetypes if a["verifiable_type"] == "program" and unresolved_placeholders(a["template"], a["parameter_constraints"]["slots"])]
    elif mode == "none":
        todo = [a for a in archetypes if a["verifiable_type"] == "program" and _returns_none(a)]
    else:  # render：rule / human 卡片中占位符无法解析、或槽位约束采不到参数的
        todo = [a for a in archetypes if a["verifiable_type"] != "program" and _not_renderable(a)]
    if limit:
        todo = todo[:limit]
    print(f"[repair] 待修复 {len(todo)} 张", file=sys.stderr, flush=True)
    if dry_run:
        return {"todo": [a["id"] for a in todo]}
    with ThreadPoolExecutor(workers) as ex:
        results = list(ex.map(lambda t: repair_one(t[1], ctx, client, 7000 + t[0]), enumerate(todo)))
    by_id = {a["id"]: a for a in archetypes}
    fixed, failed, records = [], [], []
    for r in results:
        for rnd, (res, prompt) in enumerate(r["calls"]):
            jr = judgment_record("other", f"{r['id']}.repair{rnd + 1}", "pipeline", res, prompt,
                                 json.dumps(res.parsed, ensure_ascii=False)[:2000] if res.ok else "ERROR")
            jr["task"] = "archetype_repair"
            records.append(jr)
        if r["card"] is not None:
            apply_card(by_id[r["id"]], r["card"])
            fixed.append(r["id"])
        else:
            failed.append({"id": r["id"], "errors": r["errors"][:4], "rounds": r["rounds"]})
    from chalkbase.models import ItemArchetype

    for a in archetypes:
        ItemArchetype(**a)
    write_json(DATA_DIR / "archetypes.json", archetypes)
    jpath = JUDGMENTS_DIR / "stage3_repair.jsonl"
    prior = read_jsonl(jpath) if jpath.exists() else []
    seen = {r["id"] for r in prior}
    write_jsonl(jpath, prior + [r for r in records if r["id"] not in seen])
    cost = sum(float(res.cost_cny) for r in results for res, _ in r["calls"])
    return {"todo": len(todo), "fixed": len(fixed), "failed": failed, "cost_cny": round(cost, 2)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["placeholders", "none", "render"])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    print(json.dumps(run_placeholders(limit=args.limit, dry_run=args.dry_run, mode=args.what), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
