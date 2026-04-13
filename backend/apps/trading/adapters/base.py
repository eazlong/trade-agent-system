"""
交易所适配器基类和核心数据类型。

所有交易所适配器实现此接口，提供统一的交易操作抽象。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass
class OrderRequest:
    """下单请求"""

    exchange: str
    symbol: str
    order_type: str  # 'limit' | 'market' | 'stop'
    side: str  # 'buy' | 'sell'
    quantity: Decimal
    price: Optional[Decimal] = None
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    client_order_id: Optional[str] = None


@dataclass
class OrderResponse:
    """下单响应"""

    exchange_order_id: str
    status: str  # 'NEW' | 'FILLED' | 'PARTIALLY_FILLED' | etc.
    filled_qty: Decimal
    avg_price: Optional[Decimal]
    fee: Optional[Decimal]
    raw: dict  # 交易所原始响应，便于调试


@dataclass
class Position:
    """持仓信息"""

    symbol: str
    side: str  # 'long' | 'short'
    quantity: Decimal
    entry_price: Decimal
    unrealized_pnl: Decimal
    leverage: int


class BaseExchangeAdapter(ABC):
    """
    交易所适配器抽象基类。

    所有具体交易所实现必须实现以下异步方法。
    """

    def __init__(self, api_key: str, secret: str):
        self._api_key = api_key
        self._secret = secret

    @abstractmethod
    async def connect(self) -> None:
        """建立与交易所的连接，初始化 HTTP 客户端"""

    @abstractmethod
    async def disconnect(self) -> None:
        """关闭连接，释放资源"""

    @abstractmethod
    async def place_order(self, request: OrderRequest) -> OrderResponse:
        """发送订单到交易所"""

    @abstractmethod
    async def cancel_order(self, exchange_order_id: str, symbol: str) -> bool:
        """撤销指定订单"""

    @abstractmethod
    async def get_positions(self) -> list[Position]:
        """获取当前持仓"""

    @abstractmethod
    async def get_balance(self) -> dict[str, Decimal]:
        """获取账户余额，key 为资产名称，value 为数量"""
