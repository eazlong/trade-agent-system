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
        # 防止同方向重复开仓：记录每个 symbol 最近的入场方向
        # "buy" | "sell" | None，平仓（target_qty=0）时清除
        self._entry_direction: dict[str, str] = {}
        # per-session 并发锁：信号处理中时拒绝新的 kline 触发
        self._signal_lock = asyncio.Lock()

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

        # 预加载历史 K 线（warmup），让指标能立即产出有效值
        await self.load_initial_history(limit=self.strategy.min_kline_length)

        # 从交易所同步当前持仓到 ctx
        await self._sync_position_from_exchange()

        # 用最新历史收盘价初始化 ctx.price，避免第一根实时 K 线到达前
        # 策略拿到的 price 为 0（select_universe / generate_insights 可能依赖）
        if self._kline_history:
            try:
                last_close = self._kline_history[-1].get("close")
                if last_close is not None:
                    self.strategy.ctx.set_price(
                        self.symbol, Decimal(str(last_close))
                    )
            except Exception as e:
                logger.warning(
                    f"[LiveStrategyRunner] failed to seed ctx.price from history: {e}"
                )

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
            f"watch_signals={len(watch_signals)} "
            f"exchange_account={self.exchange_account_id} "
            f"user={self.user_id} live_session={self.live_session_id}"
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

        # 过滤：DataSource 按 DataType 广播，需按 symbol 二次过滤（防御层，
        # 精确分发已保证同 (symbol, interval) 的回调只触发一次）
        # kline["symbol"] 可能是 binance 原始格式 (DOGEUSDT) 或 ccxt 格式 (DOGE/USDT)
        kline_symbol = kline.get("symbol")
        if kline_symbol:
            norm_kline = kline_symbol.replace("/", "").upper()
            norm_self = self.symbol.replace("/", "").upper()
            if norm_kline != norm_self:
                logger.debug(
                    f"[SF-01][LiveStrategy] drop mismatched kline: "
                    f"expected={self.symbol} got={kline_symbol} close={kline.get('close')}"
                )
                return

        logger.info(f"[SF-01][LiveStrategy] on_kline: {self.symbol} close={kline.get('close')}")

        # 更新历史：同一根 K 线（相同 open_time）更新最后一条，新周期才 append
        kline_ts = kline.get("timestamp")
        if (
            self._kline_history
            and kline_ts is not None
            and self._kline_history[-1].get("timestamp") == kline_ts
        ):
            self._kline_history[-1] = kline
        else:
            self._kline_history.append(kline)
        # 限制历史长度，避免内存无限增长
        max_history = 500
        if len(self._kline_history) > max_history:
            self._kline_history = self._kline_history[-max_history:]

        try:
            # per-session 并发控制：上一轮信号仍在处理时，丢弃本次 kline
            if self._signal_lock.locked():
                logger.debug(
                    f"[SF-01][LiveStrategy] skip kline {self.symbol}: "
                    f"previous signal still processing"
                )
                return
            async with self._signal_lock:
                await self._process_kline(kline)
        except Exception as e:
            logger.error(f"[LiveStrategyRunner] on_kline error: {e}", exc_info=True)

    async def _process_kline(self, kline: dict) -> None:
        """单根 kline 的策略管线（在 _signal_lock 保护下执行）。"""
        # 同步当前价格到 context
        self.strategy.ctx.set_price(
            self.symbol, Decimal(str(kline.get("close", "0")))
        )

        # 调试：记录当前持仓状态
        logger.debug(
            f"[LiveStrategyRunner] _process_kline start: ctx.position={self.strategy.ctx.position}, "
            f"ctx._position={self.strategy.ctx._position}, "
            f"ctx._positions={self.strategy.ctx._positions}"
        )

        # Phase 2: 5-step pipeline with diagnostic logging
        universe = self.strategy.select_universe()
        insights = self.strategy.generate_insights(kline, self._kline_history)
        logger.debug(
            f"[LiveStrategyRunner] pipeline: universe={universe} "
            f"insights={len(insights)} "
            f"insight_details={[f'{i.direction} {i.symbol} conf={i.confidence}' for i in insights]}"
        )

        # 调试：构造 portfolio 前记录 context
        logger.debug(
            f"[LiveStrategyRunner] before construct_portfolio: context.position={self.strategy.ctx.position}"
        )
        targets = self.strategy.construct_portfolio(insights, self.strategy.ctx)
        logger.debug(
            f"[LiveStrategyRunner] portfolio: targets={len(targets)} "
            f"target_details={[f'{t.symbol} qty={t.target_quantity} reason={t.reason}' for t in targets]}"
        )

        safe_targets = self.strategy.apply_risk_filters(targets, self.strategy.ctx)
        filtered_count = len(targets) - len(safe_targets)
        if filtered_count > 0:
            logger.warning(
                f"[LiveStrategyRunner] risk filter: {filtered_count}/{len(targets)} targets rejected"
            )
        logger.debug(
            f"[LiveStrategyRunner] risk_passed: safe_targets={len(safe_targets)}"
        )

        signal_count = await self._dispatch_targets(safe_targets, kline)

        if signal_count == 0 and (insights or targets):
            logger.debug(
                f"[SF-02][LiveStrategyRunner] no signals dispatched this bar "
                f"(insights={len(insights)} targets={len(targets)} safe={len(safe_targets)})"
            )
        elif signal_count > 0:
            logger.info(
                f"[SF-02][LiveStrategyRunner] pipeline done: "
                f"universe={len(universe)} insights={len(insights)} "
                f"targets={len(targets)} safe={len(safe_targets)} signals={signal_count}"
            )

    async def _sync_position_from_exchange(self) -> None:
        """从交易所同步当前持仓到 ctx。

        启动时调用，确保策略知道实际持仓，避免重复开仓。
        """
        try:
            from apps.trading.executor import OrderExecutor

            executor = OrderExecutor.get_instance()
            if not executor:
                logger.warning(
                    f"[LiveStrategyRunner] OrderExecutor not ready, "
                    f"position sync skipped for {self.symbol}"
                )
                return

            adapter = executor._adapters.get("binance")
            if not adapter:
                logger.warning(
                    f"[LiveStrategyRunner] Binance adapter not found, "
                    f"position sync skipped for {self.symbol}"
                )
                return

            positions = await adapter.get_positions()
            symbol_norm = self.symbol.replace("/", "").upper()

            for pos in positions:
                pos_symbol = pos.symbol.replace("/", "").upper()
                if pos_symbol == symbol_norm:
                    actual_qty = pos.quantity if pos.side == "long" else -pos.quantity
                    self.strategy.ctx.set_position(self.symbol, actual_qty)
                    logger.info(
                        f"[LiveStrategyRunner] position synced from exchange: "
                        f"{self.symbol} = {actual_qty}"
                    )
                    return

            # 没有找到持仓，确认为 0
            self.strategy.ctx.set_position(self.symbol, Decimal("0"))
            logger.info(
                f"[LiveStrategyRunner] position synced from exchange: {self.symbol} = 0"
            )
        except Exception as e:
            logger.warning(
                f"[LiveStrategyRunner] failed to sync position from exchange: {e}"
            )

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

        registered = DataSourceRegistry.list_registered()
        logger.info(
            f"[LiveStrategyRunner] _subscribe_kline: "
            f"symbol={self.symbol} tf={self.timeframe} "
            f"registered_sources={registered}"
        )
        if not registered:
            logger.error(
                "[LiveStrategyRunner] NO data sources registered! "
                "K-line subscription will be empty. "
                "Check that a DataSource (e.g. BinanceDataSource) is properly registered."
            )

        subscribed_count = 0
        for source_name in registered:
            # Trigger lazy loading if not yet loaded
            if not DataSourceRegistry.is_loaded(source_name):
                logger.info(
                    f"[LiveStrategyRunner] source '{source_name}' not yet loaded, triggering lazy load via get()"
                )

            ds = DataSourceRegistry.get(source_name)

            if not DataSourceRegistry.is_loaded(source_name):
                logger.error(
                    f"[LiveStrategyRunner] source '{source_name}' failed to load (get() returned but not in registry instances)"
                )
                continue

            if not ds.is_connected():
                logger.warning(
                    f"[LiveStrategyRunner] source '{source_name}' NOT connected — skipping"
                )
                continue

            if DataType.KLINE not in ds.supported_data_types:
                logger.warning(
                    f"[LiveStrategyRunner] source '{source_name}' does not support KLINE "
                    f"(supported: {ds.supported_data_types}) — skipping"
                )
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
                subscribed_count += 1
            except Exception as e:
                logger.warning(
                    f"[LiveStrategyRunner] failed to subscribe {source_name}: {e}",
                    exc_info=True,
                )

        if subscribed_count == 0:
            logger.error(
                f"[LiveStrategyRunner] SUBSCRIPTION FAILED: 0 sources subscribed for "
                f"{self.symbol}@{self.timeframe}. Live trading will NOT receive kline data."
            )
        else:
            logger.info(
                f"[LiveStrategyRunner] subscription complete: {subscribed_count} source(s) active"
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
    async def _dispatch_signal(self, signal: "OrderSignal") -> bool:
        """分发策略信号到 Redis Stream，返回是否成功分发。"""
        if not self._dispatcher:
            logger.error(
                "[LiveStrategyRunner] _dispatcher is None! Signal will be dropped. "
                "This means SignalDispatcher was not initialized in start()."
            )
            return False

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
                return True
            else:
                logger.warning(
                    f"[LiveStrategyRunner] signal REJECTED or dropped by risk check: "
                    f"{signal.signal_name} {signal.side} {signal.quantity} {self.symbol}"
                )
                return False
        except Exception as e:
            logger.error(
                f"[LiveStrategyRunner] failed to dispatch signal: {e}", exc_info=True
            )
            return False

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
            f"[SF-11][LiveStrategyRunner] ctx updated: "
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

    async def _dispatch_targets(
        self, targets: "list[PortfolioTarget]", kline: dict
    ) -> int:
        """遍历 safe_targets，过滤重复入场后分发信号。返回实际分发数量。

        防重复规则：同一 symbol 已有同方向入场记录时，若 target 仍要求同向加仓
        （target_quantity > ctx.position），跳过该 target。平仓（target_quantity=0）
        时清除方向记录，允许反向入场。
        """
        count = 0
        for target in targets:
            direction = (
                "buy" if target.target_quantity > self.strategy.ctx.position
                else "sell"
            )
            prev = self._entry_direction.get(target.symbol)
            if prev is not None and direction == prev and target.target_quantity > 0:
                logger.debug(
                    f"[SF-02][LiveStrategyRunner] skip repeated {direction} "
                    f"for {target.symbol} (target={target.target_quantity} "
                    f"position={self.strategy.ctx.position})"
                )
                continue

            signal = self._target_to_order(target)
            if signal is None:
                logger.debug(
                    f"[SF-02][LiveStrategyRunner] _target_to_order returned None for "
                    f"target {target.symbol} (diff=0, no action needed)"
                )
                continue
            logger.info(
                f"[SF-03][LiveStrategyRunner] signal generated: "
                f"{signal.signal_name} {signal.side} qty={signal.quantity} "
                f"symbol={self.symbol}"
            )
            dispatched = await self._dispatch_signal(signal)
            if dispatched:
                estimated_price = Decimal(str(kline.get("close", "0")))
                self._apply_signal_to_context(signal, estimated_price)
                if target.target_quantity <= 0:
                    self._entry_direction.pop(target.symbol, None)
                else:
                    self._entry_direction[target.symbol] = signal.side
                count += 1
            else:
                logger.debug(
                    f"[SF-02][LiveStrategyRunner] signal rejected, ctx not updated: "
                    f"{signal.signal_name} {signal.side} {signal.quantity}"
                )
        return count

    async def load_initial_history(self, limit: int = 500) -> None:
        """
        启动时加载历史 K 线数据，确保策略有足够的历史数据运行。

        优先从 DataSource REST API 直接拉取交易所最新 K 线（最可靠，
        不依赖本地缓存），失败时降级到 DataStore 本地存储。
        """
        # 1) 优先：直接从数据源 REST API 拉取
        try:
            klines = await self._fetch_klines_from_source(limit=limit)
            if klines:
                self._kline_history = klines
                logger.info(
                    f"[LiveStrategyRunner] pulled {len(klines)} historical klines "
                    f"for {self.symbol} via DataSource REST"
                )
                return
        except Exception as e:
            logger.warning(
                f"[LiveStrategyRunner] REST kline fetch failed: {e}, "
                f"falling back to DataStore"
            )

        # 2) 降级：本地 DataStore
        try:
            from apps.datasource.store import get_data_store

            store = get_data_store()
            klines = store.get_latest("kline", self.symbol, limit=limit)
            if klines:
                self._kline_history = klines
                logger.info(
                    f"[LiveStrategyRunner] loaded {len(klines)} historical klines "
                    f"for {self.symbol} from DataStore"
                )
            else:
                logger.warning(
                    f"[LiveStrategyRunner] no historical klines available for "
                    f"{self.symbol} (requested {limit}). Indicators may be invalid "
                    f"until history accumulates."
                )
        except Exception as e:
            logger.warning(
                f"[LiveStrategyRunner] failed to load historical klines: {e}"
            )

    async def _fetch_klines_from_source(self, limit: int) -> list[dict]:
        """遍历已注册且已连接的 DataSource，返回最新 limit 根 K 线。"""
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
            if not ds.is_connected():
                continue
            if DataType.KLINE not in ds.supported_data_types:
                continue

            try:
                klines = await ds.fetch_klines(
                    symbol=self.symbol,
                    interval=interval,
                    market_type=MarketType.SPOT,
                    limit=limit,
                )
                if klines:
                    # 按时间升序，便于 append/update 逻辑一致
                    klines.sort(key=lambda k: k.get("timestamp", 0))
                    logger.info(
                        f"[LiveStrategyRunner] source '{source_name}' provided "
                        f"{len(klines)} historical klines"
                    )
                    return klines
            except Exception as e:
                logger.warning(
                    f"[LiveStrategyRunner] source '{source_name}' fetch_klines "
                    f"failed: {e}"
                )
        return []

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
            "[SF-10][LiveStrategyRunner] validate trigger received: %s %s",
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
            dispatched = await self._dispatch_targets(safe_targets, latest_kline)
            if dispatched == 0:
                logger.info(
                    "[LiveStrategyRunner] strategy rejected signal after full validation"
                )
        except Exception as e:
            logger.error(
                "[LiveStrategyRunner] validate trigger error: %s", e, exc_info=True
            )
