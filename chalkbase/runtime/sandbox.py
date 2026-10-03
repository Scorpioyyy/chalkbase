"""程序校验沙箱：在子进程中执行题型卡片里的求解函数 `solve(**params)`，并在同一子进程内按槽位约束采样参数。

题型实例化（`chalkbase.runtime.instantiate`）、Stage 3 卡片校验与 Stage 6 生成探针共用这一份实现。

- 只暴露 Decimal / Fraction / math 与一小组安全内建函数，禁止 import / open / 属性逃逸式调用；
- 子进程 + 超时，防止死循环；
- 参数按槽位类型解析为 int / Decimal / Fraction（禁止浮点，CLAUDE.md 第 2 节第 6 条）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

SAMPLER = r'''
def sample_params(slots, rng):
    out = {}
    for k, s in slots.items():
        t = s.get("type")
        if t == "int":
            step = int(s.get("step", 1) or 1)
            out[k] = int(s["min"]) + step * rng.randint(0, (int(s["max"]) - int(s["min"])) // step)
        elif t == "decimal":
            p = int(s.get("places", 1)); scale = 10 ** p
            lo, hi = int(Decimal(str(s["min"])) * scale), int(Decimal(str(s["max"])) * scale)
            out[k] = str(Decimal(rng.randint(lo, hi)) / scale)
        elif t == "fraction":
            lo, hi = Fraction(str(s.get("min", "0"))), Fraction(str(s.get("max", "1")))
            md = int(s.get("max_denominator", 10))
            f = lo
            for _ in range(200):
                d = rng.randint(2, max(2, md)); n = rng.randint(int(lo * d), int(hi * d)); f = Fraction(n, d)
                if lo <= f <= hi:
                    break
            out[k] = f"{f.numerator}/{f.denominator}"
        elif t == "choice":
            out[k] = rng.choice(s["options"])
        else:
            raise ValueError(f"未知槽位类型 {t}")
    return out
'''

RUNNER = SAMPLER + r'''
import json, sys, math, ast, random
from decimal import Decimal, ROUND_HALF_UP, getcontext
from fractions import Fraction
getcontext().prec = 50
SAFE_BUILTINS = {n: __builtins__[n] if isinstance(__builtins__, dict) else getattr(__builtins__, n) for n in
    ["abs","min","max","sum","range","len","int","str","bool","round","divmod","sorted","list","tuple","dict","set",
     "enumerate","zip","all","any","map","filter","isinstance","pow","ValueError","ZeroDivisionError","Exception","reversed"]}
FORBIDDEN = ("import", "__", "open(", "eval(", "exec(", "compile(", "globals", "locals", "getattr", "setattr", "input(")

def parse(v, t):
    if t == "int": return int(v)
    if t == "decimal": return Decimal(str(v))
    if t == "fraction": return Fraction(str(v))
    return v

def norm(x):
    if isinstance(x, bool): return str(x)
    if isinstance(x, (int, Decimal, Fraction)):
        if isinstance(x, Fraction):
            return str(x.numerator) if x.denominator == 1 else f"{x.numerator}/{x.denominator}"
        if isinstance(x, Decimal):
            x = x.normalize()
            s = format(x, "f")
            return s
        return str(x)
    if isinstance(x, float):
        raise TypeError("solver returned float (禁止浮点)")
    if isinstance(x, (list, tuple)):
        return [norm(i) for i in x]
    if isinstance(x, dict):
        return {str(k): norm(v) for k, v in x.items()}
    return str(x)

def tag(x):
    """带类型标签的结果编码，调用方据此还原 int / Decimal / Fraction / bool / str / list / dict。"""
    if x is None: return ["none", None]
    if isinstance(x, bool): return ["bool", x]
    if isinstance(x, int): return ["int", str(x)]
    if isinstance(x, Fraction): return ["frac", f"{x.numerator}/{x.denominator}"]
    if isinstance(x, Decimal): return ["dec", format(x.normalize(), "f")]
    if isinstance(x, (list, tuple)): return ["list", [tag(i) for i in x]]
    if isinstance(x, dict): return ["dict", {str(k): tag(v) for k, v in x.items()}]
    return ["str", str(x)]

req = json.loads(sys.stdin.read())
code = req["code"]
# 无害导入（所需名字已在命名空间中提供）直接剔除，其余 import 仍禁止
import re as _re
code = _re.sub(r"(?m)^[ \t]*(from[ \t]+(fractions|decimal|math)[ \t]+import[^\n]*|import[ \t]+(math|fractions|decimal)[ \t]*)$", "", code)
bad = [f for f in FORBIDDEN if f in code]
if bad:
    print(json.dumps({"ok": False, "error": f"forbidden tokens {bad}"})); sys.exit()
from decimal import ROUND_DOWN, ROUND_FLOOR, ROUND_CEILING
ns = {"__builtins__": SAFE_BUILTINS, "Decimal": Decimal, "Fraction": Fraction, "math": math, "ROUND_HALF_UP": ROUND_HALF_UP,
      "ROUND_DOWN": ROUND_DOWN, "ROUND_FLOOR": ROUND_FLOOR, "ROUND_CEILING": ROUND_CEILING}
try:
    exec(compile(code, "<solver>", "exec"), ns)
    solve = ns["solve"]
except Exception as e:
    print(json.dumps({"ok": False, "error": f"compile: {type(e).__name__}: {e}"})); sys.exit()
def check(args):
    chk = req.get("constraints") or []
    # 参数与内建名放进同一个全局命名空间：Python 3.11 中约束里的推导式（如 any([... for c in [c1, c2]])）看不到 eval 的 locals
    env = {"__builtins__": SAFE_BUILTINS, "Decimal": Decimal, "Fraction": Fraction, "math": math, **args}
    return all(bool(eval(c, env)) for c in chk)

param_sets = req.get("param_sets")
if req.get("acceptance"):  # 约束接受率估计：随机采样 n 组，统计满足约束的比例
    ac = req["acceptance"]; rng = random.Random(ac["seed"]); hit = 0
    for _t in range(ac["n"]):
        try:
            cand = sample_params(ac["slots"], rng)
            hit += bool(check({k: parse(v, req["slot_types"].get(k, "str")) for k, v in cand.items()}))
        except Exception:
            pass
    print(json.dumps({"ok": True, "acceptance": hit / ac["n"]})); sys.exit()
def draw(slots, rng, max_tries):
    """在槽位范围内采样，返回第一组满足 constraints 的参数；max_tries 次内都不满足则返回 None。"""
    for _t in range(max_tries):
        try:
            cand = sample_params(slots, rng)
            if check({k: parse(v, req["slot_types"].get(k, "str")) for k, v in cand.items()}):
                return cand
        except Exception:
            continue
    return None

def solve_one(params):
    try:
        args = {k: parse(v, req["slot_types"].get(k, "str")) for k, v in params.items()}
        cok = check(args)
        res = solve(**args)
        return {"ok": True, "result": norm(res), "typed": tag(res), "constraints_ok": cok, "params": params}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "params": params}

sm = req.get("sample")
if sm:  # 按种子采样模式：每个种子一条独立随机流，取其前 per_seed 组满足约束的参数并求解
    seeds_out = []
    for sd in sm["seeds"]:
        rng = random.Random(sd); cands = []
        for _i in range(sm.get("per_seed", 1)):
            found = draw(sm["slots"], rng, sm.get("max_tries", 5000))
            if found is None:
                cands.append({"ok": False, "error": "constraints_unsatisfiable"}); break
            cands.append(solve_one(found))
        seeds_out.append({"seed": sd, "candidates": cands})
    print(json.dumps({"ok": True, "samples": seeds_out}, ensure_ascii=False)); sys.exit()
if param_sets is None:  # 生成探针模式：在子进程内采样，满足约束的参数最多尝试 max_tries 次
    pr = req["probe"]; rng = random.Random(pr["seed"]); param_sets = []
    for _ in range(pr["n"]):
        found = draw(pr["slots"], rng, pr.get("max_tries", 5000))
        if found is None:
            print(json.dumps({"ok": False, "error": "constraints_unsatisfiable"})); sys.exit()
        param_sets.append(found)
out = [solve_one(params) for params in param_sets]
print(json.dumps({"ok": True, "results": out}, ensure_ascii=False))
'''


def run_solver(code: str, slot_types: dict[str, str], param_sets: list[dict] | None, constraints: list[str] | None = None,
               timeout: float = 20.0, probe: dict | None = None, acceptance: dict | None = None, sample: dict | None = None) -> dict:
    """子进程执行求解函数。四种模式（互斥，按此优先级）：
    acceptance={slots,n,seed}：估计槽位随机取值满足约束的比例；
    sample={slots,seeds,per_seed,max_tries?}：每个种子一条随机流，采样满足约束的参数并求解，结果在 `samples`；
    param_sets：对给定参数逐组求解；
    probe={slots,n,seed}：单条随机流采样 n 组并求解（Stage 3 / Stage 6 探针）。"""
    req = {"code": code, "slot_types": slot_types, "constraints": constraints or []}
    if acceptance is not None:
        req["acceptance"] = acceptance
    elif sample is not None:
        req["sample"] = sample
    elif param_sets is not None:
        req["param_sets"] = param_sets
    else:
        req["probe"] = probe
    payload = json.dumps(req, ensure_ascii=False).encode("utf-8")
    try:
        # 全程 UTF-8 字节收发：Windows 默认 gbk，题面含 ✓ 等字符时文本模式会崩溃（-I 隔离模式忽略 PYTHON* 环境变量，所以同时用 -X utf8）
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        p = subprocess.run([sys.executable, "-X", "utf8", "-I", "-c", RUNNER], input=payload, capture_output=True, env=env,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    out = p.stdout.decode("utf-8", "replace").strip()
    if p.returncode != 0 or not out:
        return {"ok": False, "error": f"runner crashed: {p.stderr.decode('utf-8', 'replace')[-300:]}"}
    return json.loads(out.splitlines()[-1])
