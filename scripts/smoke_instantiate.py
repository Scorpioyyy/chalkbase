"""全量题型实例化 smoke：对每张题型卡片用 5 个种子实例化。

检查项（program 类）：5 个种子都能实例化、答案非空、没有 warnings、题面不含未替换的占位符；
卡片示例的参数经运行时重新求解后，答案与示例的 answer_value 一致。rule / human 类只检查能渲染出题面且 answer 为 None。
有失败时列出清单并以非零状态退出。

用法：python scripts/smoke_instantiate.py [--seeds 5] [--workers 8] [--limit N]
"""
from __future__ import annotations

import argparse
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chalkbase import Curriculum  # noqa: E402
from chalkbase.runtime import run_solver, slot_types  # noqa: E402
from chalkbase.stage3.generate import answers_equal  # noqa: E402

LEFTOVER = re.compile(r"\{[^{}\d][^{}]*\}")
LATEX = re.compile(r"\$[^$]*\$|[\\][a-zA-Z]+(\{[^{}]*\})+")


def leftover(text: str):
    """题面里没被替换的 {占位符}：LaTeX 公式（美元符号括起的部分、反斜杠命令的花括号参数）与纯数字花括号不算。"""
    return LEFTOVER.search(LATEX.sub("", text))


def check(cur: Curriculum, a, n_seeds: int) -> list[str]:
    errs: list[str] = []
    try:
        ps = cur.instantiate_many(a.id, n_seeds, 0, unique=False)
    except Exception as e:  # noqa: BLE001
        return [f"{type(e).__name__}: {str(e)[:150]}"]
    if len(ps) != n_seeds:
        errs.append(f"只实例化出 {len(ps)}/{n_seeds} 道")
    for p in ps:
        if a.verifiable_type.value == "program" and p.answer is None:
            errs.append(f"seed={p.seed}: 答案为空")
        if a.verifiable_type.value != "program" and p.answer is not None:
            errs.append(f"seed={p.seed}: 非 program 类不应有答案")
        errs += [f"seed={p.seed}: {w}" for w in p.warnings]
        if a.verifiable_type.value == "program" and leftover(p.problem):
            errs.append(f"seed={p.seed}: 题面含未替换的花括号：{leftover(p.problem).group(0)}")
        if p.answer and leftover(p.answer):
            errs.append(f"seed={p.seed}: 答案含未替换的花括号：{leftover(p.answer).group(0)}")
    if a.verifiable_type.value == "program":
        pc = a.parameter_constraints
        r = run_solver(a.solver_program, slot_types(pc["slots"]), [e.params for e in a.rewritten_examples], pc.get("constraints"))
        if not r["ok"]:
            errs.append(f"示例重算失败：{r['error']}")
        else:
            for i, (e, x) in enumerate(zip(a.rewritten_examples, r["results"])):
                if not x["ok"] or not answers_equal(x["result"], e.answer_value if e.answer_value is not None else e.answer):
                    errs.append(f"示例{i + 1}: 重算 {x.get('result', x.get('error'))!r} != 卡片 {e.answer_value!r}")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()
    cur = Curriculum()
    arch = sorted(cur.archetypes_by_id.values(), key=lambda a: a.id)[: args.limit]
    with ThreadPoolExecutor(args.workers) as ex:
        results = list(ex.map(lambda a: check(cur, a, args.seeds), arch))
    for vt in ("program", "rule", "human"):
        rows = [(a, e) for a, e in zip(arch, results) if a.verifiable_type.value == vt]
        print(f"{vt}: {len(rows)} 张 × {args.seeds} 种子，失败 {sum(1 for _, e in rows if e)} 张")
    bad = [(a, e) for a, e in zip(arch, results) if e]
    for a, e in bad:
        print(f"  {a.id}: {e[0]}" + (f"（另 {len(e) - 1} 条）" if len(e) > 1 else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
