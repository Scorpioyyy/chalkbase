"""Stage 1 不变量测试（eval/specs/stage1.md §3）。

只对已经产出 lessons.json/knowledge_points.json/exercises.json 的书生效；
尚未跑 Stage 1 的书自动跳过，不影响 Stage 0 阶段的整体测试通过。
"""
import json
from pathlib import Path

import pytest

from curriculum.models import ExerciseInstance, KnowledgePoint, Lesson

ROOT = Path(__file__).resolve().parent.parent
WORK_BOOKS_DIR = ROOT / "work" / "books"


def _extracted_book_ids() -> list[str]:
    if not WORK_BOOKS_DIR.exists():
        return []
    return sorted(
        p.parent.name for p in WORK_BOOKS_DIR.glob("*/lessons.json")
        if p.exists() and p.stat().st_size > 2
    )


def _load(book_id: str, name: str) -> list[dict]:
    path = WORK_BOOKS_DIR / book_id / name
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("book_id", _extracted_book_ids())
def test_lessons_valid_and_have_intro_kp(book_id: str):
    lessons = [Lesson(**d) for d in _load(book_id, "lessons.json")]
    assert lessons, f"{book_id} lessons.json 为空"
    for lesson in lessons:
        assert lesson.intro_knowledge_point_ids or lesson.practice_knowledge_point_ids, (
            f"{lesson.id} 既未引入也未练习任何知识点"
        )


@pytest.mark.parametrize("book_id", _extracted_book_ids())
def test_knowledge_points_valid(book_id: str):
    kps = [KnowledgePoint(**d) for d in _load(book_id, "knowledge_points.json")]
    assert kps, f"{book_id} knowledge_points.json 为空"


@pytest.mark.parametrize("book_id", _extracted_book_ids())
def test_exercises_reference_existing_lessons_and_kps(book_id: str):
    lessons = [Lesson(**d) for d in _load(book_id, "lessons.json")]
    kps = [KnowledgePoint(**d) for d in _load(book_id, "knowledge_points.json")]
    exercises = [ExerciseInstance(**d) for d in _load(book_id, "exercises.json")]
    assert exercises, f"{book_id} exercises.json 为空"

    lesson_ids = {l.id for l in lessons}
    kp_ids = {k.id for k in kps}

    for ex in exercises:
        assert ex.lesson_id in lesson_ids, f"{ex.id} 引用了不存在的 lesson_id={ex.lesson_id}"
        assert ex.primary_knowledge_point_id in kp_ids, (
            f"{ex.id} 引用了不存在的 primary_knowledge_point_id={ex.primary_knowledge_point_id}"
        )
        for sec in ex.secondary_knowledge_point_ids:
            assert sec in kp_ids, f"{ex.id} 引用了不存在的次知识点 id={sec}"


@pytest.mark.parametrize("book_id", _extracted_book_ids())
def test_unit_lesson_ids_consistent_with_lessons(book_id: str):
    registry = json.loads((WORK_BOOKS_DIR / book_id / "registry.json").read_text(encoding="utf-8"))
    lessons = _load(book_id, "lessons.json")
    lesson_ids_by_unit: dict[str, set[str]] = {}
    for l in lessons:
        lesson_ids_by_unit.setdefault(l["unit_id"], set()).add(l["id"])

    for unit in registry["units"]:
        declared = set(unit.get("lesson_ids", []))
        actual = lesson_ids_by_unit.get(unit["id"], set())
        assert declared == actual, (
            f"{unit['id']} 的 lesson_ids 与 lessons.json 不一致：声明={declared} 实际={actual}"
        )
