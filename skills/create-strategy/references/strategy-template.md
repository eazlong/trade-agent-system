# 策略文件模板

> 由 SKILL.md 阶段 3.3 引用。复制此模板，按用户需求填充入场/出场/风控逻辑。
>
> 模板内的注释包含所有关键陷阱的内联提醒，生成代码时逐条核对。

```python
"""
{策略名称} - {一句话描述}

策略逻辑：
- {入场条件}
- {出场条件}

风控：{风控规则}
"""

from decimal import Decimal

from apps.strategy_engine.base import BaseStrategy, StrategyContext
from apps.strategy_engine.indicators import rsi, sma, ema, macd, bollinger, atr, stoch
from apps.strategy_engine.registry import register_strategy


@register_strategy(name="{strategy_name}")
class {StrategyName}Strategy(BaseStrategy):
    """{策略描述}"""

    name = "{strategy_name}"
    description = "{一句话描述}"
    params_schema = {
        # 可调参数
        # "param_name": {"type": "number"/"integer"/"string", "default": value},
        "position_pct": {"type": "number", "default": 0.1},
    }

    def __init__(self, context: StrategyContext):
        super().__init__(context)
        # 注意：不要设置 self.portfolio = PctCapitalPortfolio()！
        # 使用默认的 SingleAssetPortfolio，on_bar 返回的 quantity 直接生效。
        # 如需使用 PctCapitalPortfolio，见 api-reference.md "仓位计算" 章节。
        self.bb_period = context.params.get("bb_period", 20)
        self.min_bars = self.bb_period + 5

    def on_bar(self, kline: dict, history: list[dict]):
        """
        每根 K 线完成时调用。

        Args:
            kline: 当前 K 线 {open, high, low, close, volume, timestamp}
            history: 历史 K 线列表（含当前 K 线）

        Returns:
            OrderSignal 或 None
        """
        if len(history) < self.min_bars:
            return None

        # ═══ 指标计算 ═══
        # 路径依赖指标（EMA/ATR）必须用有限回溯窗口 history[-period*5:]
        # 详见 SKILL.md 清单 #4
        #
        # _lookback = min(self.ema_period * 5, len(history))
        # _recent = history[-_lookback:]
        # ema_array = ema(_recent, period=self.ema_period)
        # ema_val = float(ema_array[-1])

        # 访问上下文用 self.ctx（仅以下属性存在，禁止使用 portfolio_value/equity/cash）
        # self.ctx.position           # 当前持仓量 (Decimal)
        # self.ctx.balance            # 可用余额/现金 (Decimal)，≠ 总资金！
        # self.ctx.to_portfolio_context().total_capital  # 总资金 = balance + 持仓市值

        # ⚠️ 必须 return 信号，不能仅调用（详见 SKILL.md 清单 #2）

        # ✅ 买入示例（手动计算仓位）：
        # if self.ctx.position == 0 and buy_condition:
        #     total_capital = self.ctx.to_portfolio_context().total_capital
        #     price = Decimal(str(kline["close"]))
        #     position_pct = Decimal(str(self.ctx.params.get("position_pct", 0.1)))
        #     qty = (total_capital * position_pct / price).quantize(Decimal("0.0001"))
        #     return self.ctx.buy(quantity=qty, signal_name="entry")

        # ✅ 卖出示例：
        # if self.ctx.position > 0 and sell_condition:
        #     return self.ctx.sell(quantity=self.ctx.position, signal_name="exit")

        # ✅ 平仓：卖出全部持仓
        # return self.ctx.close_position(signal_name="take_profit")

        return None

    def on_start(self) -> None:
        """策略启动时调用（初始化指标状态等）"""

    def on_stop(self) -> None:
        """策略停止时调用"""

    def get_watch_signals(self) -> list[dict]:
        """返回最小周期信号配置，由 LiveStrategyRunner 注册到 SignalMonitor 做初筛。

        **重要**：此方法由 agent 在阶段3根据策略入场条件自动推导生成，
        不需要用户手动配置。推导规则见 references/watch-signals.md。

        示例（RSI超卖策略）：
        return [
            {
                "interval": "1h",
                "indicator_type": "rsi",
                "indicator_params": {"period": 14},
                "condition": {"operator": "lt", "left": {"field": "rsi"}, "right": {"value": 30}},
                "trigger_type": "continuous",
            }
        ]

        每个 dict 包含:
            interval: str         — K线周期 (如 "15m", "1h")
            indicator_type: str   — 指标类型 (donchian/bollinger/rsi/price_watch/...)
            indicator_params: dict — 指标参数 (如 {"period": 20})
            condition: dict       — 触发条件，格式见 references/watch-signals.md
            trigger_type: str     — "once"(单次) 或 "continuous"(持续)

        子类覆盖此方法启用两阶段信号检测：
        1. SignalMonitor 按最小周期检测单指标条件（轻量初筛）
        2. 触发后运行完整 on_bar() 验证多指标组合条件
        """
        return []  # ⚠️ agent 必须替换为实际 watch signal 列表（详见 SKILL.md 清单 #7）
```
