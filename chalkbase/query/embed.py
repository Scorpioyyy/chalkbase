"""DashScope 文本向量（text-embedding-v4，1024 维）。

查找顺序：随包发布的知识点向量（`data/kp_embeddings.npz`，按文本哈希匹配，文本变了的条目自动失效）> 本地响应缓存 > 在线调用。
在线调用的 API key 只从环境变量 `DASHSCOPE_API_KEY` 读取，节点用 `DASHSCOPE_BASE_URL`。
缓存缺失且无法联网/无 key 时抛 `EmbeddingUnavailable`，调用方应降级为词法检索。

本地响应缓存目录：环境变量 `CHALKBASE_CACHE`；否则仓库内（存在 pyproject.toml）用 `.cache/embeddings/`，
安装态用 `~/.cache/chalkbase/embeddings/`。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import numpy as np

from chalkbase.common import DATA_DIR, ROOT

MODEL = "text-embedding-v4"
DIM = 1024
BATCH = 10  # text-embedding-v3/v4 单次请求最多 10 条
EMBEDDINGS_FILE = "kp_embeddings.npz"
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_lock = threading.Lock()


class EmbeddingUnavailable(RuntimeError):
    pass


def cache_dir() -> Path:
    env = os.environ.get("CHALKBASE_CACHE")
    if env:
        return Path(env)
    if (ROOT / "pyproject.toml").exists():
        return ROOT / ".cache" / "embeddings"
    return Path.home() / ".cache" / "chalkbase" / "embeddings"


def text_hash(text: str) -> str:
    """向量的文本键：sha256(模型 ID + 换行 + 文本) 的十六进制全串。"""
    return hashlib.sha256(f"{MODEL}\n{text}".encode("utf-8")).hexdigest()


def _key(text: str) -> str:
    return text_hash(text)[:32]


def dense_text(kp) -> str:
    """知识点参与向量检索的文本：名称、别名、描述。"""
    return f"{kp.name}。{'、'.join(kp.aliases)}。{kp.description}"


# ---------------------------------------------------------------- 随包发布的知识点向量

_packaged: dict[Path, dict[str, np.ndarray]] = {}


def load_packaged(data_dir: Optional[Path] = None) -> dict[str, np.ndarray]:
    """{文本键: float16 向量}。文件缺失、模型不符或格式异常时返回空字典。"""
    d = Path(data_dir) if data_dir else DATA_DIR
    if d not in _packaged:
        out: dict[str, np.ndarray] = {}
        p = d / EMBEDDINGS_FILE
        if p.exists():
            try:
                z = np.load(p, allow_pickle=False)
                if str(z["model"]) == MODEL:
                    out = {_h[:32]: v for _h, v in zip(z["text_sha256"].tolist(), z["vectors"])}
            except Exception:
                out = {}
        _packaged[d] = out
    return _packaged[d]


# ---------------------------------------------------------------- 本地响应缓存

_mem: dict[str, list[float]] | None = None


def _load_cache() -> dict[str, list[float]]:
    global _mem
    if _mem is None:
        p = cache_dir() / f"{MODEL}.json"
        _mem = {}
        if p.exists():
            try:
                _mem = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                _mem = {}
    return _mem


def _save_cache(cache: dict) -> None:
    try:
        d = cache_dir()
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f"{MODEL}.json.tmp"
        tmp.write_text(json.dumps(cache), encoding="utf-8")
        tmp.replace(d / f"{MODEL}.json")
    except OSError:
        pass  # 缓存目录不可写时只用内存缓存


def _call(batch: list[str]) -> list[list[float]]:
    key = os.environ.get("DASHSCOPE_API_KEY")
    if not key:
        raise EmbeddingUnavailable("缺少 DASHSCOPE_API_KEY 且向量缓存未命中")
    base = os.environ.get("DASHSCOPE_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    body = json.dumps({"model": MODEL, "input": batch, "encoding_format": "float"}).encode("utf-8")
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(f"{base}/embeddings", data=body, method="POST",
                                         headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = sorted(json.loads(r.read().decode("utf-8"))["data"], key=lambda d: d["index"])
                return [d["embedding"] for d in data]
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
        except (urllib.error.URLError, OSError, ValueError) as e:
            last = type(e).__name__
        import time

        time.sleep(1.5 ** attempt)
    raise EmbeddingUnavailable(f"向量接口调用失败: {last}")


def embed(texts: list[str], data_dir: Optional[Path] = None) -> np.ndarray:
    """返回 L2 归一化的向量矩阵 (n, 1024)。命中随包向量或本地缓存的文本不联网。"""
    packaged = load_packaged(data_dir)
    with _lock:
        cache = _load_cache()
        need = sorted({t for t in texts if _key(t) not in packaged and _key(t) not in cache})
        if need:
            batches = [need[i : i + BATCH] for i in range(0, len(need), BATCH)]
            with ThreadPoolExecutor(max_workers=4) as ex:
                results = list(ex.map(_call, batches))
            for b, vs in zip(batches, results):
                for t, v in zip(b, vs):
                    cache[_key(t)] = v
            _save_cache(cache)
    m = np.array([packaged[_key(t)] if _key(t) in packaged else cache[_key(t)] for t in texts], dtype=np.float32)
    return m / np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-9)
