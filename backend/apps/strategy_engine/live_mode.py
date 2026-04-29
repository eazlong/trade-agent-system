"""
实盘策略运行器

订阅实时 K 线 → 执行策略 on_bar → 分发信号到 Redis Stream。
由 FrameManager 在启动交易框架时加载。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from .base import BaseStrategy, OrderSignal

logger = logging.getLogger(__name__)


class LiveStrategyRunner:
    """
    实盘策略运行器：订阅 K 线 → 执行策略 → 分发信号

    生命周期：
    1. start() → 加载策略、订阅 K 线
    2. _on_kline() → 每根 K 线回调调用 strategy.on_bar()
    3. stop() → 停止策略、取消订阅
    """

    def __init__(
        self,
        strategy: "BaseStrategy",
        symbol: str,
        timeframe: str,
        exchange_account_id: str,
        user_id: str | None = None,
        live_session_id: str | None = None,
    ):
        self.strategy = strategy
        self.symbol = symbol
        self.timeframe = timeframe
        self.exchange_account_id = exchange_account_id
        self.user_id = user_id
        self.live_session_id = live_session_id
        self._running = False
        self._kline_history: list[dict] = []
        self._dispatcher = None

    async def start(self) -> None:
        """启动实盘策略运行"""
        if self._running:
            logger.warning(f"[LiveStrategyRunner] already running for {self.symbol}")
            return

        self._running = True
        self._kline_history = []
        self.strategy.on_start()

        # 初始化信号分发器
        from .signals import SignalDispatcher

        self._dispatcher = SignalDispatcher()

        # 订阅 K 线数据
        await self._subscribe_kline()

        logger.info(
            f"[LiveStrategyRunner] started: strategy={self.strategy.name} "
            f"symbol={self.symbol} tf={self.timeframe}"
        )

    async def stop(self) -> None:
        """停止实盘策略运行"""
        if not self._running:
            return

        self._running = False
        self.strategy.on_stop()
        await self._unsubscribe_kline()
        logger.info(f"[LiveStrategyRunner] stopped: {self.strategy.name}")

    async def on_kline(self, kline: dict) -> None:
        """
        K 线数据回调入口。
        由 DataFeed 收到新 K 线时调用。

        Args:
            kline: {open, high, low, close, volume, timestamp, ...}
        """
        if not self._running:
            return

        # 更新历史
        self._kline_history.append(kline)
        # 限制历史长度，避免内存无限增长
        max_history = 500
        if len(self._kline_history) > max_history:
            self._kline_history = self._kline_history[-max_history:]

        try:
            # 调用策略逻辑
            signal = self.strategy.on_bar(kline, self._kline_history)

            if signal:
                await self._dispatch_signal(signal)
        except Exception as e:
            logger.error(f"[LiveStrategyRunner] on_kline error: {e}", exc_info=True)

    async def _subscribe_kline(self) -> None:
        """订阅 K 线数据"""
        from apps.datasource.registry import DataSourceRegistry
        from apps.datasource.base import DataType, KlineInterval, MarketType

        try:
            interval = KlineInterval(self.timeframe)
        except ValueError:
            interval = KlineInterval.H1
            logger.warning(
                f"[LiveStrategyRunner] unknown timeframe {self.timeframe}, defaulting to 1h"
            )

        for source_name in DataSourceRegistry.list_registered():
            if not DataSourceRegistry.is_loaded(source_name):
                continue

            ds = DataSourceRegistry.get(source_name)
            if not ds.is_connected() or DataType.KLINE not in ds.supported_data_types:
                continue

            try:
                await ds.subscribe(
                    symbol=self.symbol,
                    data_type=DataType.KLINE,
                    interval=interval,
                    market_type=MarketType.SPOT,
                    callback=self.on_kline,
                )
                logger.info(
                    f"[LiveStrategyRunner] subscribed {source_name} kline "
                    f"{self.symbol} @{self.timeframe}"
                )
            except Exception as e:
                logger.warning(
                    f"[LiveStrategyRunner] failed to subscribe {source_name}: {e}"
                )

    async def _unsubscribe_kline(self) -> None:
        """取消 K 线订阅"""
        from apps.datasource.registry import DataSourceRegistry
        from apps.datasource.base import DataType, KlineInterval, MarketType

        try:
            interval = KlineInterval(self.timeframe)
        except ValueError:
            interval = KlineInterval.H1

        for source_name in DataSourceRegistry.list_registered():
            if not DataSourceRegistry.is_loaded(source_name):
                continue

            ds = DataSourceRegistry.get(source_name)
            try:
                await ds.unsubscribe(
                    symbol=self.symbol,
                    data_type=DataType.KLINE,
                    interval=interval,
                    market_type=MarketType.SPOT,
                    callback=self.on_kline,
                )
            except Exception:
                pass

    async def _dispatch_signal(self, signal: "OrderSignal") -> None:
        """分发策略信号到 Redis Stream"""
        if not self._dispatcher:
            return

        try:
            msg_id = await self._dispatcher.dispatch_with_risk_check(
                signal=signal,
                symbol=self.symbol,
                exchange_account_id=self.exchange_account_id,
                user_id=self.user_id,
                live_session_id=self.live_session_id,
            )
            if msg_id:
                logger.info(
                    f"[LiveStrategyRunner] signal dispatched: {signal.signal_name} "
                    f"msg_id={msg_id}"
                )
        except Exception as e:
            logger.error(
                f"[LiveStrategyRunner] failed to dispatch signal: {e}", exc_info=True
            )

    async def load_initial_history(self, limit: int = 200) -> None:
        """
        启动时加载历史 K 线数据，确保策略有足够的历史数据运行。
        从 DataSource 或外部 API 获取。
        """
        from apps.datasource.store import get_data_store

        try:
            store = get_data_store()
            klines = store.get_latest("kline", self.symbol, limit=limit)
            if klines:
                self._kline_history = klines
                logger.info(
                    f"[LiveStrategyRunner] loaded {len(klines)} historical klines "
                    f"for {self.symbol}"
                )
        except Exception as e:
            logger.warning(
                f"[LiveStrategyRunner] failed to load historical klines: {e}"
            )
