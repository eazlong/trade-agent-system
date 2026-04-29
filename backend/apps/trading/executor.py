"""
OrderExecutor - 订单执行器

职责：
1. 接收 Agent 下单指令
2. 调用 RiskGuard 前置校验（必须通过才能下单）
3. 通过交易所适配器执行订单
4. 写入 orders 表 + 更新持仓缓存

生命周期由 FrameManager 管理（initialize/shutdown）。
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from apps.trading.models import Order

from asgiref.sync import sync_to_async
from django.conf import settings

from .adapters import ADAPTER_MAP, BaseExchangeAdapter, OrderRequest

if TYPE_CHECKING:
    from apps.riskguard.guard import RiskGuard
    from apps.trading.models import Order

logger = logging.getLogger(__name__)


class OrderExecutor:
    """
    订单执行器（随交易框架懒加载，由 FrameManager 管理生命周期）。

    单例模式：通过 get_instance() 访问。
    """

    _instance: "OrderExecutor | None" = None

    def __init__(self):
        self._adapters: dict[str, BaseExchangeAdapter] = {}
        self._riskguard: Optional[RiskGuard] = None
        self._running = False

    @classmethod
    def get_instance(cls) -> "OrderExecutor | None":
        """返回当前单例，未初始化时为 None"""
        return cls._instance

    # ─── 生命周期 ────────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """
        FrameManager 启动交易框架时调用。
        1. 设置单例
        2. 从 DB 加载已激活的交易所账号
        3. 解密 API Key 并连接适配器
        """
        OrderExecutor._instance = self
        self._running = True

        await self._load_adapters()
        self._load_riskguard()

        logger.info(f"OrderExecutor initialized with {len(self._adapters)} adapters")

    async def shutdown(self) -> None:
        """
        FrameManager 停止交易框架时调用。
        断开所有适配器连接并清理单例。
        """
        self._running = False

        for name, adapter in self._adapters.items():
            await adapter.disconnect()
            logger.info(f"Adapter disconnected: {name}")

        self._adapters.clear()
        OrderExecutor._instance = None
        logger.info("OrderExecutor shutdown")

    # ─── 下单主流程 ───────────────────────────────────────────────────────────

    async def submit_order(
        self,
        exchange: str,
        symbol: str,
        side: str,
        order_type: str,
        quantity: Decimal,
        price: Optional[Decimal],
        exchange_account_id: str,
        user_id: Optional[str] = None,
    ) -> dict:
        """
        下单主流程。

        Args:
            exchange: 交易所名称 ('binance' | 'okx')
            symbol: 交易对符号（如 'BTCUSDT'）
            side: 买卖方向 ('buy' | 'sell')
            order_type: 订单类型 ('market' | 'limit')
            quantity: 数量
            price: 价格（市价单可为空）
            exchange_account_id: ExchangeAccount UUID
            user_id: User UUID（供 RiskGuard 风控校验）

        Returns:
            包含 exchange_order_id 和 order_id 的字典

        Raises:
            RuntimeError: OrderExecutor 未运行
            ValueError: 交易所适配器不存在
            PermissionError: RiskGuard 拒绝下单
        """
        uid = user_id  # 保留引用供 finally 使用
        if not self._running:
            raise RuntimeError("OrderExecutor is not running")

        adapter = self._adapters.get(exchange)
        if not adapter:
            raise ValueError(f"Exchange adapter not found: {exchange}")

        # 1. RiskGuard 前置校验
        request = OrderRequest(
            exchange=exchange,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
        )
        if self._riskguard:
            approved, reason = await self._riskguard.pre_trade_check(request, user_id)
            if not approved:
                raise PermissionError(f"RiskGuard拒绝下单: {reason}")

        # 2. 持久化订单 (status=pending)
        order = await self._persist_order(
            exchange_account_id=exchange_account_id,
            user_id=user_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            status="pending",
        )

        # 3. 发送到交易所
        try:
            response = await adapter.place_order(request)
            await self._update_order(
                order_id=str(order.id),
                status="submitted",
                exchange_order_id=response.exchange_order_id,
            )
            if self._riskguard:
                await self._riskguard.record_order_success(uid)
            logger.info(
                f"Order submitted: order_id={order.id} "
                f"exchange_order_id={response.exchange_order_id}"
            )
            return {
                "order_id": str(order.id),
                "exchange_order_id": response.exchange_order_id,
                "status": response.status,
            }
        except Exception as e:
            await self._update_order(
                order_id=str(order.id),
                status="failed",
                error_message=str(e),
            )
            if self._riskguard:
                await self._riskguard.record_order_failure(uid)
            logger.error(f"Order failed: order_id={order.id} - {e}")
            raise

    async def cancel_order(
        self,
        exchange: str,
        exchange_order_id: str,
        symbol: str,
    ) -> bool:
        """
        撤销指定订单。

        Returns:
            True 撤销成功，False 失败
        """
        if not self._running:
            raise RuntimeError("OrderExecutor is not running")

        adapter = self._adapters.get(exchange)
        if not adapter:
            raise ValueError(f"Exchange adapter not found: {exchange}")

        return await adapter.cancel_order(exchange_order_id, symbol)

    # ─── 持仓 & 余额查询 ─────────────────────────────────────────────────────

    async def get_positions(self, exchange: str) -> list:
        """获取指定交易所的当前持仓"""
        if not self._running:
            raise RuntimeError("OrderExecutor is not running")

        adapter = self._adapters.get(exchange)
        if not adapter:
            raise ValueError(f"Exchange adapter not found: {exchange}")

        return await adapter.get_positions()

    async def get_balance(self, exchange: str) -> dict:
        """获取指定交易所的账户余额"""
        if not self._running:
            raise RuntimeError("OrderExecutor is not running")

        adapter = self._adapters.get(exchange)
        if not adapter:
            raise ValueError(f"Exchange adapter not found: {exchange}")

        return await adapter.get_balance()

    # ─── 内部方法 ─────────────────────────────────────────────────────────────

    async def _load_adapters(self) -> None:
        """从 DB 加载已激活的交易所账号，解密并连接适配器"""
        from apps.exchange.models import ExchangeAccount

        accounts = await sync_to_async(
            lambda: list(ExchangeAccount.objects.filter(is_active=True))
        )()

        fernet = self._get_fernet()

        for account in accounts:
            exchange = account.exchange.lower()
            adapter_cls = ADAPTER_MAP.get(exchange)
            if not adapter_cls:
                logger.warning(f"No adapter for exchange: {exchange}, skipping")
                continue

            try:
                api_key_enc = (
                    bytes(account.api_key_enc)
                    if hasattr(account.api_key_enc, "__bytes__")
                    else account.api_key_enc
                )
                api_secret_enc = (
                    bytes(account.api_secret_enc)
                    if hasattr(account.api_secret_enc, "__bytes__")
                    else account.api_secret_enc
                )
                api_key = fernet.decrypt(api_key_enc).decode()
                api_secret = fernet.decrypt(api_secret_enc).decode()

                if exchange == "okx":
                    # OKX 需要额外的 passphrase，从 label 字段临时存储
                    passphrase = account.label or ""
                    adapter = adapter_cls(api_key, api_secret, passphrase, account.testnet)
                else:
                    adapter = adapter_cls(api_key, api_secret, account.testnet)

                await adapter.connect()
                self._adapters[exchange] = adapter
                logger.info(
                    f"Adapter loaded: {exchange} ({account.label or 'default'})"
                )
            except Exception as e:
                logger.error(f"Failed to load adapter for {exchange}: {e}")

    def _load_riskguard(self) -> None:
        """获取 RiskGuard 单例引用"""
        from apps.riskguard.guard import RiskGuard

        self._riskguard = RiskGuard.get_instance()

    def _get_fernet(self):
        """创建 Fernet 解密器"""
        from cryptography.fernet import Fernet

        key = getattr(settings, "FERNET_KEY", "")
        if not key:
            raise ValueError("FERNET_KEY not configured in settings")
        return Fernet(key.encode())

    async def _persist_order(
        self,
        exchange_account_id: str,
        user_id: Optional[str],
        symbol: str,
        side: str,
        order_type: str,
        quantity: Decimal,
        price: Optional[Decimal],
        status: str,
    ) -> "Order":
        """异步写入 orders 表"""
        from apps.trading.models import Order

        @sync_to_async
        def _create():
            kwargs_create = {
                "exchange_account_id": exchange_account_id,
                "symbol": symbol.upper(),
                "side": side.lower(),
                "order_type": order_type.lower(),
                "quantity": quantity,
                "price": price,
                "status": status,
                "request_id": uuid.uuid4(),
            }
            if user_id:
                kwargs_create["user_id"] = user_id
            return Order.objects.create(**kwargs_create)

        return await _create()

    async def _update_order(self, order_id: str, **kwargs) -> None:
        """异步更新订单"""
        from apps.trading.models import Order

        @sync_to_async
        def _update():
            Order.objects.filter(id=order_id).update(**kwargs)

        await _update()
