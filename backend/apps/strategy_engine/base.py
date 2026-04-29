"""
策略引擎基础模块

定义策略基类、运行上下文和订单信号。
所有用户策略必须继承 BaseStrategy 并实现 on_bar 方法。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class OrderSignal:
    """策略发出的订单信号"""

    side: str  # 'buy' | 'sell'
    quantity: Decimal
    order_type: str = "market"  # 'market' | 'limit'
    price: Optional[Decimal] = None
    exchange: str = "binance"  # 默认交易所
    signal_name: str = ""  # 信号名称（用于日志和回测记录）
    metadata: dict = field(default_factory=dict)  # 额外元数据

    def __post_init__(self):
        if self.side not in ("buy", "sell"):
            raise ValueError(f"Invalid side: {self.side!r}, must be 'buy' or 'sell'")
        if self.quantity <= 0:
            raise ValueError(f"Quantity must be positive, got {self.quantity}")


class StrategyContext:
    """策略运行上下文，注入到每个策略实例"""

    def __init__(
        self,
        symbol: str,
        timeframe: str,
        mode: str,
        params: dict,
        balance: Decimal | None = None,
        position: Decimal | None = None,
    ):
        self.symbol = symbol
        self.timeframe = timeframe
        self.mode = mode  # 'backtest' | 'live'
        self.params = params
        self._balance = balance or Decimal("0")
        self._position = position or Decimal("0")

    @property
    def balance(self) -> Decimal:
        """当前可用余额"""
        return self._balance

    @balance.setter
    def balance(self, value: Decimal) -> None:
        self._balance = value

    @property
    def position(self) -> Decimal:
        """当前持仓量"""
        return self._position

    @position.setter
    def position(self, value: Decimal) -> None:
        self._position = value

    def buy(
        self,
        quantity: Decimal,
        price: Decimal | None = None,
        order_type: str = "market",
        signal_name: str = "",
        **kwargs,
    ) -> OrderSignal:
        """发出买入信号"""
        return OrderSignal(
            side="buy",
            quantity=quantity,
            price=price,
            order_type=order_type,
            signal_name=signal_name or "buy",
            **kwargs,
        )

    def sell(
        self,
        quantity: Decimal,
        price: Decimal | None = None,
        order_type: str = "market",
        signal_name: str = "",
        **kwargs,
    ) -> OrderSignal:
        """发出卖出信号"""
        return OrderSignal(
            side="sell",
            quantity=quantity,
            price=price,
            order_type=order_type,
            signal_name=signal_name or "sell",
            **kwargs,
        )

    def close_position(
        self, price: Decimal | None = None, **kwargs
    ) -> OrderSignal | None:
        """平仓信号（卖出全部持仓）"""
        if self._position <= 0:
            return None
        return self.sell(self._position, price=price, signal_name="close", **kwargs)


class BaseStrategy(ABC):
    """
    策略基类，所有策略必须继承。

    子类必须实现：
    - name: 策略名称
    - on_bar(kline, history): 每根 K 线完成时的逻辑

    可选覆盖：
    - on_start(): 策略启动时调用
    - on_stop(): 策略停止时调用
    - params_schema: 参数校验 Schema
    """

    name = "unnamed"
    description = ""
    params_schema: dict[str, Any] = {}

    def __init__(self, context: StrategyContext):
        self.ctx = context

    @abstractmethod
    def on_bar(self, kline: dict, history: list[dict]) -> OrderSignal | None:
        """
        每根 K 线完成时调用。

        Args:
            kline: 当前 K 线 {open, high, low, close, volume, timestamp}
            history: 历史 K 线列表（含当前 K 线）

        Returns:
            OrderSignal 或 None（不操作）
        """

    def on_start(self) -> None:
        """策略启动时调用（初始化指标等）"""

    def on_stop(self) -> None:
        """策略停止时调用（清理资源等）"""

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r})"
