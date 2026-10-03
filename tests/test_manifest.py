"""数据清单与随包数据的一致性（不变量）：改了 data/ 却没重新生成清单、或知识点文本变了却没重算向量，都会失败。"""
from __future__ import annotations

import numpy as np
import pytest

from chalkbase import __version__
from chalkbase.common import DATA_DIR, SCHEMA_VERSION, SEQUENCE_PATH, book_sequence, resolve_data_dir
from chalkbase.manifest import DataSchemaError, check_compatible, load_manifest, verify_manifest
from chalkbase.query import Curriculum
from chalkbase.query import embed as E


def test_manifest_matches_data_files():
    problems = verify_manifest(DATA_DIR)
    assert not problems, "data/ 与 manifest.json 不一致，运行 `python scripts/build_manifest.py`：" + "; ".join(problems)


def test_manifest_versions():
    m = load_manifest(DATA_DIR)
    assert m["data_version"] == __version__
    assert m["schema_version"] == SCHEMA_VERSION
    assert m["embedding_model"] == E.MODEL
    assert Curriculum().manifest == m


def test_incompatible_schema_is_rejected():
    with pytest.raises(DataSchemaError, match="不兼容"):
        check_compatible({"schema_version": "999.0", "data_version": "x"})
    with pytest.raises(DataSchemaError):
        check_compatible({})
    check_compatible({"schema_version": SCHEMA_VERSION.split(".")[0] + ".99"})  # minor 不同仍兼容


def test_kp_embeddings_current():
    """随包向量与当前知识点文本逐条对应（文本哈希一致），数量一致。"""
    cur = Curriculum()
    z = np.load(DATA_DIR / E.EMBEDDINGS_FILE, allow_pickle=False)
    assert str(z["model"]) == E.MODEL and z["vectors"].dtype == np.float16 and z["vectors"].shape == (len(cur.knowledge_points), E.DIM)
    by_id = dict(zip(z["kp_ids"].tolist(), z["text_sha256"].tolist()))
    assert set(by_id) == set(cur.knowledge_points)
    stale = [i for i, h in by_id.items() if h != E.text_hash(E.dense_text(cur.kp(i)))]
    assert not stale, f"{len(stale)} 个知识点文本已变，运行 `python scripts/build_embeddings.py`：{stale[:3]}"
    assert np.isfinite(z["vectors"].astype(np.float32)).all()


def test_book_sequence_matches_config():
    yaml = pytest.importorskip("yaml")  # pyyaml 属于 build 依赖，纯运行时环境没有

    assert list(book_sequence()) == yaml.safe_load(SEQUENCE_PATH.read_text(encoding="utf-8"))["books"]


def test_data_dir_resolution(monkeypatch, tmp_path):
    monkeypatch.setenv("CHALKBASE_DATA", str(tmp_path))
    assert resolve_data_dir() == tmp_path
    monkeypatch.delenv("CHALKBASE_DATA")
    assert (resolve_data_dir() / "knowledge_points.json").exists()
