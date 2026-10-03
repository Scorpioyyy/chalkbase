"""生成 data/manifest.json（数据版本、schema 版本、各文件条目数与 sha256、构建日期）。

任何 data/ 文件变动后运行：python scripts/build_manifest.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chalkbase.common import ROOT  # noqa: E402
from chalkbase.manifest import MANIFEST_NAME, build_manifest  # noqa: E402


def main() -> None:
    data_dir = ROOT / "data"
    m = build_manifest(data_dir)
    (data_dir / MANIFEST_NAME).write_text(json.dumps(m, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{data_dir / MANIFEST_NAME}: {len(m['files'])} 个文件，数据版本 {m['data_version']}，schema {m['schema_version']}")


if __name__ == "__main__":
    main()
