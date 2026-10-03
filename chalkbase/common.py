"""公共加载与排序工具：读取 Stage 1 观测（work/books/）、教学序列（config/sequence.yaml）与规范数据（data/）。

注意：`work/books/` 是 Stage 1 的原始产物与溯源依据，只有 Stage 2（实体消解）与 Stage 3 的
情境/术语汇总直接读它；其余下游一律读 `data/`。
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
# data/ 的 schema 版本（major.minor）。字段含义或结构不兼容地变化时 major 加一；只新增可选字段时 minor 加一。
# 代码只接受 major 相同的数据（`chalkbase.manifest.check_compatible`）。
SCHEMA_VERSION = "1.0"
WORK_BOOKS_DIR = ROOT / "work" / "books"


def resolve_data_dir() -> Path:
    """规范数据目录。优先级：环境变量 CHALKBASE_DATA > 包内 `chalkbase/data`（wheel 安装态）> 仓库根 `data/`（开发态）。"""
    env = os.environ.get("CHALKBASE_DATA")
    if env:
        return Path(env)
    packaged = Path(__file__).resolve().parent / "data"
    if (packaged / "manifest.json").exists():
        return packaged
    return ROOT / "data"


DATA_DIR = resolve_data_dir()
JUDGMENTS_DIR = ROOT / "work" / "judgments"   # 每次模型判定的结构化记录（输入、结论、置信度、理由）
STAGE5_DIR = ROOT / "work" / "stage5"        # 版本对齐的修复日志与待生成卡片清单
EVAL_DIR = ROOT / "eval"
SEQUENCE_PATH = ROOT / "config" / "sequence.yaml"


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=_json_default) + "\n", encoding="utf-8")


def read_jsonl(path: Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, default=_json_default) + "\n")


def _json_default(o):
    from decimal import Decimal
    from fractions import Fraction

    if isinstance(o, (Decimal, Fraction)):
        return str(o)
    if isinstance(o, set):
        return sorted(o)
    if hasattr(o, "model_dump"):
        return o.model_dump(mode="json")
    raise TypeError(f"not JSON serializable: {type(o)}")


@lru_cache(maxsize=1)
def book_sequence() -> tuple[str, ...]:
    """教学序列中的书 ID 顺序。运行时读 `data/books.json` 的排列顺序（它由 config/sequence.yaml 生成，一致性有不变量测试）；
    数据尚未生成时（流水线早期）回退到 config/sequence.yaml。"""
    books = DATA_DIR / "books.json"
    if books.exists():
        return tuple(b["id"] for b in (r["book"] for r in read_json(books)))
    import yaml  # 仅流水线需要，运行时依赖不含 pyyaml

    return tuple(yaml.safe_load(SEQUENCE_PATH.read_text(encoding="utf-8"))["books"])


def book_index(book_id: str) -> int:
    return book_sequence().index(book_id)


def book_of(some_id: str) -> str:
    """从课时/单元/实例 ID 中取书 ID：g4a.u1.l03 → g4a；ex.g4a.u1.l03.02 → g4a。"""
    parts = some_id.split(".")
    return parts[1] if parts[0] == "ex" else parts[0]


def grade_of(book_id: str) -> int:
    return int(book_id[1])


# ---------------------------------------------------------------- Stage 1 观测


@lru_cache(maxsize=1)
def load_work_books() -> dict[str, dict[str, Any]]:
    """{book_id: {registry, lessons, knowledge_points, exercises, contexts, glossary}}，按教学序列排序。"""
    out = {}
    for bid in book_sequence():
        d = WORK_BOOKS_DIR / bid
        out[bid] = {
            "registry": read_json(d / "registry.json"),
            "lessons": read_json(d / "lessons.json"),
            "knowledge_points": read_json(d / "knowledge_points.json"),
            "exercises": read_json(d / "exercises.json"),
            "contexts": read_json(d / "contexts_observed.json"),
            "glossary": read_json(d / "glossary_observed.json"),
        }
    return out


@lru_cache(maxsize=1)
def lesson_order() -> dict[str, int]:
    """课时 ID → 全序位置（按教学序列、单元 index、课时 index）。"""
    rows = []
    for bid, b in load_work_books().items():
        unit_index = {u["id"]: u["index"] for u in b["registry"]["units"]}
        for l in b["lessons"]:
            rows.append((book_index(bid), unit_index[l["unit_id"]], l["index"], l["id"]))
    rows.sort()
    return {lid: i for i, (*_, lid) in enumerate(rows)}


def local_key(book_id: str, local_id: str) -> str:
    """局部知识点的全局唯一键。局部 ID 跨书存在重名（21 个），必须带书 ID。"""
    return f"{book_id}::{local_id}"


@lru_cache(maxsize=1)
def local_kps() -> list[dict]:
    """全部局部知识点，附加 `_key`（书::局部ID）与 `_book`，按教学序列与引入课时排序。"""
    order = lesson_order()
    rows = []
    for bid, b in load_work_books().items():
        for k in b["knowledge_points"]:
            r = dict(k)
            r["_book"] = bid
            r["_key"] = local_key(bid, k["id"])
            rows.append(r)
    rows.sort(key=lambda r: (order.get(r["first_introduced_lesson_id"], 10**6), r["_key"]))
    return rows
