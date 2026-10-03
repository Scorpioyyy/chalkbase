"""VeriChalk 课程知识库。对外接口：`from chalkbase import Curriculum`（见 chalkbase/README.md）。"""


def __getattr__(name):  # 惰性导入：避免 `python -m chalkbase.xxx` 子模块与包初始化互相拖慢
    if name in ("Curriculum", "Location", "ChainEntry"):
        from chalkbase import query

        return getattr(query, name)
    raise AttributeError(name)
