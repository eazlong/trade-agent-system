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
    mark_price: Optional[Decimal] = None  # 实时标记价（交易所提供，可能缺失）


@dataclass
class OrderFill:
    """订单成交状态（用于成交同步 / fill sync）"""

    status: str  # 'submitted' | 'partial' | 'filled' | 'cancelled' | 'failed'
    filled_quantity: Decimal
    avg_fill_price: Optional[Decimal]
    error_message: Optional[str] = None


class OrderNotFoundError(Exception):
    """交易所返回订单不存在（可能被外部取消或已过期）"""


class OrderLookupUnavailableError(Exception):
    """按 ``clientOrderId`` 反查**无法得出结论**（网络不可达 / 交易所返回意外响应）。

    与「确定不存在」是两个事实，必须分开：前者说明「现在不知道」，后者说明
    「交易所确实没有这张单」。把前者当成后者会把一张可能真实存在的订单记成
    ``failed``，本地账面与交易所分叉，而分叉的账面比空白更危险。
    """


class OrderLookupUnsupportedError(OrderLookupUnavailableError):
    """该适配器不支持按 ``clientOrderId`` 反查（能力缺失，同样属于「不知道」）"""


class OrderPlacementUnknown(Exception):
    """下单请求**可能已被交易所受理**，但结果没有得出结论。

    与 ``OrderLookupUnavailableError`` 是同一种「不知道」，区别在于它发生的位置：
    请求已经发出去了，只是响应丢了（读超时/连接被关），或者交易所回了重复单号而
    我们又对不上账。

    **绝不能当成失败**：交易所侧可能正有一张活着的单。本地记成 ``failed`` 会让账面
    与实际分叉，而分叉的账面比空白更危险——用户看到「下单失败」会再下一张，于是
    敞口变成两倍。调用方必须把它落成 ``status="unknown"``（非终态），让悬挂扫描与
    成交同步继续找它。
    """


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
        """发送订单到交易所

        Raises:
            OrderPlacementUnknown: 请求可能已被受理但结果不明。调用方必须落成
                ``status="unknown"``，不得当成失败。
        """

    @abstractmethod
    async def cancel_order(self, exchange_order_id: str, symbol: str) -> bool:
        """撤销指定订单"""

    @abstractmethod
    async def fetch_order(
        self, exchange_order_id: str, symbol: str
    ) -> OrderFill:
        """查询订单当前成交状态（用于成交同步）。

        Args:
            exchange_order_id: 交易所订单号
            symbol: 本地交易对符号（如 'DOGE/USDT'，由适配器归一化）

        Returns:
            OrderFill: 订单状态、已成交数量、成交均价

        Raises:
            OrderNotFoundError: 交易所返回订单不存在
        """

    @abstractmethod
    async def get_positions(self) -> list[Position]:
        """获取当前持仓"""

    async def find_order_by_client_id(
        self, client_order_id: str, symbol: str
    ) -> Optional[dict]:
        """按 ``clientOrderId`` 反查交易所侧订单的**原始**响应。

        这是「本地已落单、但 ``exchange_order_id`` 为空」那批行的唯一消解手段：
        本地只有 ``Order.request_id``，交易所侧只有 ``newClientOrderId``，两者相
        等是这条链路的前提。

        Returns:
            命中时返回交易所原始 dict。

        Raises:
            OrderLookupUnavailableError: 无法得出结论（不可达 / 意外响应）。
            OrderLookupUnsupportedError: 适配器不具备该能力。

        **注意返回 ``None`` 只允许表示「交易所确定没有这张单」**（如币安 -2013）。
        无法区分这两种情况的实现必须抛错，不能笼统返回 ``None``。
        """
        raise OrderLookupUnsupportedError(
            f"{type(self).__name__} 不支持按 clientOrderId 反查"
        )

    @abstractmethod
    async def get_balance(self) -> dict[str, Decimal]:
        """获取账户余额，key 为资产名称，value 为数量"""
