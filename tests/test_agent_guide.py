"""面向调用方 Agent 的使用说明必须与真实接口一致：说明里出现的 `cur.xxx(` 方法都存在，说明可经 API / CLI 取得。"""
import re
import subprocess
import sys

from chalkbase import Curriculum, agent_guide


def test_guide_mentions_only_existing_methods():
    text = agent_guide()
    names = set(re.findall(r"cur\.(\w+)\(", text))
    assert {"search", "archetypes", "instantiate", "check_item", "boundary", "chain"} <= names
    missing = sorted(n for n in names if not hasattr(Curriculum, n))
    assert not missing, f"AGENT_GUIDE.md 提到了不存在的方法：{missing}"


def test_guide_is_compact_and_available_from_cli():
    assert len(agent_guide()) < 4500  # 要放进 Agent 的上下文，保持精简
    out = subprocess.run([sys.executable, "-m", "chalkbase", "guide"], capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0 and "ChalkBase 使用说明" in out.stdout
