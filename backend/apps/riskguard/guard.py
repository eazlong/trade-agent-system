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

import logging
from typing import TYPE_CHECKING, Tuple

if TYPE_CHECKING:
    from apps.trading.adapters.base import OrderRequest

logger = logging.getLogger(__name__)


class RiskGuard:
    """
    风控守卫（随交易框架或辅助框架启动）。
    """

    _instance: 'RiskGuard | None' = None

    def __init__(self, mode: str = 'trading'):
        self.mode = mode
        self._running = False

    @classmethod
    def get_instance(cls) -> 'RiskGuard | None':
        return cls._instance

    async def start(self) -> None:
        RiskGuard._instance = self
        self._running = True
        logger.info(f'RiskGuard started (mode={self.mode})')

    async def stop(self) -> None:
        self._running = False
        RiskGuard._instance = None
        logger.info('RiskGuard stopped')

    async def pre_trade_check(
        self, request: 'OrderRequest', user_id: str
    ) -> Tuple[bool, str]:
        """
        前置校验，返回 (approved, reason)。
        approved=False 时 OrderExecutor 禁止下单。

        Phase 1: 空实现，直接通过。
        后续完善：熔断器检查 → 日内交易次数 → 仓位上限 → 回撤限制
        """
        return True, 'OK'
