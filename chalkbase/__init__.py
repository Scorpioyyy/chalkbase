"""ChalkBase：北师大版小学数学课程知识库。对外接口：`from chalkbase import Curriculum`（见 docs/api.md）。"""

__version__ = "0.1.0"

__all__ = ["Curriculum", "Location", "ChainEntry", "Problem", "DataSchemaError", "__version__"]


def __getattr__(name):  # 惰性导入：避免 `python -m chalkbase.xxx` 子模块与包初始化互相拖慢
    if name in ("Curriculum", "Location", "ChainEntry", "DataSchemaError"):
        from chalkbase import query

        return getattr(query, name)
    if name == "Problem":
        from chalkbase.runtime import Problem

        return Problem
    raise AttributeError(name)
