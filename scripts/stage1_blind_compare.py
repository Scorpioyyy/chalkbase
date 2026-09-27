"""Stage 1 双盲一致性对比：打印 blindA / blindB 的知识点与实例概览，供人工/LLM 判读语义一致性。

不做自动模糊匹配（知识点命名是否语义等价需要语义判断，不适合用字符串规则硬编码），
只做确定性的对齐与呈现：按 pdf_page 排序后的知识点列表、实例列表（含关键字段），
供结合 §抽取指南 目测比对。Jaccard/字段一致率由比对者（人或模型）读完后给出，写入 progress 或报告。

用法：python scripts/stage1_blind_compare.py <book_id>
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(path: Path):
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def summarize_kps(kps):
    return [f"  - {k['id']}: {k['name']} (thread={k['thread']}, grants={k.get('grants')})" for k in kps]


def summarize_exercises(exs):
    exs_sorted = sorted(exs, key=lambda e: (e["pdf_page"], e["id"]))
    return [
        f"  - p{e['pdf_page']} [{e['item_form']}/{e['answer_form']}] primary={e['primary_knowledge_point_id']} "
        f"fig={e['requires_figure']} :: {e['summary']}"
        for e in exs_sorted
    ]


def main():
    book_id = sys.argv[1]
    base = ROOT / "work" / "books" / book_id / "blind_check"
    for label in ["blindA", "blindB"]:
        d = base / label
        kps = load(d / "knowledge_points.json")
        exs = load(d / "exercises.json")
        lessons = load(d / "lessons.json")
        print(f"===== {book_id} / {label} =====")
        print(f"lessons={len(lessons)} knowledge_points={len(kps)} exercises={len(exs)}")
        print("-- knowledge points --")
        print("\n".join(summarize_kps(kps)))
        print("-- exercises (sorted by page) --")
        print("\n".join(summarize_exercises(exs)))
        print()


if __name__ == "__main__":
    main()
