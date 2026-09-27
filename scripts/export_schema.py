"""导出 curriculum.models 中全部 pydantic 模型的 JSON Schema 到 schema/。

用法：python scripts/export_schema.py
"""
import json
from pathlib import Path

from curriculum import models

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schema"

TOP_LEVEL_MODELS = [
    "Book",
    "Unit",
    "Lesson",
    "KnowledgePoint",
    "ExerciseInstance",
    "ItemArchetype",
    "Edge",
    "Judgment",
    "CapabilityBoundary",
    "Context",
    "GlossaryEntry",
]


def main() -> None:
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    for name in TOP_LEVEL_MODELS:
        model_cls = getattr(models, name)
        schema = model_cls.model_json_schema()
        out_path = SCHEMA_DIR / f"{name}.schema.json"
        out_path.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
