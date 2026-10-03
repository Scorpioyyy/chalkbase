"""不变量测试（CLAUDE.md 4.2 节的硬门槛），必须 100% 通过。

Stage 0 阶段覆盖：目录整洁、书/单元注册表的 schema 合法性与 ID/页码约定。
后续 Stage 完成后在本文件追加对应的不变量（knowledge point 图无环、能力边界单调等）。
"""
import json
import re
from pathlib import Path

import pytest

from chalkbase.models import Book, Unit

ROOT = Path(__file__).resolve().parent.parent
WORK_BOOKS_DIR = ROOT / "work" / "books"

BOOK_ID_RE = re.compile(r"^g[1-6][ab]$")
UNIT_ID_RE = re.compile(r"^g[1-6][ab]\.u\d+$")

EXPECTED_TOP_LEVEL = {
    "textbook", "CLAUDE.md", "KICKOFF.md", "README.md", "LICENSE",
    "environment.yml", "pyproject.toml",
    "config", "docs", "chalkbase", "scripts", "schema", "work", "data",
    "eval", "tests", "reports", "viz", ".cache", "tmp",
    ".gitignore", ".git",
}


def _all_registries() -> list[Path]:
    if not WORK_BOOKS_DIR.exists():
        return []
    return sorted(WORK_BOOKS_DIR.glob("*/registry.json"))


IGNORED_TOP_LEVEL_SUFFIXES = (".egg-info",)
IGNORED_TOP_LEVEL_NAMES = {".pytest_cache", ".claude"}


def test_directory_top_level_is_clean():
    actual = {
        p.name for p in ROOT.iterdir()
        if not p.name.endswith(IGNORED_TOP_LEVEL_SUFFIXES) and p.name not in IGNORED_TOP_LEVEL_NAMES
    }
    unexpected = actual - EXPECTED_TOP_LEVEL
    assert not unexpected, f"顶层出现未声明的条目，需归类或删除：{unexpected}"


def test_tmp_is_empty_at_commit_time():
    tmp_dir = ROOT / "tmp"
    if not tmp_dir.exists():
        return
    leftovers = [p for p in tmp_dir.iterdir() if p.name != ".gitkeep"]
    assert not leftovers, f"tmp/ 提交前必须清空，当前残留：{[p.name for p in leftovers]}"


@pytest.mark.parametrize("registry_path", _all_registries(), ids=lambda p: p.parent.name)
def test_registry_matches_schema(registry_path: Path):
    data = json.loads(registry_path.read_text(encoding="utf-8"))
    book = Book(**data["book"])
    units = [Unit(**u) for u in data["units"]]

    assert BOOK_ID_RE.match(book.id), f"book id 不符合约定：{book.id}"
    assert book.id == registry_path.parent.name, "book id 与所在目录名不一致"

    for unit in units:
        assert UNIT_ID_RE.match(unit.id), f"unit id 不符合约定：{unit.id}"
        assert unit.id.startswith(book.id + "."), f"unit id 未挂在正确的 book 下：{unit.id}"
        assert unit.pages.pdf_start <= unit.pages.pdf_end, f"{unit.id} 页码范围颠倒"
        assert unit.pages.pdf_end <= book.pdf_page_count, f"{unit.id} 超出全书页数"

    assert set(book.unit_ids) == {u.id for u in units}, "book.unit_ids 与 units 列表不一致"


@pytest.mark.parametrize("registry_path", _all_registries(), ids=lambda p: p.parent.name)
def test_units_ordered_and_non_overlapping(registry_path: Path):
    data = json.loads(registry_path.read_text(encoding="utf-8"))
    units = sorted((Unit(**u) for u in data["units"]), key=lambda u: u.index)

    for prev, cur in zip(units, units[1:]):
        assert prev.index < cur.index, f"单元 index 未严格递增：{prev.id} -> {cur.id}"
        assert prev.pages.pdf_end < cur.pages.pdf_start, (
            f"单元页码范围重叠或乱序：{prev.id}({prev.pages.pdf_start}-{prev.pages.pdf_end}) "
            f"vs {cur.id}({cur.pages.pdf_start}-{cur.pages.pdf_end})"
        )
