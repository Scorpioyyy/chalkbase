"""生成 data/kp_embeddings.npz：知识点向量（text-embedding-v4，float16），随包发布供检索使用。

每行对应一个知识点，附该知识点向量文本的哈希（`chalkbase.query.embed.text_hash`）。重跑时优先复用已有文件与本地响应缓存，
只对缺失或文本已变的知识点在线调用（需要环境变量 DASHSCOPE_API_KEY）。知识点的名称/别名/描述变动后须重跑，
否则 tests 里的 `test_kp_embeddings_current` 失败。

用法：python scripts/build_embeddings.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chalkbase.query import Curriculum  # noqa: E402
from chalkbase.query import embed as E  # noqa: E402


def main() -> None:
    cur = Curriculum()
    ids = sorted(cur.knowledge_points)
    texts = [E.dense_text(cur.kp(i)) for i in ids]
    mat = E.embed(texts, cur.data_dir)  # 随包文件 > 本地缓存 > 在线调用
    out = cur.data_dir / E.EMBEDDINGS_FILE
    np.savez_compressed(out, model=np.array(E.MODEL), kp_ids=np.array(ids), text_sha256=np.array([E.text_hash(t) for t in texts]),
                        vectors=mat.astype(np.float16))
    print(f"{out}: {len(ids)} x {mat.shape[1]}, {out.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
