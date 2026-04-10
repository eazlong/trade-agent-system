from __future__ import annotations
import logging
from enum import Enum
from typing import Optional

from django.conf import settings
import redis

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
        self._riskguard = None
        self._order_executor = None

        # 从 Redis 恢复持久化的框架状态
        self._restore_frame_states()

    @classmethod
    def get_instance(cls) -> FrameManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------ #
    #  Redis 持久化
    # ------------------------------------------------------------------ #

    def _get_redis(self) -> redis.Redis:
        """获取 Redis 连接（DB8，无 TTL）。"""
        url = settings.REDIS_URL
        db = getattr(settings, 'REDIS_DB_FRAME', 8)
        return redis.Redis.from_url(url, db=db, decode_responses=True)

    def _persist_frame_state(self) -> None:
        """将当前框架状态持久化到 Redis。"""
        try:
            r = self._get_redis()
            pipe = r.pipeline()
            pipe.set('frame:trading:state', self._trading_state.value)
            pipe.set('frame:assist:state', self._assist_state.value)
            pipe.set('frame:risk_guard_refs', self._risk_guard_refs)
            pipe.set('frame:order_executor', '1' if self._order_executor else '0')
            pipe.execute()
        except Exception as e:
            logger.warning('[FrameManager] persist state failed: %s', e)

    def _restore_frame_states(self) -> None:
        """从 Redis 恢复框架状态。"""
        try:
            r = self._get_redis()
            trading = r.get('frame:trading:state')
            assist = r.get('frame:assist:state')
            risk_refs = r.get('frame:risk_guard_refs')
            order_exec = r.get('frame:order_executor')

            if trading == FrameState.RUNNING.value:
                self._trading_state = FrameState.RUNNING
            elif trading == FrameState.STOPPED.value:
                self._trading_state = FrameState.STOPPED

            if assist == FrameState.RUNNING.value:
                self._assist_state = FrameState.RUNNING
            elif assist == FrameState.STOPPED.value:
                self._assist_state = FrameState.STOPPED

            if risk_refs is not None:
                self._risk_guard_refs = int(risk_refs)

            if order_exec == '1':
                # 标记需要恢复，但实际对象在 start 时重新初始化
                pass  # _order_executor 保持 None，start 时会重建

            if trading == FrameState.RUNNING.value or assist == FrameState.RUNNING.value:
                logger.info(
                    '[FrameManager] restored frame states: trading=%s, assist=%s',
                    self._trading_state.value, self._assist_state.value,
                )
        except Exception as e:
            logger.warning('[FrameManager] restore state failed: %s', e)

    async def restore_and_restart_frames(self) -> None:
        """系统启动时调用：检查持久化状态并自动重启运行中的框架。"""
        # 如果框架标记为 running，但底层组件未初始化，需要重启
        need_restart_trading = (self._trading_state == FrameState.RUNNING
                                and self._risk_guard_refs == 0)
        need_restart_assist = (self._assist_state == FrameState.RUNNING
                               and self._risk_guard_refs == 0)

        if need_restart_trading:
            logger.info('[FrameManager] trading frame was running before restart, auto-restarting...')
            try:
                await self.start_trading_frame(mode='live')
                logger.info('[FrameManager] trading frame auto-restarted')
            except Exception as e:
                logger.error('[FrameManager] trading frame auto-restart failed: %s', e)
                self._trading_state = FrameState.STOPPED

        if need_restart_assist:
            logger.info('[FrameManager] assist frame was running before restart, auto-restarting...')
            try:
                await self.start_assist_frame()
                logger.info('[FrameManager] assist frame auto-restarted')
            except Exception as e:
                logger.error('[FrameManager] assist frame auto-restart failed: %s', e)
                self._assist_state = FrameState.STOPPED

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def status(self) -> dict:
        return {
            'trading': self._trading_state,
            'assist': self._assist_state,
            'risk_guard': 'running' if self._risk_guard_refs > 0 else 'stopped',
            'order_executor': 'running' if self._order_executor is not None else 'stopped',
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
            await self._start_order_executor()
            await self._start_order_consumer(mode)
            self._trading_state = FrameState.RUNNING
            self._persist_frame_state()
            logger.info(f'[FrameManager] trading frame started (mode={mode})')
        except Exception as e:
            self._trading_state = FrameState.STOPPED
            self._persist_frame_state()
            raise

    async def stop_trading_frame(self) -> None:
        if self._trading_state != FrameState.RUNNING:
            return
        self._trading_state = FrameState.STOPPING
        await self._stop_order_consumer()
        await self._stop_order_executor()
        await self._stop_risk_guard()
        await self._stop_data_feed()
        self._trading_state = FrameState.STOPPED
        self._persist_frame_state()
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
            self._persist_frame_state()
            logger.info('[FrameManager] assist frame started')
        except Exception as e:
            self._assist_state = FrameState.STOPPED
            self._persist_frame_state()
            raise

    async def stop_assist_frame(self) -> None:
        if self._assist_state != FrameState.RUNNING:
            return
        self._assist_state = FrameState.STOPPING
        await self._stop_signal_monitor()
        await self._stop_risk_guard()
        await self._stop_data_feed()
        self._assist_state = FrameState.STOPPED
        self._persist_frame_state()
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
            from apps.riskguard.guard import RiskGuard
            self._riskguard = RiskGuard()
            await self._riskguard.start()
            logger.info('[FrameManager] RiskGuard started')
        self._persist_frame_state()

    async def _stop_risk_guard(self) -> None:
        self._risk_guard_refs = max(0, self._risk_guard_refs - 1)
        if self._risk_guard_refs == 0 and self._riskguard:
            await self._riskguard.stop()
            self._riskguard = None
            logger.info('[FrameManager] RiskGuard stopped')
        self._persist_frame_state()

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

    async def _start_order_executor(self) -> None:
        from apps.trading import OrderExecutor
        self._order_executor = OrderExecutor()
        await self._order_executor.initialize()
        logger.info('[FrameManager] OrderExecutor started')
        self._persist_frame_state()

    async def _stop_order_executor(self) -> None:
        if self._order_executor:
            await self._order_executor.shutdown()
            self._order_executor = None
            logger.info('[FrameManager] OrderExecutor stopped')
        self._persist_frame_state()
