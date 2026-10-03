"""Stage 3 · 题型卡片生成与程序校验。

对每个签名组，流水线模型（qwen3.7-plus）写：抽象模板 + 结构化槽位约束 + 约束表达式 + 求解程序（program 类）
+ 求解步骤 + 典型错误 + 2～3 个**新写**的改写示例（含参数）。程序侧校验：
  V1 示例答案 = 求解程序重算结果（program 类，数值等价比较）；
  V2 生成探针：在槽位约束内随机采样 20 组参数，求解程序可运行、非浮点、满足约束表达式；
  V3 槽位约束不超出该组观测包络（整数位数、小数位数）；
  V4 改写示例与源实例原文字符 5-gram 重合率 < 0.5（不得复述教材原题）；
  V5 示例题面包含其数值参数。
不通过则把错误反馈给模型修复，最多 3 轮（第 3 轮开思考模式）。
"""
from __future__ import annotations

import json
import re
import sys
import threading
from decimal import Decimal
from fractions import Fraction
from typing import Any

from chalkbase.annotate.client import AnnotationClient, AnnotationRequest, Progress
from chalkbase.annotate.gold import ModelConfig
from chalkbase.runtime.features import slot_types  # noqa: F401  （兼容旧导入路径）
from chalkbase.runtime.sandbox import run_solver

PIPELINE = ModelConfig("qwen3.7-plus", False, max_tokens=4096)
REPAIR_THINKING = ModelConfig("qwen3.7-plus", True, max_tokens=12000)
N_PROBE = 20
MIN_ACCEPTANCE = 0.01  # 约束接受率下限
CACHE_ONLY = __import__("os").environ.get("STAGE3_CACHE_ONLY") == "1"  # 收尾重放：不发起新调用
MAX_ROUNDS = 6
LATE_HINT = ("\n常见错误提醒：禁止使用 float（小数用 Decimal、分数用 Fraction，Decimal 与 Fraction 已可直接使用，不要 import）；"
             "solve 的参数名必须与 slots 的键完全一致；examples 的 params 必须满足 constraints；answer_value 必须与 solve(**params) 的结果相等。")

SYSTEM = """你是小学数学命题专家，同时会写严谨的 Python。你要把教材里同一类习题归纳成可参数化生成的「题型卡片」。

输出只能是一个 JSON 对象，字段：
- "template"：抽象题干模板，用 {槽位名} 表示可变部分（如 "计算：{a} + {b}"，应用题可写 "{name}买了{n}支笔，每支{price}元……"）。
- "slots"：槽位约束，形如 {"a": {"type": "int", "min": 10, "max": 99}, "b": {"type": "decimal", "min": "0.1", "max": "9.9", "places": 1}, "f": {"type": "fraction", "min": "0", "max": "1", "max_denominator": 10}, "name": {"type": "choice", "options": ["小明", "小红"]}}。
  type 只能是 int / decimal / fraction / choice；choice 的 options 只放简单字符串（不要放 JSON 列表或表格）。数值范围必须落在给出的「教材观测包络」内（整数位数、小数位数不得超过观测最大值）。
- "constraints"：参数之间的约束，Python 布尔表达式列表（可用 Decimal、Fraction、math），如 ["a > b", "(a * b) % 10 == 0"]；没有则 []。
- **保持源实例的题目形式与考查方式**：画图题仍是画图题，选择题必须在题面中写出由槽位生成的全部选项（A/B/C…），判断题给出待判断的命题，说理题仍要求说理。不要为了便于程序校验而改变题目的考查方式。
- **一个题型只写一种题**：源实例若带多个小问，只保留最能代表该题型的一问（至多两问），不要把多种题拼成一道大题。
- "verifiable_type"：按源实例的真实性质判定——"program"：答案由题面数值唯一确定且可比较（计算、填数、判断对错、有明确选项的选择、比较大小、单位换算、数值应用题等）；"rule"：可按规则检查但答案不唯一或是作图/操作结果（画图、分类方案、排列方案、拼摆）；"human"：开放表达（说一说理由、谈想法、调查报告、评价方案）。
- "solver"：verifiable_type 为 program 时必填，Python 源码，定义 def solve(<全部槽位名作为参数>)，返回答案（int / Decimal / Fraction / str / bool，或它们的列表）；**禁止 float、禁止 import**（Decimal、Fraction、math 已可直接使用）。choice 槽位以 str 传入。其他类型填 null。
- "answer_format"：答案形式的简短说明（如「一个整数」「最简分数」「两空：商和余数」）。
- "solution_steps"：求解步骤（抽象描述，2～5 步）。
- "requires_reverse_thinking"：是否需要逆向思考（已知结果求条件、倒推），布尔。
- "typical_errors"：学生典型错误 2～4 条。
- "examples"：2～3 个改写示例，每个 {"params": {槽位: 值}, "problem": 题面, "answer": 答案（给学生看的写法）, "answer_value": 程序可比较的答案（program 类必须与 solve(**params) 的结果数值相等；分数写 "a/b"；多个答案用 JSON 列表）, "solution": 简要解法}。
  params 的值用字符串或整数表示（小数写 "3.45"，分数写 "3/4"）；题面必须由模板代入 params 得到（可略加润色），题面里要出现各数值参数；**必须全新编写，不得复述或改编下面给出的教材原题**（换数、换情境、换说法）。"""

