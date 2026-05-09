"""
实盘策略运行器

订阅实时 K 线 → 执行策略 on_bar → 分发信号到 Redis Stream。
由 FrameManager 在启动交易框架时加载。
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from .base import BaseStrategy, OrderSignal, PortfolioTarget

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
        self._validation_task: "asyncio.Task | None" = None

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

        # 注册策略的最小周期信号到 SignalMonitor
        await self._register_signal_monitors()

        # 启动策略验证事件监听（由 SignalMonitor 触发后消费）
        watch_signals = self.strategy.get_watch_signals()
        if watch_signals:
            self._validation_task = asyncio.create_task(self._listen_validation())

        logger.info(
            f"[LiveStrategyRunner] started: strategy={self.strategy.name} "
            f"symbol={self.symbol} tf={self.timeframe} "
            f"watch_signals={len(watch_signals)}"
        )

    async def stop(self) -> None:
        """停止实盘策略运行"""
        if not self._running:
            return

        self._running = False
        self.strategy.on_stop()
        await self._unsubscribe_kline()

        # 取消验证监听任务
        if self._validation_task and not self._validation_task.done():
            self._validation_task.cancel()
            try:
                await self._validation_task
            except asyncio.CancelledError:
                pass
            self._validation_task = None

        # 清理 SignalMonitor 记录
        await self._unregister_signal_monitors()

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
            # 同步当前价格到 context
            self.strategy.ctx.set_price(
                self.symbol, Decimal(str(kline.get("close", "0")))
            )

            # Phase 2: 5-step pipeline
            _ = self.strategy.select_universe()
            insights = self.strategy.generate_insights(kline, self._kline_history)
            targets = self.strategy.construct_portfolio(insights, self.strategy.ctx)
            safe_targets = self.strategy.apply_risk_filters(targets, self.strategy.ctx)
            for target in safe_targets:
                signal = self._target_to_order(target)
                if signal:
                    await self._dispatch_signal(signal)
                    estimated_price = Decimal(str(kline.get("close", "0")))
                    self._apply_signal_to_context(signal, estimated_price)
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

    def _apply_signal_to_context(
        self, signal: "OrderSignal", estimated_price: "Decimal"
    ) -> None:
        """乐观更新 ctx.position/balance（假设市价单立即按 estimated_price 成交）。

        这是 C4 修复：实盘模式下 ctx.position 持续追踪预期持仓。
        回测模式由 BacktestEngine._execute_buy/_execute_sell 同步更新。
        """
        if signal.side == "buy":
            cost = signal.quantity * estimated_price
            self.strategy.ctx.position += signal.quantity
            self.strategy.ctx.set_position(self.symbol, self.strategy.ctx.position)
            self.strategy.ctx.balance -= cost
        elif signal.side == "sell":
            proceeds = signal.quantity * estimated_price
            self.strategy.ctx.position -= signal.quantity
            self.strategy.ctx.set_position(self.symbol, self.strategy.ctx.position)
            self.strategy.ctx.balance += proceeds

        logger.debug(
            f"[LiveStrategyRunner] ctx updated: "
            f"position={self.strategy.ctx.position} "
            f"balance={self.strategy.ctx.balance}"
        )

    def _target_to_order(self, target: "PortfolioTarget") -> "OrderSignal | None":
        """将目标持仓差量转化为订单信号"""
        diff = target.target_quantity - self.strategy.ctx.position
        if diff == 0:
            return None
        if diff > 0:
            return self.strategy.ctx.buy(diff, signal_name=target.reason)
        else:
            return self.strategy.ctx.sell(abs(diff), signal_name=target.reason)

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

    async def _register_signal_monitors(self) -> None:
        """将策略的 get_watch_signals() 注册到 SignalMonitor 数据库。"""
        watch_signals = self.strategy.get_watch_signals()
        if not watch_signals:
            return

        if not self.user_id:
            logger.warning(
                "[LiveStrategyRunner] cannot register signal monitors: no user_id"
            )
            return

        from asgiref.sync import sync_to_async
        from apps.signal_monitor.models import SignalMonitor

        @sync_to_async
        def _create_monitor(ws: dict) -> None:
            SignalMonitor.objects.update_or_create(
                strategy_name=self.strategy.name,
                live_session_id=self.live_session_id or "",
                symbol=self.symbol,
                interval=ws.get("interval", self.timeframe),
                defaults={
                    "name": f"{self.strategy.name}:{ws.get('indicator_type', '')}",
                    "user_id": self.user_id,
                    "indicator_type": ws["indicator_type"],
                    "indicator_params": ws.get("indicator_params", {}),
                    "condition": ws["condition"],
                    "trigger_type": ws.get("trigger_type", "continuous"),
                    "action_type": "validate_strategy",
                    "status": "active",
                },
            )

        subscribed: set[tuple[str, str]] = set()

        for ws in watch_signals:
            try:
                await _create_monitor(ws)
                logger.info(
                    "[LiveStrategyRunner] registered signal monitor: %s %s %s",
                    self.strategy.name,
                    ws.get("indicator_type"),
                    ws.get("interval"),
                )

                # 为新增的监控订阅 WebSocket K 线回调，确保实时信号检查生效
                interval_str = ws.get("interval", self.timeframe)
                key = (self.symbol, interval_str)
                if key not in subscribed:
                    subscribed.add(key)
                    from apps.agent.frame_manager import FrameManager

                    fm = FrameManager.get_instance()
                    await fm.subscribe_signal_klines(
                        symbol=self.symbol, interval_str=interval_str
                    )
            except Exception as e:
                logger.error(
                    "[LiveStrategyRunner] failed to register signal monitor: %s",
                    e,
                )

    async def _unregister_signal_monitors(self) -> None:
        """清理该实盘会话的 SignalMonitor 记录。"""
        if not self.live_session_id:
            return

        from asgiref.sync import sync_to_async
        from apps.signal_monitor.models import SignalMonitor

        @sync_to_async
        def _cleanup():
            SignalMonitor.objects.filter(
                live_session_id=self.live_session_id,
                strategy_name=self.strategy.name,
            ).update(status="expired")

        try:
            await _cleanup()
            logger.info(
                "[LiveStrategyRunner] unregistered signal monitors for %s/%s",
                self.strategy.name,
                self.live_session_id,
            )
        except Exception as e:
            logger.error(
                "[LiveStrategyRunner] failed to unregister signal monitors: %s", e
            )

    async def _listen_validation(self) -> None:
        """后台监听 Redis List，消费 SignalMonitor 发布的策略验证事件。"""
        import json

        import redis.asyncio as aioredis
        from django.conf import settings

        key = f"strategy:validate:{self.live_session_id}"
        r = None

        try:
            r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
            while self._running:
                try:
                    result = await r.blpop(key, timeout=5)
                    if result is None:
                        continue
                    _list_key, data = result
                    event = json.loads(data)
                    await self._on_validate_trigger(event)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(
                        "[LiveStrategyRunner] validation listener error: %s",
                        e,
                        exc_info=True,
                    )
                    await asyncio.sleep(1)
        finally:
            if r is not None:
                await r.aclose()

    async def _on_validate_trigger(self, event: dict) -> None:
        """SignalMonitor 触发后，运行完整策略验证。

        Args:
            event: {
                monitor_id, strategy_name, symbol, interval, trigger_value
            }
        """
        logger.info(
            "[LiveStrategyRunner] validate trigger received: %s %s",
            event.get("strategy_name"),
            event.get("symbol"),
        )

        try:
            # 确保有足够的历史数据
            if len(self._kline_history) < 10:
                await self.load_initial_history(limit=200)
                if len(self._kline_history) < 10:
                    logger.warning(
                        "[LiveStrategyRunner] insufficient kline history for validation"
                    )
                    return

            # 用最新 K 线运行完整策略逻辑 (Phase 2 pipeline)
            latest_kline = self._kline_history[-1]
            self.strategy.ctx.set_price(
                self.symbol, Decimal(str(latest_kline.get("close", "0")))
            )
            _ = self.strategy.select_universe()
            insights = self.strategy.generate_insights(
                latest_kline, self._kline_history
            )
            targets = self.strategy.construct_portfolio(insights, self.strategy.ctx)
            safe_targets = self.strategy.apply_risk_filters(targets, self.strategy.ctx)
            for target in safe_targets:
                signal = self._target_to_order(target)
                if signal:
                    await self._dispatch_signal(signal)
                    estimated_price = Decimal(str(latest_kline.get("close", "0")))
                    self._apply_signal_to_context(signal, estimated_price)
                    logger.info(
                        "[LiveStrategyRunner] strategy confirmed signal: %s %s",
                        signal.signal_name,
                        signal.side,
                    )
                else:
                    logger.info(
                        "[LiveStrategyRunner] strategy rejected signal after full validation"
                    )
        except Exception as e:
            logger.error(
                "[LiveStrategyRunner] validate trigger error: %s", e, exc_info=True
            )
