from __future__ import annotations
import logging
from decimal import Decimal
from enum import Enum
from typing import Optional

from asgiref.sync import sync_to_async
from django.conf import settings
import redis

logger = logging.getLogger(__name__)


class FrameState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"


class FrameManager:
    """管理交易/辅助/回测框架的生命周期（懒加载，按需启动）"""

    _instance: Optional[FrameManager] = None

    def __init__(self):
        self._trading_state = FrameState.STOPPED
        self._assist_state = FrameState.STOPPED
        self._risk_guard_refs = 0  # 引用计数：trading+assist共享单实例
        self._data_feed_refs = 0  # 引用计数：trading+assist共享数据源
        self._riskguard = None
        self._order_executor = None
        self._strategy_runner = None

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
        db = getattr(settings, "REDIS_DB_FRAME", 8)
        return redis.Redis.from_url(url, db=db, decode_responses=True)

    def _persist_frame_state(self) -> None:
        """将当前框架状态持久化到 Redis。"""
        try:
            r = self._get_redis()
            pipe = r.pipeline()
            pipe.set("frame:trading:state", self._trading_state.value)
            pipe.set("frame:assist:state", self._assist_state.value)
            pipe.set("frame:risk_guard_refs", self._risk_guard_refs)
            pipe.set("frame:data_feed_refs", self._data_feed_refs)
            pipe.set("frame:order_executor", "1" if self._order_executor else "0")
            pipe.execute()
        except Exception as e:
            logger.warning("[FrameManager] persist state failed: %s", e)

    def _restore_frame_states(self) -> None:
        """从 Redis 恢复框架状态。"""
        try:
            r = self._get_redis()
            trading = r.get("frame:trading:state")
            assist = r.get("frame:assist:state")
            risk_refs = r.get("frame:risk_guard_refs")
            data_feed_refs = r.get("frame:data_feed_refs")
            order_exec = r.get("frame:order_executor")

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

            if data_feed_refs is not None:
                self._data_feed_refs = int(data_feed_refs)

            if order_exec == "1":
                # OrderExecutor 无法跨进程复用，标记需要重建
                self._risk_guard_refs = 0
                self._data_feed_refs = 0
            elif order_exec == "0" and (
                trading == FrameState.RUNNING.value
                or assist == FrameState.RUNNING.value
            ):
                # 状态不一致：frame 标记为 running 但 executor 为 0
                # 说明上次 shutdown 只保存了部分状态，需要完全重启
                self._trading_state = FrameState.STOPPED
                self._assist_state = FrameState.STOPPED
                self._risk_guard_refs = 0
                self._data_feed_refs = 0

            if (
                trading == FrameState.RUNNING.value
                or assist == FrameState.RUNNING.value
            ):
                logger.info(
                    "[FrameManager] restored frame states: trading=%s, assist=%s",
                    self._trading_state.value,
                    self._assist_state.value,
                )
        except Exception as e:
            logger.warning("[FrameManager] restore state failed: %s", e)

    async def restore_and_restart_frames(self) -> None:
        """系统启动时调用：检查持久化状态并自动重启运行中的框架。"""
        # 如果框架标记为 running，但底层组件未初始化，需要重启
        need_restart_trading = (
            self._trading_state == FrameState.RUNNING and self._risk_guard_refs == 0
        )
        need_restart_assist = (
            self._assist_state == FrameState.RUNNING and self._risk_guard_refs == 0
        )

        if need_restart_trading:
            logger.info(
                "[FrameManager] trading frame was running before restart, auto-restarting..."
            )
            try:
                await self.start_trading_frame(mode="live")
                logger.info("[FrameManager] trading frame auto-restarted")
            except Exception as e:
                logger.error("[FrameManager] trading frame auto-restart failed: %s", e)
                self._trading_state = FrameState.STOPPED

        if need_restart_assist:
            logger.info(
                "[FrameManager] assist frame was running before restart, auto-restarting..."
            )
            try:
                await self.start_assist_frame()
                logger.info("[FrameManager] assist frame auto-restarted")
            except Exception as e:
                logger.error("[FrameManager] assist frame auto-restart failed: %s", e)
                self._assist_state = FrameState.STOPPED

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def status(self) -> dict:
        # 优先使用 Redis 持久化状态（进程重启后可恢复），
        # 不依赖进程内对象引用（进程重启后对象丢失）。
        try:
            r = self._get_redis()
            order_exec_persisted = r.get("frame:order_executor") == "1"
            risk_refs_persisted = int(r.get("frame:risk_guard_refs") or 0)
            data_feed_refs_persisted = int(r.get("frame:data_feed_refs") or 0)
        except Exception:
            order_exec_persisted = self._order_executor is not None
            risk_refs_persisted = self._risk_guard_refs
            data_feed_refs_persisted = self._data_feed_refs

        return {
            "trading": self._trading_state.value
            if hasattr(self._trading_state, "value")
            else self._trading_state,
            "assist": self._assist_state.value
            if hasattr(self._assist_state, "value")
            else self._assist_state,
            "risk_guard": "running"
            if self._risk_guard_refs > 0 or risk_refs_persisted > 0
            else "stopped",
            "order_executor": "running"
            if self._order_executor is not None or order_exec_persisted
            else "stopped",
            "data_feed": "running"
            if self._data_feed_refs > 0 or data_feed_refs_persisted > 0
            else "stopped",
        }

    # --- Trading Frame ---

    async def start_trading_frame(self, mode: str = "live") -> None:
        """mode: 'live' | 'paper'"""
        if self._trading_state == FrameState.RUNNING:
            logger.warning("[FrameManager] trading frame already running")
            return
        self._trading_state = FrameState.STARTING
        try:
            await self._start_data_feed()
            await self._start_risk_guard()
            await self._start_order_executor()
            await self._start_order_consumer(mode)
            self._trading_state = FrameState.RUNNING
            self._persist_frame_state()
            logger.info(f"[FrameManager] trading frame started (mode={mode})")
        except Exception:
            self._trading_state = FrameState.STOPPED
            self._persist_frame_state()
            raise

    async def stop_trading_frame(self) -> None:
        if self._trading_state != FrameState.RUNNING:
            return
        self._trading_state = FrameState.STOPPING
        await self.stop_strategy_runner()
        await self._stop_order_consumer()
        await self._stop_order_executor()
        await self._stop_risk_guard()
        await self._stop_data_feed()
        self._trading_state = FrameState.STOPPED
        self._persist_frame_state()
        logger.info("[FrameManager] trading frame stopped")

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
            logger.info("[FrameManager] assist frame started")
        except Exception:
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
        logger.info("[FrameManager] assist frame stopped")

    async def stop_all(self) -> None:
        await self.stop_trading_frame()
        await self.stop_assist_frame()

    async def start(self, frame_type: str, mode: str = "live") -> None:
        """统一启动入口（supervisor调用）"""
        if frame_type == "trading":
            await self.start_trading_frame(mode=mode)
        elif frame_type == "assist":
            await self.start_assist_frame()
        else:
            raise ValueError(f"Unknown frame type: {frame_type!r}")

    async def stop(self, frame_type: str) -> None:
        """统一停止入口（supervisor调用）"""
        if frame_type == "trading":
            await self.stop_trading_frame()
        elif frame_type == "assist":
            await self.stop_assist_frame()
        else:
            raise ValueError(f"Unknown frame type: {frame_type!r}")

    # --- Strategy Runner ---

    async def start_strategy_runner(
        self,
        strategy_name: str,
        symbol: str,
        timeframe: str,
        parameters: dict | None = None,
        exchange_account_id: str = "",
        user_id: str | None = None,
        live_session_id: str | None = None,
        initial_balance: Decimal = Decimal("0"),
    ) -> None:
        """启动策略运行器，在交易框架启动后调用。"""
        from apps.strategy_engine.runner import StrategyRunner

        if self._strategy_runner is not None:
            logger.warning(
                "[FrameManager] strategy runner already exists, stopping first"
            )
            await self.stop_strategy_runner()

        self._strategy_runner = StrategyRunner()
        await self._strategy_runner.start_live(
            strategy_name=strategy_name,
            symbol=symbol,
            timeframe=timeframe,
            parameters=parameters,
            exchange_account_id=exchange_account_id,
            user_id=user_id,
            live_session_id=live_session_id,
            initial_balance=initial_balance,
        )
        logger.info(
            "[FrameManager] strategy runner started: %s %s %s",
            strategy_name,
            symbol,
            timeframe,
        )

    async def stop_strategy_runner(self) -> None:
        """停止策略运行器。"""
        if self._strategy_runner is not None:
            await self._strategy_runner.stop_live()
            self._strategy_runner = None
            logger.info("[FrameManager] strategy runner stopped")

    # --- Internal lifecycle methods (to be implemented) ---

    async def _start_data_feed(self) -> None:
        """连接所有已注册的数据源 WebSocket，并订阅信号监控所需的 K 线流。"""
        self._data_feed_refs += 1
        if self._data_feed_refs > 1:
            # 已有其他 frame 启动了数据源，只需增加引用计数
            self._persist_frame_state()
            logger.info(
                "[FrameManager] data feed already running (refs=%d)",
                self._data_feed_refs,
            )
            return

        from apps.datasource.registry import DataSourceRegistry

        # 1. 加载并连接所有已注册的 crypto 数据源
        sources = DataSourceRegistry.list_registered()
        logger.info(
            "[FrameManager] starting data feed, registered sources: %s", sources
        )
        for source_name in sources:
            try:
                ds = DataSourceRegistry.get(source_name)
                if not ds.is_connected():
                    connected = await ds.connect_websocket()
                    if connected:
                        logger.info(
                            "[FrameManager] data source %s connected", source_name
                        )
                    else:
                        logger.warning(
                            "[FrameManager] data source %s connection failed",
                            source_name,
                        )
                else:
                    logger.info(
                        "[FrameManager] data source %s already connected", source_name
                    )

                # 输出当前运行状态
                status = ds.get_status()
                logger.info(
                    "[FrameManager] data source %s status: connected=%s, subs=%d, "
                    "last_data_age=%ss, market_types=%s, types=%s",
                    source_name,
                    status["connected"],
                    status["subscriptions"],
                    status.get("last_data_age_seconds"),
                    status["market_types"],
                    status["supported_data_types"],
                )
            except Exception as e:
                logger.warning(
                    "[FrameManager] failed to load data source %s: %s", source_name, e
                )

        # 2. 根据活跃信号监控自动订阅对应的 K 线数据
        try:
            monitors = await self._get_active_signal_monitors()
            logger.info(
                "[FrameManager] signal monitors to subscribe: %d", len(monitors)
            )
            for monitor in monitors:
                await self.subscribe_signal_klines(
                    symbol=monitor.symbol,
                    interval_str=monitor.interval or "1h",
                )
        except Exception as e:
            logger.warning("[FrameManager] signal monitor subscription failed: %s", e)

        self._persist_frame_state()
        # 输出所有数据源最终状态
        for source_name in DataSourceRegistry.list_registered():
            if DataSourceRegistry.is_loaded(source_name):
                ds = DataSourceRegistry.get(source_name)
                s = ds.get_status()
                logger.info(
                    "[FrameManager] data source %s final: connected=%s, subs=%d, "
                    "sub_keys=%s",
                    source_name,
                    s["connected"],
                    s["subscriptions"],
                    s["subscription_keys"],
                )
        logger.info("[FrameManager] data feed started")

    async def _stop_data_feed(self) -> None:
        """断开所有数据源 WebSocket 连接（引用计数为 0 时）。"""
        self._data_feed_refs = max(0, self._data_feed_refs - 1)
        if self._data_feed_refs > 0:
            # 还有其他 frame 在使用数据源，不断开
            self._persist_frame_state()
            logger.info(
                "[FrameManager] data feed refs=%d, keeping alive", self._data_feed_refs
            )
            return

        from apps.datasource.registry import DataSourceRegistry

        for source_name in DataSourceRegistry.list_registered():
            if DataSourceRegistry.is_loaded(source_name):
                try:
                    ds = DataSourceRegistry.get(source_name)
                    status = ds.get_status()
                    logger.info(
                        "[FrameManager] stopping data source %s: connected=%s, subs=%d, keys=%s",
                        source_name,
                        status["connected"],
                        status["subscriptions"],
                        status["subscription_keys"],
                    )
                    if ds.is_connected():
                        await ds.disconnect_websocket()
                        logger.info(
                            "[FrameManager] data source %s disconnected", source_name
                        )
                except Exception as e:
                    logger.warning(
                        "[FrameManager] failed to disconnect %s: %s", source_name, e
                    )

        self._persist_frame_state()
        logger.info("[FrameManager] data feed stopped")

    async def _get_active_signal_monitors(self) -> list:
        """获取活跃的信号监控列表。"""
        try:
            from apps.signal_monitor.models import SignalMonitor
            from django.utils import timezone

            @sync_to_async
            def _query():
                monitors = list(SignalMonitor.objects.filter(status="active"))
                now = timezone.now()
                return [m for m in monitors if not m.expires_at or m.expires_at > now]

            return await _query()
        except Exception as e:
            logger.warning(
                "[FrameManager] failed to get active signal monitors: %s: %s",
                type(e).__name__, e,
            )
            return []

    async def subscribe_signal_klines(
        self, symbol: str, interval_str: str = "1h"
    ) -> None:
        """为信号监控订阅指定 symbol 的 K 线 WebSocket 数据。

        可在实盘策略启动后调用，将新注册的 SignalMonitor symbol
        加入 WebSocket 实时回调，确保信号检查立即生效（而非仅依赖
        Celery Beat 30s 轮询兜底）。

        Args:
            symbol: 交易对，如 "BTC/USDT"
            interval_str: K 线周期，如 "1h", "15m"
        """
        from apps.datasource.registry import DataSourceRegistry
        from apps.datasource.base import DataType, KlineInterval, MarketType

        try:
            interval = KlineInterval(interval_str)
        except ValueError:
            interval = KlineInterval.H1
            logger.warning(
                "[FrameManager] unknown interval %s, defaulting to 1h", interval_str
            )

        registered = DataSourceRegistry.list_registered()
        if not registered:
            logger.warning("[FrameManager] no data sources registered, cannot subscribe kline")
            return

        for source_name in registered:
            ds = DataSourceRegistry.get(source_name)
            if not ds.is_connected():
                logger.warning(
                    "[FrameManager] data source %s not connected, skip kline subscribe",
                    source_name,
                )
                continue
            if DataType.KLINE not in ds.supported_data_types:
                logger.warning(
                    "[FrameManager] data source %s does not support KLINE, skip",
                    source_name,
                )
                continue
            await ds.subscribe(
                symbol=symbol,
                data_type=DataType.KLINE,
                interval=interval,
                market_type=MarketType.SPOT,
                callback=lambda data, sym=symbol: self._on_kline_data(data, sym),
            )
            logger.info(
                "[FrameManager] subscribed %s kline %s @%s for signal monitor",
                source_name,
                symbol,
                interval.value,
            )

    def _on_kline_data(self, kline: dict, symbol: str) -> None:
        """K 线数据回调：触发信号检查。"""
        try:
            from apps.signal_monitor.engine import SignalMonitorEngine

            engine = SignalMonitorEngine.get_instance()
            klines = engine._load_klines_for_monitors(
                [type("_M", (), {"symbol": symbol, "interval": "1h"})()]
            ).get(symbol, [])
            if len(klines) >= 2:
                engine.check_signals_for_kline(symbol, klines)
        except Exception as e:
            logger.warning("[FrameManager] kline callback error: %s", e)

    async def _start_risk_guard(self) -> None:
        self._risk_guard_refs += 1
        if self._risk_guard_refs == 1:
            from apps.riskguard.guard import RiskGuard

            self._riskguard = RiskGuard()
            await self._riskguard.start()
            logger.info("[FrameManager] RiskGuard started")
        self._persist_frame_state()

    async def _stop_risk_guard(self) -> None:
        self._risk_guard_refs = max(0, self._risk_guard_refs - 1)
        if self._risk_guard_refs == 0 and self._riskguard:
            await self._riskguard.stop()
            self._riskguard = None
            logger.info("[FrameManager] RiskGuard stopped")
        self._persist_frame_state()

    async def _start_order_consumer(self, mode: str) -> None:
        logger.info(f"[FrameManager] order consumer starting (mode={mode})...")
        # TODO: 启动Redis Stream消费者

    async def _stop_order_consumer(self) -> None:
        logger.info("[FrameManager] order consumer stopped")

    async def _start_signal_monitor(self) -> None:
        """启动信号监控。

        信号监控由 Celery Beat 每 30 秒定时驱动（check_signals 任务），
        同时 WebSocket 数据源的 K 线回调也会触发实时检查（_on_kline_data）。
        启动时重新为所有活跃监控器订阅 K 线数据。
        """
        monitors = await self._get_active_signal_monitors()
        active_count = len(monitors)
        logger.info(
            "[FrameManager] signal monitor started: %d active monitors", active_count
        )

        # 确保活跃的 signal monitor 都订阅了 WebSocket K 线数据
        for monitor in monitors:
            try:
                await self.subscribe_signal_klines(
                    symbol=monitor.symbol,
                    interval_str=monitor.interval or "1h",
                )
            except Exception as e:
                logger.warning(
                    "[FrameManager] signal monitor kline subscribe failed for %s: %s",
                    monitor.symbol, e
                )

    async def _stop_signal_monitor(self) -> None:
        logger.info("[FrameManager] signal monitor stopped")

    async def _start_order_executor(self) -> None:
        from apps.trading import OrderExecutor

        self._order_executor = OrderExecutor()
        await self._order_executor.initialize()
        logger.info("[FrameManager] OrderExecutor started")
        self._persist_frame_state()

    async def _stop_order_executor(self) -> None:
        if self._order_executor:
            await self._order_executor.shutdown()
            self._order_executor = None
            logger.info("[FrameManager] OrderExecutor stopped")
        self._persist_frame_state()
