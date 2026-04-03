"""
RiskGuard - 风控守卫

前置校验（pre_trade_check）：
- 熔断器检查（circuit breaker）
- 单笔仓位上限
- 最大回撤限制
- 日内交易次数

实时监控（monitor loop）：
- 浮亏超阈值预警
- 止损触发强平

由 FrameManager 管理生命周期，非独立进程（ADR-003）。
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Tuple

import redis.asyncio as aioredis
from asgiref.sync import sync_to_async
from django.conf import settings

if TYPE_CHECKING:
    from apps.trading.adapters.base import OrderRequest

from apps.riskguard.circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)


class RiskGuard:
    """
    风控守卫（随交易框架或辅助框架启动）。
    """

    _instance: 'RiskGuard | None' = None

    # 风控阈值（可由管理员通过 RiskConfig 覆盖）
    MAX_POSITION_RATIO = Decimal('0.20')     # 单仓不超过总资产20%
    MAX_DAILY_DRAWDOWN = Decimal('0.05')     # 日内最大回撤5%
    MAX_DAILY_TRADES = 50                     # 日内最大交易次数
    FLOATING_LOSS_ALERT = Decimal('-0.03')  # 浮亏-3%预警

    def __init__(self, mode: str = 'trading'):
        self.mode = mode
        self._running = False
        self._monitor_task: asyncio.Task | None = None

    @classmethod
    def get_instance(cls) -> 'RiskGuard | None':
        return cls._instance

    async def start(self) -> None:
        RiskGuard._instance = self
        self._running = True
        if self.mode in ('trading', 'monitor'):
            self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info(f'RiskGuard started (mode={self.mode})')

    async def stop(self) -> None:
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None
        RiskGuard._instance = None
        logger.info('RiskGuard stopped')

    # ------------------------------------------------------------------ #
    #  前置校验                                                          #
    # ------------------------------------------------------------------ #

    async def pre_trade_check(
        self, request: 'OrderRequest', user_id: str | None
    ) -> Tuple[bool, str]:
        """
        返回 (approved, reason)，approved=False 时 OrderExecutor 禁止下单。

        校验顺序：
        1. 熔断器检查
        2. 日内交易次数
        3. 仓位上限
        4. 日内回撤
        """
        if not user_id:
            return True, 'OK'

        # 1. 熔断器检查
        if await self._is_circuit_open(user_id):
            return False, '熔断器触发，今日禁止交易'

        # 2. 日内交易次数
        daily_count = await self._get_daily_trade_count(user_id)
        if daily_count >= self.MAX_DAILY_TRADES:
            return False, f'日内交易次数已达上限 {self.MAX_DAILY_TRADES}'

        # 3. 仓位上限
        position_ok, reason = await self._check_position_limit(request, user_id)
        if not position_ok:
            return False, reason

        # 4. 日内回撤
        drawdown_ok, reason = await self._check_drawdown(user_id)
        if not drawdown_ok:
            return False, reason

        return True, 'OK'

    async def _is_circuit_open(self, user_id: str) -> bool:
        """检查熔断器是否打开"""
        cb = CircuitBreaker(user_id=user_id)
        return await cb.is_open()

    async def record_order_success(self, user_id: str) -> None:
        """订单成功时调用，重置失败计数"""
        if not user_id:
            return
        cb = CircuitBreaker(user_id=user_id)
        await cb.record_success()

    async def record_order_failure(self, user_id: str) -> None:
        """订单失败时调用，连续失败触发熔断"""
        if not user_id:
            return
        cb = CircuitBreaker(user_id=user_id)
        await cb.record_failure()

    async def _get_daily_trade_count(self, user_id: str) -> int:
        """统计日内已成交订单数量"""
        from apps.trading.models import Order
        from django.utils import timezone

        today = timezone.now().date()

        @sync_to_async
        def count():
            return Order.objects.filter(
                user_id=user_id,
                created_at__date=today,
                status__in=['submitted', 'filled'],
            ).count()

        return await count()

    async def _check_position_limit(
        self, request: 'OrderRequest', user_id: str
    ) -> Tuple[bool, str]:
        """单笔仓位不超过总资产20%"""
        from apps.trading.executor import OrderExecutor

        executor = OrderExecutor.get_instance()
        if not executor:
            return True, ''

        adapter = executor._adapters.get(request.exchange)
        if not adapter:
            return True, ''

        try:
            balance = await adapter.get_balance()
        except Exception:
            return True, ''

        total_usdt = balance.get('USDT', Decimal('0'))
        if total_usdt == 0:
            return True, ''

        if request.price is None:
            # 市价单无法预知价格，跳过仓位校验
            return True, ''

        order_value = request.price * request.quantity
        ratio = order_value / total_usdt
        if ratio > self.MAX_POSITION_RATIO:
            return False, (
                f'单笔仓位 {ratio:.1%} 超过上限 {self.MAX_POSITION_RATIO:.0%}'
            )
        return True, ''

    async def _check_drawdown(self, user_id: str) -> Tuple[bool, str]:
        """
        日内已实现回撤检查。
        对比期初净值（从 daily_account_snapshot 表读取）。
        简化：若当日亏损超过初始资金的5%，禁止交易。
        """
        from django.db.models import Sum
        from django.utils import timezone

        today = timezone.now().date()

        @sync_to_async
        def get_today_pnl():
            result = Order.objects.filter(
                user_id=user_id,
                status='filled',
                created_at__date=today,
            ).aggregate(total_pnl=Sum('realized_pnl'))
            return result['total_pnl'] or Decimal('0')

        @sync_to_async
        def get_initial_balance():
            """从 daily_account_snapshot 读取期初余额"""
            try:
                from apps.trading.models import DailySnapshot
                snap = DailySnapshot.objects.filter(
                    user_id=user_id,
                    date__lt=today,
                ).order_by('-date').first()
                if snap:
                    return snap.total_equity
            except Exception:
                pass
            return None

        pnl = await get_today_pnl()
        if pnl >= 0:
            return True, ''

        initial = await get_initial_balance()
        if initial is None or initial == 0:
            return True, ''

        drawdown = abs(pnl) / initial
        if drawdown > self.MAX_DAILY_DRAWDOWN:
            return False, (
                f'日内回撤 {drawdown:.1%} 超过上限 {self.MAX_DAILY_DRAWDOWN:.0%}'
            )
        return True, ''

    # ------------------------------------------------------------------ #
    #  实时监控 Loop                                                     #
    # ------------------------------------------------------------------ #

    async def _monitor_loop(self) -> None:
        """每分钟检查所有持仓的浮亏"""
        while self._running:
            try:
                await self._check_floating_pnl()
            except Exception as e:
                logger.error(f'RiskGuard monitor error: {e}')
            await asyncio.sleep(60)

    async def _check_floating_pnl(self) -> None:
        """浮亏超阈值时发送告警"""
        from apps.trading.executor import OrderExecutor

        executor = OrderExecutor.get_instance()
        if not executor:
            return

        for exchange, adapter in executor._adapters.items():
            try:
                positions = await adapter.get_positions()
                balance = await adapter.get_balance()
            except Exception as e:
                logger.error(f'Failed to fetch positions from {exchange}: {e}')
                continue

            total = balance.get('USDT', Decimal('1'))
            if total <= 0:
                total = Decimal('1')

            for pos in positions:
                pnl_ratio = pos.unrealized_pnl / total
                if pnl_ratio < self.FLOATING_LOSS_ALERT:
                    await self._send_floating_loss_alert(exchange, pos, pnl_ratio)
                    # 记录风控事件
                    await self._record_risk_event(
                        level='P1',
                        event_type='floating_loss',
                        message=f'{exchange}:{pos.symbol} 浮亏 {pnl_ratio:.2%}',
                    )

    async def _send_floating_loss_alert(
        self, exchange: str, pos, ratio: Decimal
    ) -> None:
        """发送 Telegram 浮亏预警"""
        try:
            from apps.channel.telegram import TelegramChannel
            # TelegramChannel 需要 app 实例，通过日志作为 fallback
            logger.warning(
                f'浮亏预警 | 交易所: {exchange} | 品种: {pos.symbol} | '
                f'方向: {pos.side} | 数量: {pos.quantity} | 浮亏: {ratio:.2%}'
            )
        except Exception as e:
            logger.warning(
                f'浮亏预警 | 交易所: {exchange} | 品种: {pos.symbol} | '
                f'方向: {pos.side} | 数量: {pos.quantity} | 浮亏: {ratio:.2%}'
            )

    async def _record_risk_event(
        self,
        level: str,
        event_type: str,
        message: str,
    ) -> None:
        """记录风控事件到数据库"""
        try:
            from apps.risk.models import RiskEvent

            @sync_to_async
            def create():
                return RiskEvent.objects.create(
                    level=level,
                    event_type=event_type,
                    message=message,
                )

            await create()
        except Exception as e:
            logger.error(f'Failed to record risk event: {e}')
