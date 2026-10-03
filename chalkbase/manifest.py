"""数据清单 `data/manifest.json`：数据版本、schema 版本、各文件的条目数与 sha256、构建日期。

由 `scripts/build_manifest.py` 生成；`tests/test_manifest.py` 校验清单与文件一致，数据变动后忘记重生成清单会失败。
"""
from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Optional

from chalkbase import __version__
from chalkbase.common import SCHEMA_VERSION, read_json

MANIFEST_NAME = "manifest.json"


class DataSchemaError(RuntimeError):
    """数据的 schema 版本与代码不兼容。"""


def _content(path: Path) -> bytes:
    """文件内容；文本文件把 CRLF 规整为 LF（Windows 上 git autocrlf 检出的行尾不同，不应改变哈希）。"""
    b = path.read_bytes()
    return b.replace(b"\r\n", b"\n") if path.suffix == ".json" else b


def _records(path: Path) -> int:
    if path.suffix == ".npz":
        import numpy as np

        return int(np.load(path, allow_pickle=False)["vectors"].shape[0])
    obj = read_json(path)
    return len(obj)  # 列表为条目数；字典为顶层键数


def data_files(data_dir: Path) -> list[Path]:
    return sorted(p for p in Path(data_dir).iterdir() if p.is_file() and p.name != MANIFEST_NAME)


def file_entries(data_dir: Path) -> dict[str, dict]:
    out = {}
    for p in data_files(data_dir):
        b = _content(p)
        out[p.name] = {"records": _records(p), "bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()}
    return out


def build_manifest(data_dir: Path) -> dict:
    """生成清单。文件内容都没变时沿用旧清单的 built_at，避免无意义的版本控制噪声。"""
    from chalkbase.query.embed import MODEL

    data_dir = Path(data_dir)
    files = file_entries(data_dir)
    old = load_manifest(data_dir)
    built_at = old["built_at"] if old and old.get("files") == files and old.get("data_version") == __version__ else date.today().isoformat()
    return {"data_version": __version__, "schema_version": SCHEMA_VERSION, "embedding_model": MODEL, "built_at": built_at, "files": files}


def load_manifest(data_dir: Path) -> Optional[dict]:
    p = Path(data_dir) / MANIFEST_NAME
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def check_compatible(manifest: dict) -> None:
    """schema 的 major 版本必须一致，否则抛 `DataSchemaError`。"""
    have = str(manifest.get("schema_version", ""))
    if have.split(".")[0] != SCHEMA_VERSION.split(".")[0]:
        raise DataSchemaError(
            f"数据的 schema 版本 {have or '（缺失）'} 与代码支持的 {SCHEMA_VERSION}（major 须相同）不兼容；"
            f"数据版本 {manifest.get('data_version')}，代码版本 {__version__}。请安装与数据匹配的 chalkbase 版本。")


def verify_manifest(data_dir: Path) -> list[str]:
    """返回清单与实际文件不一致之处（空列表 = 一致）。"""
    m = load_manifest(data_dir)
    if m is None:
        return ["manifest.json 不存在"]
    problems = []
    actual = file_entries(data_dir)
    listed = m.get("files", {})
    for name in sorted(set(actual) | set(listed)):
        if name not in listed:
            problems.append(f"{name}: 文件存在但未列入清单")
        elif name not in actual:
            problems.append(f"{name}: 清单中有但文件不存在")
        elif actual[name] != listed[name]:
            diff = [k for k in ("sha256", "records", "bytes") if actual[name][k] != listed[name].get(k)]
            problems.append(f"{name}: 与清单不一致（{'、'.join(diff)}）")
    return problems
