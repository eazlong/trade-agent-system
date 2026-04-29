"""
RSI 交叉策略示例

策略逻辑：
- RSI 从超卖区 (<30) 向上穿越 30 时买入
- RSI 从超买区 (>70) 向下穿越 70 时卖出

此策略演示如何使用策略引擎的 BaseStrategy 和 indicators 模块。
"""

from decimal import Decimal

import numpy as np

from apps.strategy_engine.base import BaseStrategy, StrategyContext
from apps.strategy_engine.indicators import rsi


class RsiCrossStrategy(BaseStrategy):
    """RSI 超卖/超买交叉策略"""

    name = "rsi_cross"
    description = "RSI 超卖区金叉买入，超买区死叉卖出"
    params_schema = {
        "rsi_period": {"type": "integer", "default": 14},
        "oversold": {"type": "number", "default": 30},
        "overbought": {"type": "number", "default": 70},
        "quantity": {"type": "number", "default": 0.01},
    }

    def __init__(self, context: StrategyContext):
        super().__init__(context)
        self.period = context.params.get("rsi_period", 14)
        self.oversold = context.params.get("oversold", 30)
        self.overbought = context.params.get("overbought", 70)
        self.quantity = Decimal(str(context.params.get("quantity", 0.01)))

    def on_bar(self, kline: dict, history: list[dict]):
        # 需要足够的数据才能计算 RSI
        min_bars = self.period + 2
        if len(history) < min_bars:
            return None

        rsi_vals = rsi(history, period=self.period)

        # 获取最后两个有效的 RSI 值（非 NaN）
        valid_mask = ~np.isnan(rsi_vals)
        valid_count = np.sum(valid_mask)
        if valid_count < 2:
            return None

        valid_values = rsi_vals[valid_mask]
        prev_rsi = float(valid_values[-2])
        curr_rsi = float(valid_values[-1])

        # 超卖区金叉 → 买入（无持仓时）
        if prev_rsi < self.oversold and curr_rsi >= self.oversold:
            if self.ctx.position == 0:
                return self.ctx.buy(
                    quantity=self.quantity,
                    signal_name="rsi_oversold_cross",
                )

        # 超买区死叉 → 卖出（有持仓时）
        if prev_rsi > self.overbought and curr_rsi <= self.overbought:
            if self.ctx.position > 0:
                return self.ctx.sell(
                    quantity=self.ctx.position,
                    signal_name="rsi_overbought_cross",
                )

        return None