USER_TMPL = """【题型信息】
主知识点：{kp_name}（{kp_desc}）
次知识点：{secondary}
题目形式：{item_form}；答案形式分布：{answer_forms}
适用年级（源实例所在年级）：{grades}
教材观测包络：{envelope}
源实例所用情境：{contexts}

【源实例原文（{n} 条，最多列 6 条，仅供理解题型，不得复述）】
{texts}

请输出题型卡片 JSON。"""


# ------------------------------------------------------------------ 校验工具


def _num(v) -> Fraction | None:
    s = str(v).strip().replace("，", ",")
    try:
        if "/" in s:
            a, b = s.split("/", 1)
            return Fraction(int(a), int(b))
        return Fraction(Decimal(s))
    except Exception:
        return None


def answers_equal(a, b) -> bool:
    if isinstance(a, dict) or isinstance(b, dict):
        if not (isinstance(a, dict) and isinstance(b, dict)) or set(map(str, a)) != set(map(str, b)):
            return False
        bb = {str(k): v for k, v in b.items()}
        return all(answers_equal(v, bb[str(k)]) for k, v in a.items())
    if isinstance(a, bool) or isinstance(b, bool):
        return str(a).strip().lower() == str(b).strip().lower()
    if isinstance(a, list) or isinstance(b, list):
        if isinstance(b, str):
            try:
                b = json.loads(b)
            except json.JSONDecodeError:
                b = [x for x in re.split(r"[,，;；\s]+", b) if x]
        if not isinstance(a, list):
            a = [a]
        if not isinstance(b, list):
            b = [b]
        return len(a) == len(b) and all(answers_equal(x, y) for x, y in zip(a, b))
    na, nb = _num(a), _num(b)
    if na is not None and nb is not None:
        return na == nb
    return str(a).strip().lower() == str(b).strip().lower()


def ngram_overlap(a: str, b: str, n: int = 5) -> float:
    a, b = re.sub(r"\s+", "", a), re.sub(r"\s+", "", b)
    ga = {a[i : i + n] for i in range(len(a) - n + 1)}
    gb = {b[i : i + n] for i in range(len(b) - n + 1)}
    return len(ga & gb) / len(ga) if ga else 0.0


def validate_card(d: Any) -> bool:
    if not isinstance(d, dict):
        return False
    need = ("template", "slots", "verifiable_type", "examples", "solution_steps")
    if any(k not in d for k in need) or d["verifiable_type"] not in ("program", "rule", "human"):
        return False
    if not isinstance(d["slots"], dict) or not isinstance(d["examples"], list) or not (2 <= len(d["examples"]) <= 3):
        return False
    for s in d["slots"].values():
        if not isinstance(s, dict) or s.get("type") not in ("int", "decimal", "fraction", "choice"):
            return False
    if d["verifiable_type"] == "program" and not (isinstance(d.get("solver"), str) and "def solve" in d["solver"]):
        return False
    return all(isinstance(e, dict) and e.get("problem") and "answer" in e for e in d["examples"])


