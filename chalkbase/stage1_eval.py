"""Stage 1（抽取）的评测指标：覆盖与结构统计，直接由 data/ 与 work/books/ 计算（确定性，无模型调用）。

双盲一致性与 5 页对照核对只在 g4a/g4b 上执行（用于抽取指南定稿，规则见 docs/design.md D7–D9）；
全书的结构合法性、引用可解析、课时挂载等由 tests/test_stage1_invariants.py 保证。
"""
from __future__ import annotations

import re

from chalkbase.common import DATA_DIR, read_json
from chalkbase.metrics import wilson

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
                                             "detail": "图形信息因渲染精度未能判读的实例，按 D9 不做二次渲染核实"},
        },
        "report_lines": [
            "",
            "抽取指南在 g4a/g4b 上经双盲一致性与 5 页对照核对后定稿，规则见 `docs/design.md` D6–D9；"
            "全书不变量见 `tests/test_stage1_invariants.py`（每册结构合法、引用可解析、课时都有知识点挂载或无习题引用）。",
        ],
    }
