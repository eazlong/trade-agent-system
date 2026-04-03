"""
测试工具模块

包含：
- buggify: 合成故障注入（确定性仿真测试 DST）
- shadow_evaluator: 影子状态评估（语义先知 L1）
- check_invariants: 不变量检查脚本
"""

from .buggify import (
    buggify,
    buggify_delay,
    buggify_decorator,
    BuggifyConfig,
    BuggifyContext,
    inject_fault,
    FAULT_TYPES,
)

__all__ = [
    "buggify",
    "buggify_delay",
    "buggify_decorator",
    "BuggifyConfig",
    "BuggifyContext",
    "inject_fault",
    "FAULT_TYPES",
]