def verify_card(card: dict, env: dict, source_texts: list[str], seed: int) -> list[str]:
    """返回错误列表（空 = 通过）。"""
    errs = []
    slots = card["slots"]
    # V3 包络
    idig = env.get("integer_digits")
    dplc = env.get("decimal_places")
    for k, s in slots.items():
        try:
            if s["type"] == "int" and idig and len(str(abs(int(s["max"])))) > idig[1]:
                errs.append(f"槽位 {k} 上限 {s['max']} 的位数超过教材观测最大整数位数 {idig[1]}")
            if s["type"] == "decimal":
                if dplc and int(s.get("places", 1)) > dplc[1]:
                    errs.append(f"槽位 {k} 小数位数 {s.get('places')} 超过教材观测最大值 {dplc[1]}")
                if idig and len(str(int(abs(Decimal(str(s["max"])))))) > idig[1]:
                    errs.append(f"槽位 {k} 上限 {s['max']} 的整数部分位数超过教材观测最大整数位数 {idig[1]}")
            if s["type"] in ("int", "decimal", "fraction") and _num(s.get("min", 0)) > _num(s.get("max", 0)):
                errs.append(f"槽位 {k} 的 min 大于 max")
            if s["type"] == "choice" and not s.get("options"):
                errs.append(f"choice 槽位 {k} 没有 options")
        except (KeyError, ValueError, TypeError) as e:
            errs.append(f"槽位 {k} 约束不完整：{e}")
    # V4 / V5 / V6
    if len({re.sub(r"\s+", "", ex["problem"]) for ex in card["examples"]}) < len(card["examples"]):
        errs.append("改写示例的题面有重复，每个示例必须是不同的题（换数、换情境或换问法）")
    for i, ex in enumerate(card["examples"]):
        ov = max((ngram_overlap(ex["problem"], t) for t in source_texts), default=0.0)
        if ov >= 0.5:
            errs.append(f"示例{i + 1} 与教材原题 5-gram 重合率 {ov:.2f} ≥ 0.5，疑似复述，请全新编写")
        params = ex.get("params") or {}
        if set(params) != set(slots):
            errs.append(f"示例{i + 1} 的 params 键 {sorted(params)} 与槽位 {sorted(slots)} 不一致")
            continue
        for k, v in params.items():
            if slots[k]["type"] in ("int", "decimal") and str(v) not in ex["problem"] and str(_num(v) if _num(v) is not None and _num(v).denominator == 1 else v) not in ex["problem"]:
                errs.append(f"示例{i + 1} 题面中没有出现参数 {k}={v}")
    if errs:
        return errs
    if card["verifiable_type"] != "program":
        return errs
    # V1 示例重算
    types = slot_types(slots)
    cons = card.get("constraints") or []
    res = run_solver(card["solver"], types, [ex["params"] for ex in card["examples"]], cons)
    if not res["ok"]:
        return [f"求解程序无法编译/运行：{res['error']}"]
    for i, (ex, r) in enumerate(zip(card["examples"], res["results"])):
        if not r["ok"]:
            errs.append(f"示例{i + 1} 运行出错：{r['error']}")
        elif not r["constraints_ok"]:
            errs.append(f"示例{i + 1} 的参数不满足 constraints")
        elif not answers_equal(r["result"], ex.get("answer_value", ex["answer"])):
            errs.append(f"示例{i + 1} 的 answer_value={ex.get('answer_value')!r} 与程序重算结果 {r['result']!r} 不一致")
    if errs:
        return errs
    # V2a 约束接受率：随机取值满足 constraints 的比例过低（< 1%）时，生成探针对随机种子很敏感（换种子就可能采不到）
    if cons:
        ac = run_solver("def solve(**kw):\n    return 0", types, None, cons, acceptance={"slots": slots, "n": 2000, "seed": seed})
        if ac.get("ok") and ac["acceptance"] < MIN_ACCEPTANCE:
            return [f"约束接受率过低：槽位随机取值只有 {ac['acceptance']:.2%} 满足 constraints={cons}。请改为直接采样自由参数，"
                    "把由约束决定的量写成程序计算的结果（例如采样单价与数量、营业额 = 单价 × 数量），或给 int 槽位加 step，"
                    "使随机取值大多数情况下就满足约束"]
    # V2 生成探针：用 3 个不同随机种子各采样 N_PROBE 组，全部通过才算合格（约束接受率过低的模板在这里暴露）
    for sd in (seed, seed + 7919, seed + 104729):
        pr = run_solver(card["solver"], types, None, cons, probe={"slots": slots, "n": N_PROBE, "seed": sd})
        if not pr["ok"]:
            if pr["error"] == "constraints_unsatisfiable":
                return [f"在槽位范围内随机采样 5000 次都无法满足 constraints={cons}，约束过严或与槽位范围矛盾；"
                        "请改用 int 槽位的 step 或直接调整槽位范围，使随机取值大多数情况下就满足约束"]
            return [f"生成探针运行失败：{pr['error']}"]
        bad = [r for r in pr["results"] if not r["ok"] or not r.get("constraints_ok")]
        if bad:
            r = bad[0]
            errs.append(f"生成探针 {len(bad)}/{N_PROBE} 次失败，例如参数 {r.get('params')} → {r.get('error', '不满足约束')}")
            break
    return errs


