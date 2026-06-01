"""
信号分发器

将策略信号写入 Redis Stream，由 OrderConsumer 消费。
同时记录 SignalTriggerLog 供审计。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import OrderSignal

logger = logging.getLogger(__name__)


class SignalDispatcher:
    """信号分发器：策略信号 → Redis Stream → OrderExecutor"""

    def __init__(self, live_session=None):
        """
        Args:
            live_session: LiveSession 实例（可为 None，用于关联订单）
        """
        self.live_session = live_session

    async def dispatch(
        self,
        signal: "OrderSignal",
        symbol: str,
        exchange_account_id: str,
        user_id: str | None = None,
        live_session_id: str | None = None,
    ) -> str:
        """
        分发策略信号到 Redis Stream。

        Args:
            signal: 订单信号
            symbol: 交易对符号
            exchange_account_id: 交易所账户 UUID
            user_id: 用户 UUID
            live_session_id: 实盘会话 UUID

        Returns:
            Redis Stream message_id
        """
        from apps.agent.bus import TRADING_ORDERS, publish

        payload = {
            "exchange": signal.exchange,
            "symbol": symbol,
            "side": signal.side,
            "order_type": signal.order_type,
            "quantity": str(signal.quantity),
            "price": str(signal.price) if signal.price else None,
            "exchange_account_id": exchange_account_id,
            "user_id": user_id,
            "signal_name": signal.signal_name,
            "metadata": signal.metadata,
        }

        if live_session_id:
            payload["live_session_id"] = live_session_id

        try:
            msg_id = await publish(TRADING_ORDERS, payload)
            logger.info(
                f"[SignalDispatcher] dispatched signal: {signal.signal_name} "
                f"{signal.side} {signal.quantity} {symbol} -> msg_id={msg_id}"
            )
            return msg_id
        except Exception as e:
            logger.error(f"[SignalDispatcher] failed to dispatch signal: {e}")
            raise

    async def dispatch_with_risk_check(
        self,
        signal: "OrderSignal",
        symbol: str,
        exchange_account_id: str,
        user_id: str | None = None,
        live_session_id: str | None = None,
    ) -> str | None:
        """
        带风控预检查的信号分发。

        Returns:
            message_id 或 None（被风控拒绝）
        """
        from apps.riskguard.guard import RiskGuard

        logger.info(f"[SignalDispatcher] dispatching {signal.signal_name} {symbol} side={signal.side} qty={signal.quantity}")

        riskguard = RiskGuard.get_instance()
        if riskguard:
            from apps.trading.adapters.base import OrderRequest

            request = OrderRequest(
                exchange=signal.exchange,
                symbol=symbol,
                side=signal.side,
                order_type=signal.order_type,
                quantity=signal.quantity,
                price=signal.price,
            )
            approved, reason = await riskguard.pre_trade_check(request, user_id)
            if not approved:
                logger.warning(
                    f"[SignalDispatcher] signal rejected by RiskGuard: {reason}"
                )
                return None
        else:
            logger.warning(
                "[SignalDispatcher] RiskGuard not available — dispatching signal without risk check"
            )

        return await self.dispatch(
            signal, symbol, exchange_account_id, user_id, live_session_id
        )
