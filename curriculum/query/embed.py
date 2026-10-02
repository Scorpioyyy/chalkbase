"""DashScope 文本向量（text-embedding-v4，1024 维）：按 (模型, 文本哈希) 缓存到 `.cache/embeddings/`。

API key 只从环境变量 `DASHSCOPE_API_KEY` 读取；节点用 `DASHSCOPE_BASE_URL`（与标注客户端同一约定，docs/decisions.md D12）。
缓存缺失且无法联网/无 key 时抛 `EmbeddingUnavailable`，调用方应降级为词法检索。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import requests

from curriculum.common import ROOT

MODEL = "text-embedding-v4"
BATCH = 10  # text-embedding-v3/v4 单次请求最多 10 条
CACHE_DIR = ROOT / ".cache" / "embeddings"
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_lock = threading.Lock()


class EmbeddingUnavailable(RuntimeError):
    pass


def _key(text: str) -> str:
    return hashlib.sha256(f"{MODEL}\n{text}".encode("utf-8")).hexdigest()[:32]


_mem: dict[str, list[float]] | None = None


def _load_cache() -> dict[str, list[float]]:
    global _mem
    if _mem is None:
        p = CACHE_DIR / f"{MODEL}.json"
        _mem = {}
        if p.exists():
            try:
                _mem = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                _mem = {}
    return _mem


def _save_cache(cache: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_DIR / f"{MODEL}.json.tmp"
    tmp.write_text(json.dumps(cache), encoding="utf-8")
    tmp.replace(CACHE_DIR / f"{MODEL}.json")


def _call(batch: list[str]) -> list[list[float]]:
    key = os.environ.get("DASHSCOPE_API_KEY")
    if not key:
        raise EmbeddingUnavailable("缺少 DASHSCOPE_API_KEY 且向量缓存未命中")
    base = os.environ.get("DASHSCOPE_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    last = None
    for attempt in range(4):
        try:
            r = requests.post(
                f"{base}/embeddings",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": MODEL, "input": batch, "encoding_format": "float"},
                timeout=60,
            )
            if r.status_code == 200:
                data = sorted(r.json()["data"], key=lambda d: d["index"])
                return [d["embedding"] for d in data]
            last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = type(e).__name__
        import time

        time.sleep(1.5 ** attempt)
    raise EmbeddingUnavailable(f"向量接口调用失败: {last}")


def embed(texts: list[str]) -> np.ndarray:
    """返回 L2 归一化的向量矩阵 (n, 1024)。命中缓存的文本不联网。"""
    with _lock:
        cache = _load_cache()
        need = sorted({t for t in texts if _key(t) not in cache})
        if need:
            batches = [need[i : i + BATCH] for i in range(0, len(need), BATCH)]
            with ThreadPoolExecutor(max_workers=4) as ex:
                results = list(ex.map(_call, batches))
            for b, vs in zip(batches, results):
                for t, v in zip(b, vs):
                    cache[_key(t)] = v
            _save_cache(cache)
    m = np.array([cache[_key(t)] for t in texts], dtype=np.float32)
    return m / np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-9)
