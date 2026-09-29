"""程序校验沙箱：在子进程中执行模型写的求解函数 `solve(**params)`。

- 只暴露 Decimal / Fraction / math 与一小组安全内建函数，禁止 import / open / 属性逃逸式调用；
- 子进程 + 超时，防止死循环；
- 参数按槽位类型解析为 int / Decimal / Fraction（禁止浮点，CLAUDE.md 第 2 节第 6 条）。
"""
from __future__ import annotations

import json
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
    return all(bool(eval(c, {"__builtins__": SAFE_BUILTINS, "Decimal": Decimal, "Fraction": Fraction, "math": math}, dict(args))) for c in chk)

param_sets = req.get("param_sets")
if param_sets is None:  # 生成探针模式：在子进程内采样，满足约束的参数最多尝试 max_tries 次
    pr = req["probe"]; rng = random.Random(pr["seed"]); param_sets = []
    for _ in range(pr["n"]):
        found = None
        for _t in range(pr.get("max_tries", 5000)):
            try:
                cand = sample_params(pr["slots"], rng)
                if check({k: parse(v, req["slot_types"].get(k, "str")) for k, v in cand.items()}):
                    found = cand; break
            except Exception:
                continue
        if found is None:
            print(json.dumps({"ok": False, "error": "constraints_unsatisfiable"})); sys.exit()
        param_sets.append(found)
out = []
for params in param_sets:
    try:
        args = {k: parse(v, req["slot_types"].get(k, "str")) for k, v in params.items()}
        cok = check(args)
        res = solve(**args)
        out.append({"ok": True, "result": norm(res), "constraints_ok": cok, "params": params})
    except Exception as e:
        out.append({"ok": False, "error": f"{type(e).__name__}: {e}"})
print(json.dumps({"ok": True, "results": out}, ensure_ascii=False))
'''


def run_solver(code: str, slot_types: dict[str, str], param_sets: list[dict] | None, constraints: list[str] | None = None,
               timeout: float = 20.0, probe: dict | None = None) -> dict:
    """param_sets 给定时逐组求解；param_sets=None 且给 probe={slots,n,seed} 时在子进程内按槽位约束采样后求解。"""
    req = {"code": code, "slot_types": slot_types, "constraints": constraints or []}
    if param_sets is not None:
        req["param_sets"] = param_sets
    else:
        req["probe"] = probe
    payload = json.dumps(req, ensure_ascii=False)
    try:
        p = subprocess.run([sys.executable, "-I", "-c", RUNNER], input=payload, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    if p.returncode != 0 or not p.stdout.strip():
        return {"ok": False, "error": f"runner crashed: {p.stderr[-300:]}"}
    return json.loads(p.stdout.strip().splitlines()[-1])