def probe_card(card: dict, seed: int) -> dict:
    """生成探针（评测用）：在槽位约束内采样 N_PROBE 组参数，返回 {n, ok, error?}。"""
    if card.get("verifiable_type") != "program":
        return {"n": 0, "ok": 0}
    pr = run_solver(card["solver"], slot_types(card["slots"]), None, card.get("constraints") or [],
                    probe={"slots": card["slots"], "n": N_PROBE, "seed": seed})
    if not pr["ok"]:
        return {"n": N_PROBE, "ok": 0, "error": pr["error"]}
    return {"n": N_PROBE, "ok": sum(1 for r in pr["results"] if r["ok"] and r.get("constraints_ok"))}


# ------------------------------------------------------------------ 生成主循环


def generate_cards(jobs: list[dict], client: AnnotationClient | None = None) -> dict[str, dict]:
    """jobs: [{id, user_msg, envelope, source_texts, seed}] → {id: {card, rounds, errors, calls:[AnnotationResult...]}}。

    每张卡片独立推进（生成 → 校验 → 不通过则带着错误进入下一轮），不按轮次整批等待，
    避免单个慢请求拖住整批。每轮提示词与按轮次批处理时逐字相同，已有结果照样命中 .cache/。
    """
    from concurrent.futures import ThreadPoolExecutor

    client = client or AnnotationClient(max_workers=48)
    state = {j["id"]: {"job": j, "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": j["user_msg"]}],
                       "card": None, "errors": ["未生成"], "rounds": 0, "calls": []} for j in jobs}
    prog = Progress("题型卡片（逐张推进，完成=通过或用尽轮次）", len(jobs))
    in_round: dict[int, int] = {}
    lock = threading.Lock()

    def work(s: dict) -> None:
        for rnd in range(MAX_ROUNDS):
            with lock:
                in_round[rnd + 1] = in_round.get(rnd + 1, 0) + 1
            cfg = REPAIR_THINKING if rnd >= 2 else PIPELINE  # 第 3 轮起用思考模式
            req = AnnotationRequest(request_id=s["job"]["id"], model=cfg.model, thinking=cfg.thinking, messages=list(s["messages"]),
                                    response_schema_validator=validate_card, max_tokens=cfg.max_tokens)
            if CACHE_ONLY and not client.is_cached(req):
                if not s["card"]:
                    s["errors"] = ["仅缓存模式：首轮未缓存，未发起新调用"]
                break  # 收尾重放：不发起新调用，保留上一轮的卡片与错误，按 D18 降级
            res = client._call_one(req)
            s["rounds"] = rnd + 1
            s["calls"].append(res)
            if not res.ok:
                s["errors"] = [f"模型输出不合法：{res.error}"]
                continue
            card = res.parsed
            try:
                errs = verify_card(card, s["job"]["envelope"], s["job"]["source_texts"], s["job"]["seed"])
            except Exception as e:  # 约束字段格式异常等
                errs = [f"校验时异常：{type(e).__name__}: {e}"]
            s["card"], s["errors"] = card, errs
            if not errs:
                break
            s["messages"] = s["messages"] + [
                {"role": "assistant", "content": json.dumps(card, ensure_ascii=False)},
                {"role": "user", "content": "程序校验没有通过，请修正后输出完整的新 JSON：\n- " + "\n- ".join(errs[:8]) + (LATE_HINT if rnd >= 2 else "")},
            ]
        prog.update(failed=bool(s["errors"]))

    with ThreadPoolExecutor(max_workers=client.max_workers) as ex:
        list(ex.map(work, state.values()))
    prog.finish()
    print(f"[进度] 题型卡片各轮进入数：{dict(sorted(in_round.items()))}", file=sys.stderr, flush=True)
    return state
