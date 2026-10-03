"""题型实例化运行时：沙箱求解、参数采样、题面渲染。对外入口是 `Curriculum.instantiate*`。"""
from chalkbase.runtime.features import params_features, slot_types
from chalkbase.runtime.instantiate import (
    InstantiationError,
    NoInBoundsSample,
    Problem,
    instantiate,
    instantiate_many,
    instantiate_with,
)
from chalkbase.runtime.sandbox import run_solver

__all__ = ["InstantiationError", "NoInBoundsSample", "Problem", "instantiate", "instantiate_many", "instantiate_with",
           "params_features", "run_solver", "slot_types"]
