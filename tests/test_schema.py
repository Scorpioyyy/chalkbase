"""schema/ 下导出的 JSON Schema 必须与 chalkbase.models 当前定义一致（改了模型忘记重新导出会失败）。"""
import json
from pathlib import Path

import pytest

from chalkbase import models

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schema"


@pytest.mark.parametrize("path", sorted(SCHEMA_DIR.glob("*.schema.json")), ids=lambda p: p.name)
def test_schema_is_current(path: Path):
    name = path.name.removesuffix(".schema.json")
    assert json.loads(path.read_text(encoding="utf-8")) == getattr(models, name).model_json_schema(), (
        f"{path.name} 已过期，请运行 python scripts/export_schema.py"
    )
