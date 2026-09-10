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

import asyncio
import logging
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from apps.trading.models import Order

from django.conf import settings

from apps.core.db_utils import db_async
from .adapters import (
    ADAPTER_MAP,
    BaseExchangeAdapter,
    OrderNotFoundError,
    OrderRequest,
)

if TYPE_CHECKING:
    from apps.riskguard.guard import RiskGuard
    from apps.trading.models import Order

logger = logging.getLogger(__name__)


def _to_bytes(value):
    """将 memoryview/bytearray 等类型统一转为 bytes，供 Fernet 解密使用。"""
    if isinstance(value, (bytes, str)):
        return value
    if isinstance(value, (memoryview, bytearray)):
        return bytes(value)
    return None


def compute_realized_pnl(
    history: list[dict],
    side: str,
    filled_quantity: Decimal,
    avg_fill_price: Decimal | None,
) -> Decimal | None:
    """用移动平均成本法计算平仓单的已实现盈亏（纯函数，便于单测）。

    按时间序回放同账户+同品种的成交历史（不含本单），维护移动平均成本：
    买入 → 成本加权平均；卖出 → 仓位减少、成本不变。
    平仓盈亏 = (平仓价 - 平均成本) × 平仓量。仅支持多头（买入开仓、卖出平仓）。

    Args:
        history: 成交历史，每项 {side, filled_quantity, avg_fill_price}，按 created_at 升序
        side: 当前订单方向
        filled_quantity: 当前订单已成交量
        avg_fill_price: 当前订单成交均价

    Returns:
        已实现盈亏；非卖出单、无成交均价、或无成本基础（无历史买入/开空单）时返回 None
    """
    if side != "sell" or filled_quantity is None or filled_quantity <= 0:
        return None
    if avg_fill_price is None:
        return None

    pos = Decimal("0")
    avg_cost = Decimal("0")
    for row in history:
        q = row.get("filled_quantity") or Decimal("0")
        p = row.get("avg_fill_price")
        if q <= 0 or p is None:
            continue
        if row.get("side") == "buy":
            if pos > 0:
                avg_cost = (avg_cost * pos + p * q) / (pos + q)
            else:
                avg_cost = p
            pos += q
        else:
            pos = max(Decimal("0"), pos - q)

    if pos <= 0:
        # 无成本基础：无历史买入（如开空单），无法归因已实现盈亏
        return None
    return (avg_fill_price - avg_cost) * min(filled_quantity, pos)


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
        self._fill_sync_task: Optional[asyncio.Task] = None

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
        self.start_fill_sync()

        logger.info(f"OrderExecutor initialized with {len(self._adapters)} adapters")

    async def shutdown(self) -> None:
        """
        FrameManager 停止交易框架时调用。
        停止成交同步任务、断开所有适配器并清理单例。
        """
        self._running = False

        if self._fill_sync_task and not self._fill_sync_task.done():
            self._fill_sync_task.cancel()
            try:
                await self._fill_sync_task
            except (asyncio.CancelledError, Exception):
                pass
            self._fill_sync_task = None

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
        is_close_position: bool = False,
        live_session_id: Optional[str] = None,
    ) -> dict:
        """
        下单主流程。

        Args:
            exchange: 交易所名称 ('binance')
            symbol: 交易对符号（如 'BTCUSDT'）
            side: 买卖方向 ('buy' | 'sell')
            order_type: 订单类型 ('market' | 'limit')
            quantity: 数量
            price: 价格（市价单可为空）
            exchange_account_id: ExchangeAccount UUID
            user_id: User UUID（供 RiskGuard 风控校验）
            is_close_position: 平仓信号，跳过 RiskGuard 风控检查
            live_session_id: 触发的实盘会话 UUID（用于追溯订单来源策略）

        Returns:
            包含 exchange_order_id 和 order_id 的字典

        Raises:
            RuntimeError: OrderExecutor 未运行
            ValueError: 交易所适配器不存在
            PermissionError: RiskGuard 拒绝下单
        """
        logger.info(f"[SF-07][OrderExecutor] submit: {exchange} {symbol} {side} {order_type} qty={quantity} close={is_close_position}")
        uid = user_id  # 保留引用供 finally 使用
        if not self._running:
            raise RuntimeError("OrderExecutor is not running")

        adapter = self._adapters.get(exchange)
        if not adapter:
            raise ValueError(f"Exchange adapter not found: {exchange}")

        # 1. RiskGuard 前置校验（平仓跳过）
        request = OrderRequest(
            exchange=exchange,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
        )
        if is_close_position:
            logger.info("[SF-07a][OrderExecutor] close position — skipping RiskGuard")
        elif self._riskguard:
            # 诊断：检查方法类型
            import inspect
            method = self._riskguard.pre_trade_check
            logger.info(f"[SF-07a][OrderExecutor] pre_trade_check method: {method}, iscoroutinefunction={inspect.iscoroutinefunction(method)}")

            logger.info(f"[SF-07a][OrderExecutor] riskguard instance: {type(self._riskguard).__name__} id={id(self._riskguard)}")
            logger.info("[SF-07a][OrderExecutor] calling pre_trade_check...")
            try:
                approved, reason = await self._riskguard.pre_trade_check(request, user_id)
                logger.info(f"[SF-07a][OrderExecutor] pre_trade_check returned: approved={approved}")
            except Exception as e:
                logger.error(f"[SF-07a][OrderExecutor] pre_trade_check exception: {type(e).__name__}: {e}")
                raise
            if not approved:
                logger.warning(f"[SF-04][OrderExecutor] RiskGuard拒绝下单: {reason}")
                raise PermissionError(f"RiskGuard拒绝下单: {reason}")

        # 2. 持久化订单 (status=pending)
        logger.info("[SF-07b][OrderExecutor] persisting order...")
        try:
            order = await asyncio.wait_for(
                self._persist_order(
                    exchange_account_id=exchange_account_id,
                    user_id=user_id,
                    symbol=symbol,
                    side=side,
                    order_type=order_type,
                    quantity=quantity,
                    price=price,
                    status="pending",
                    live_session_id=live_session_id,
                ),
                timeout=30.0
            )
        except asyncio.TimeoutError:
            logger.error("[SF-07b][OrderExecutor] _persist_order timeout (30s)")
            raise TimeoutError("Order persistence timeout")
        logger.info(f"[SF-07c][OrderExecutor] order persisted: id={order.id}")

        # 3. 发送到交易所
        try:
            logger.info("[SF-07d][OrderExecutor] calling adapter.place_order...")
            try:
                response = await asyncio.wait_for(
                    adapter.place_order(request),
                    timeout=15.0
                )
            except asyncio.TimeoutError:
                logger.error("[SF-07d][OrderExecutor] adapter.place_order timeout (15s)")
                raise TimeoutError("Exchange API timeout")

            # 应用下单响应中的即时成交信息（市价单通常同步成交）
            new_status = self._map_exchange_status(response.status)
            update_kwargs = {
                "status": new_status,
                "exchange_order_id": response.exchange_order_id,
            }
            try:
                filled_qty = Decimal(response.filled_qty)
            except Exception:
                filled_qty = Decimal("0")
            if filled_qty > 0:
                update_kwargs["filled_quantity"] = filled_qty
                if response.avg_price is not None:
                    try:
                        update_kwargs["avg_fill_price"] = Decimal(
                            response.avg_price
                        )
                    except Exception:
                        pass
            # 平仓卖单即时成交（市价单通常同步成交）时计算已实现盈亏，
            # 并同步会话权益。盈亏计算失败不阻塞下单主流程（订单已在交易所）。
            if (
                side == "sell"
                and filled_qty > 0
                and update_kwargs.get("avg_fill_price") is not None
            ):
                try:
                    pnl = await self._apply_close_pnl(
                        exchange_account_id=exchange_account_id,
                        symbol=symbol,
                        order_id=str(order.id),
                        live_session_id=live_session_id,
                        side=side,
                        filled_quantity=filled_qty,
                        avg_fill_price=update_kwargs["avg_fill_price"],
                    )
                except Exception as e:
                    logger.warning(
                        f"[OrderExecutor] realized_pnl compute failed: {e}"
                    )
                    pnl = None
                if pnl is not None:
                    update_kwargs["realized_pnl"] = pnl
                    logger.info(
                        f"[OrderExecutor] realized_pnl={pnl} for close order {order.id}"
                    )
            await self._update_order(
                order_id=str(order.id),
                **update_kwargs,
            )
            if self._riskguard:
                await self._riskguard.record_order_success(uid)
            logger.info(
                f"[SF-08][OrderExecutor] submitted: order_id={order.id} "
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
            logger.error(f"[SF-09][OrderExecutor] failed: order_id={order.id} - {e}")
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

        accounts = await db_async(
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
                api_key_enc = _to_bytes(account.api_key_enc)
                api_secret_enc = _to_bytes(account.api_secret_enc)
                if not api_key_enc or not api_secret_enc:
                    logger.warning(
                        "Skipping adapter for %s (%s): empty API key or secret",
                        exchange,
                        account.label or "default",
                    )
                    continue

                api_key = fernet.decrypt(api_key_enc).decode()
                api_secret = fernet.decrypt(api_secret_enc).decode()

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
        live_session_id: Optional[str] = None,
    ) -> "Order":
        """异步写入 orders 表"""
        from apps.trading.models import Order, LiveSession

        @db_async
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
            if live_session_id:
                kwargs_create["live_session_id"] = live_session_id
                # 持久化触发策略快照：即使会话/策略后续被删除，详情仍可追溯
                triggered_strategy = None
                try:
                    ls = LiveSession.objects.select_related("strategy").get(
                        id=live_session_id
                    )
                    triggered_strategy = ls.strategy.name
                except (LiveSession.DoesNotExist, AttributeError):
                    triggered_strategy = None
                if triggered_strategy:
                    kwargs_create["triggered_strategy"] = triggered_strategy
            return Order.objects.create(**kwargs_create)

        return await _create()

    async def _update_order(self, order_id: str, **kwargs) -> None:
        """异步更新订单"""
        from apps.trading.models import Order

        @db_async
        def _update():
            Order.objects.filter(id=order_id).update(**kwargs)

        await _update()

    # ─── 已实现盈亏（平仓单）────────────────────────────────────────────────

    async def _compute_close_pnl(
        self,
        exchange_account_id: str,
        symbol: str,
        order_id: str,
        side: str,
        filled_quantity: Decimal,
        avg_fill_price: Decimal | None,
    ) -> Decimal | None:
        """查同账户+同品种成交历史，计算平仓单已实现盈亏（移动平均成本法）。"""
        from apps.trading.models import Order

        @db_async
        def _history():
            return list(
                Order.objects.filter(
                    exchange_account_id=exchange_account_id,
                    # 与 _persist_order 落库口径一致（统一大写）
                    symbol=symbol.upper(),
                    status="filled",
                    avg_fill_price__isnull=False,
                )
                .exclude(id=order_id)
                .order_by("created_at")
                .values("side", "filled_quantity", "avg_fill_price")
            )

        try:
            history = await _history()
        except Exception as e:
            logger.warning(
                f"[OrderExecutor] failed to load order history for pnl: {e}"
            )
            return None
        return compute_realized_pnl(
            history, side, filled_quantity, avg_fill_price
        )

    async def _apply_close_pnl(
        self,
        exchange_account_id: str,
        symbol: str,
        order_id: str,
        live_session_id: str | None,
        side: str,
        filled_quantity: Decimal,
        avg_fill_price: Decimal | None,
        previous_pnl: Decimal | None = None,
    ) -> Decimal | None:
        """平仓卖单成交后：计算已实现盈亏，并按增量更新会话权益。

        返回新的 realized_pnl（None 表示无法计算）；
        权益增量 = 新盈亏 - 旧盈亏（部分成交→完全成交时按全量重算，
        用增量修正权益，避免重复累加）。
        """
        pnl = await self._compute_close_pnl(
            exchange_account_id=exchange_account_id,
            symbol=symbol,
            order_id=order_id,
            side=side,
            filled_quantity=filled_quantity,
            avg_fill_price=avg_fill_price,
        )
        if pnl is None:
            return None
        delta = pnl - (previous_pnl or Decimal("0"))
        if live_session_id and delta != 0:
            await self._bump_session_equity(live_session_id, delta)
        return pnl

    async def _bump_session_equity(self, live_session_id: str, delta: Decimal) -> None:
        """会话权益 += 已实现盈亏增量。

        ponytail: current_equity 建会话时必然写入，这里不做 NULL 兜底。
        """
        from django.db.models import F

        from apps.trading.models import LiveSession

        try:
            @db_async
            def _bump():
                LiveSession.objects.filter(id=live_session_id).update(
                    current_equity=F("current_equity") + delta
                )

            await _bump()
        except Exception as e:
            logger.warning(
                f"[OrderExecutor] session equity update failed ({live_session_id}): {e}"
            )

    # ─── 成交同步（fill sync）───────────────────────────────────────────────

    # 交易所状态字符串 → 本地 Order.status 映射
    _EXCHANGE_STATUS_MAP = {
        "NEW": "submitted",
        "PARTIALLY_FILLED": "partial",
        "FILLED": "filled",
        "CANCELED": "cancelled",
        "EXPIRED": "cancelled",
        "EXPIRED_IN_MATCH": "cancelled",
        "REJECTED": "failed",
    }

    def _map_exchange_status(self, status: str) -> str:
        return self._EXCHANGE_STATUS_MAP.get(status, "submitted")

    def start_fill_sync(self, interval: float = 10.0) -> None:
        """启动成交状态后台同步任务（幂等，间隔默认 10s）。"""
        if self._fill_sync_task and not self._fill_sync_task.done():
            return
        try:
            self._fill_sync_task = asyncio.create_task(
                self._fill_sync_loop(interval)
            )
            logger.info(f"Fill sync started (interval={interval}s)")
        except RuntimeError as e:
            logger.warning(f"Fill sync not started (no running loop): {e}")

    async def _fill_sync_loop(self, interval: float) -> None:
        """后台循环：周期性同步所有活跃订单的成交状态。"""
        while True:
            try:
                await self._sync_active_orders()
            except Exception as e:
                logger.error(f"Fill sync cycle error: {e}")
            await asyncio.sleep(interval)

    async def _sync_active_orders(self) -> None:
        """查询所有活跃订单（pending/submitted/partial），
        从交易所拉取最新成交状态并落库。"""
        from apps.trading.models import Order

        orders = await db_async(
            lambda: list(
                Order.objects.filter(
                    status__in=["pending", "submitted", "partial"]
                ).select_related("exchange_account")
            )
        )()

        if not orders:
            return

        by_exchange: dict[str, list] = {}
        for order in orders:
            exchange = (order.exchange_account.exchange or "").lower()
            by_exchange.setdefault(exchange, []).append(order)

        for exchange, order_list in by_exchange.items():
            adapter = self._adapters.get(exchange)
            if not adapter:
                continue
            for order in order_list:
                if not order.exchange_order_id:
                    continue
                try:
                    fill = await adapter.fetch_order(
                        order.exchange_order_id, order.symbol
                    )
                except OrderNotFoundError as e:
                    await self._update_order(
                        order_id=str(order.id),
                        status="cancelled",
                        error_message=str(e),
                    )
                    logger.warning(
                        f"[FillSync] order {order.id} not found on exchange: {e}"
                    )
                    continue
                except Exception as e:
                    logger.warning(
                        f"[FillSync] failed to fetch order {order.id} "
                        f"({order.exchange_order_id}): {e}"
                    )
                    continue
                await self._apply_fill(order, fill)

    async def _apply_fill(self, order, fill) -> None:
        """把交易所返回的成交状态落库（仅在变化时更新）。"""
        updates: dict = {}
        if fill.status != order.status:
            updates["status"] = fill.status
        if fill.filled_quantity != order.filled_quantity:
            updates["filled_quantity"] = fill.filled_quantity
        if fill.avg_fill_price is not None and (
            order.avg_fill_price is None
            or fill.avg_fill_price != order.avg_fill_price
        ):
            updates["avg_fill_price"] = fill.avg_fill_price
        if fill.error_message and fill.error_message != order.error_message:
            updates["error_message"] = fill.error_message
        # 平仓卖单：成交状态变化时重算已实现盈亏（部分→完全成交按全量重算，
        # 权益按增量修正，避免重复累加）
        if (
            order.side == "sell"
            and fill.filled_quantity > 0
            and fill.avg_fill_price is not None
            and (
                fill.status != order.status
                or fill.filled_quantity != order.filled_quantity
            )
        ):
            try:
                pnl = await self._apply_close_pnl(
                    exchange_account_id=str(order.exchange_account_id),
                    symbol=order.symbol,
                    order_id=str(order.id),
                    live_session_id=(
                        str(order.live_session_id)
                        if order.live_session_id
                        else None
                    ),
                    side=order.side,
                    filled_quantity=fill.filled_quantity,
                    avg_fill_price=fill.avg_fill_price,
                    previous_pnl=order.realized_pnl,
                )
                if pnl is not None:
                    updates["realized_pnl"] = pnl
                    logger.info(
                        f"[FillSync] order {order.id} {order.symbol}: "
                        f"realized_pnl={pnl}"
                    )
            except Exception as e:
                logger.warning(
                    f"[FillSync] realized_pnl compute failed for {order.id}: {e}"
                )
        if updates:
            await self._update_order(order_id=str(order.id), **updates)
            logger.info(
                f"[FillSync] order {order.id} {order.symbol}: "
                f"{order.status} -> {fill.status}, filled={fill.filled_quantity}"
            )
