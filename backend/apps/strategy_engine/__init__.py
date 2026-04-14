"""
策略统一执行引擎

回测和实盘共用同一份策略代码。
策略继承 BaseStrategy 并实现 on_bar 方法。
"""

from .base import BaseStrategy, StrategyContext, OrderSignal
from .registry import StrategyRegistry, register_strategy

__all__ = [
    "BaseStrategy",
    "StrategyContext",
    "OrderSignal",
    "StrategyRegistry",
    "register_strategy",
]
