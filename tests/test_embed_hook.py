"""查询向量的注入点：可注入实现、网络请求在锁外（并发互不等待）、结果写入缓存。"""
from __future__ import annotations

import threading
import time

import numpy as np
import pytest

import chalkbase.query.embed as embed_mod


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("CHALKBASE_CACHE", str(tmp_path))
    monkeypatch.setattr(embed_mod, "_mem", None)
    yield
    embed_mod.set_embedder(None)


def _fake(delay: float, calls: list):
    def fn(batch):
        calls.append(list(batch))
        time.sleep(delay)
        return [[1.0] + [0.0] * 1023 for _ in batch]

    return fn


def test_injected_embedder_is_used_and_results_cached(isolated_cache):
    calls: list = []
    embed_mod.set_embedder(_fake(0.0, calls))
    v1 = embed_mod.embed(["一个只在测试里出现的查询 A"])
    assert v1.shape == (1, 1024) and np.isclose(np.linalg.norm(v1[0]), 1.0)
    assert len(calls) == 1
    embed_mod.embed(["一个只在测试里出现的查询 A"])  # 命中缓存，不再调用
    assert len(calls) == 1


def test_concurrent_queries_do_not_serialize(isolated_cache):
    calls: list = []
    embed_mod.set_embedder(_fake(0.4, calls))
    ts = [threading.Thread(target=embed_mod.embed, args=([f"并发查询 {i}"],)) for i in range(4)]
    t0 = time.perf_counter()
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert time.perf_counter() - t0 < 1.0  # 串行需要 1.6s；并行约 0.4s
    assert len(calls) == 4
