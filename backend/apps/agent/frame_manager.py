from __future__ import annotations
import asyncio
import logging
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class FrameState(str, Enum):
    STOPPED = 'stopped'
    STARTING = 'starting'
    RUNNING = 'running'
    STOPPING = 'stopping'


class FrameManager:
    """管理交易/辅助/回测框架的生命周期（懒加载，按需启动）"""

    _instance: Optional[FrameManager] = None

    def __init__(self):
        self._trading_state = FrameState.STOPPED
        self._assist_state = FrameState.STOPPED
        self._risk_guard_refs = 0  # 引用计数：trading+assist共享单实例

    @classmethod
    def get_instance(cls) -> FrameManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def status(self) -> dict:
        return {
            'trading': self._trading_state,
            'assist': self._assist_state,
            'risk_guard': 'running' if self._risk_guard_refs > 0 else 'stopped',
        }

    # --- Trading Frame ---

    async def start_trading_frame(self, mode: str = 'live') -> None:
        """mode: 'live' | 'paper'"""
        if self._trading_state == FrameState.RUNNING:
            logger.warning('[FrameManager] trading frame already running')
            return
        self._trading_state = FrameState.STARTING
        try:
            await self._start_data_feed()
            await self._start_risk_guard()
            await self._start_order_consumer(mode)
            self._trading_state = FrameState.RUNNING
            logger.info(f'[FrameManager] trading frame started (mode={mode})')
        except Exception as e:
            self._trading_state = FrameState.STOPPED
            raise

    async def stop_trading_frame(self) -> None:
        if self._trading_state != FrameState.RUNNING:
            return
        self._trading_state = FrameState.STOPPING
        await self._stop_order_consumer()
        await self._stop_risk_guard()
        await self._stop_data_feed()
        self._trading_state = FrameState.STOPPED
        logger.info('[FrameManager] trading frame stopped')

    # --- Assist Frame ---

    async def start_assist_frame(self) -> None:
        if self._assist_state == FrameState.RUNNING:
            return
        self._assist_state = FrameState.STARTING
        try:
            await self._start_data_feed()
            await self._start_risk_guard()
            await self._start_signal_monitor()
            self._assist_state = FrameState.RUNNING
            logger.info('[FrameManager] assist frame started')
        except Exception as e:
            self._assist_state = FrameState.STOPPED
            raise

    async def stop_assist_frame(self) -> None:
        if self._assist_state != FrameState.RUNNING:
            return
        self._assist_state = FrameState.STOPPING
        await self._stop_signal_monitor()
        await self._stop_risk_guard()
        await self._stop_data_feed()
        self._assist_state = FrameState.STOPPED
        logger.info('[FrameManager] assist frame stopped')

    async def stop_all(self) -> None:
        await self.stop_trading_frame()
        await self.stop_assist_frame()

    async def start(self, frame_type: str, mode: str = 'live') -> None:
        """统一启动入口（supervisor调用）"""
        if frame_type == 'trading':
            await self.start_trading_frame(mode=mode)
        elif frame_type == 'assist':
            await self.start_assist_frame()
        else:
            raise ValueError(f'Unknown frame type: {frame_type!r}')

    async def stop(self, frame_type: str) -> None:
        """统一停止入口（supervisor调用）"""
        if frame_type == 'trading':
            await self.stop_trading_frame()
        elif frame_type == 'assist':
            await self.stop_assist_frame()
        else:
            raise ValueError(f'Unknown frame type: {frame_type!r}')

    # --- Internal lifecycle methods (to be implemented) ---

    async def _start_data_feed(self) -> None:
        logger.info('[FrameManager] data feed starting...')
        # TODO: 启动WebSocket实时数据流

    async def _stop_data_feed(self) -> None:
        logger.info('[FrameManager] data feed stopped')

    async def _start_risk_guard(self) -> None:
        self._risk_guard_refs += 1
        if self._risk_guard_refs == 1:
            logger.info('[FrameManager] RiskGuard starting...')
            # TODO: 启动RiskGuard进程（gRPC）

    async def _stop_risk_guard(self) -> None:
        self._risk_guard_refs = max(0, self._risk_guard_refs - 1)
        if self._risk_guard_refs == 0:
            logger.info('[FrameManager] RiskGuard stopped')
            # TODO: 停止RiskGuard进程

    async def _start_order_consumer(self, mode: str) -> None:
        logger.info(f'[FrameManager] order consumer starting (mode={mode})...')
        # TODO: 启动Redis Stream消费者

    async def _stop_order_consumer(self) -> None:
        logger.info('[FrameManager] order consumer stopped')

    async def _start_signal_monitor(self) -> None:
        logger.info('[FrameManager] signal monitor starting...')
        # TODO: 启动信号监控

    async def _stop_signal_monitor(self) -> None:
        logger.info('[FrameManager] signal monitor stopped')
