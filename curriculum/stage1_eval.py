"""Stage 1（抽取）的评测指标：覆盖与结构统计，直接由 data/ 与 work/books/ 计算（确定性，无模型调用）。

双盲一致性与 5 页人工核对只在 g4a/g4b 试点期间执行，结果与用户的拍板记录在 docs/decisions.md D7–D9；
全书的结构合法性、引用可解析、课时挂载等由 tests/test_stage1_invariants.py 保证。
"""
from __future__ import annotations

import re

from curriculum.common import DATA_DIR, read_json
from curriculum.metrics import wilson

UNREADABLE = re.compile("未能判读|无法判读|未判读|无法辨认|无法识别")


def metrics(split: str) -> dict:
    books = read_json(DATA_DIR / "books.json")
    lessons = read_json(DATA_DIR / "lessons.json")
    exercises = read_json(DATA_DIR / "exercises.json")
    unreadable = sum(1 for e in exercises if UNREADABLE.search(e.get("text") or ""))
    w = wilson(unreadable, len(exercises))
    return {
        "implemented": True,
        "split": split,
        "headline": {
            "已抽取教材册数": {"value": f"{len(books)}/12", "baseline": None, "threshold": "12/12", "pass": len(books) == 12},
            "课时 / 习题实例数": {"value": f"{len(lessons)} / {len(exercises)}", "baseline": None, "pass": None},
            "含「未能判读」图形信息的实例占比": {"value": w["p"], "ci": [w["lo"], w["hi"]], "n": len(exercises), "baseline": None, "pass": None,
                                             "detail": "图形信息因渲染精度未能判读的实例，按 D9 不做二次高清核实"},
        },
        "report_lines": [
            "",
            "试点（g4a/g4b）的双盲一致性与 5 页人工核对结果、抽取规则的用户拍板见 `docs/decisions.md` D6–D9；"
            "全书不变量见 `tests/test_stage1_invariants.py`（每册结构合法、引用可解析、课时都有知识点挂载或无习题引用）。",
        ],
    }
