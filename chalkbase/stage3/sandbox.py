"""兼容入口：沙箱实现已移至 `chalkbase.runtime.sandbox`（题型实例化、卡片校验与生成探针共用）。"""
from chalkbase.runtime.sandbox import RUNNER, SAMPLER, run_solver  # noqa: F401
