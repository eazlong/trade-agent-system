"""
Celery tasks for backtest execution.

异步回测任务，供 BacktestAgent 和 API 调用。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

from celery_app import app

logger = logging.getLogger(__name__)


@app.task(bind=True, max_retries=2, acks_late=True, track_started=True)
def run_backtest_task(
    self,
    strategy_name: str,
    symbol: str,
    timeframe: str,
    initial_capital: float = 10000,
    commission_rate: float = 0.001,
    parameters: dict | None = None,
    strategy_id: str | None = None,
    exchange: str = "binance",
    start_date: str = "",
    end_date: str = "",
    user_id: str | None = None,
    result_id: str | None = None,
    benchmark: str = "",
) -> dict:
    """
    异步执行策略回测。

    Args:
        strategy_name: 策略名称（StrategyRegistry 注册名）
        symbol: 交易对
        timeframe: K 线周期
        initial_capital: 初始资金
        commission_rate: 手续费率
        parameters: 策略参数
        strategy_id: Strategy 模型 UUID（用于关联回测结果）
        exchange: 交易所
        start_date: 回测开始日期（ISO 格式，如 2024-01-01），空字符串表示不限
        end_date: 回测结束日期（ISO 格式，如 2024-12-31），空字符串表示不限
        user_id: 发起回测的用户 ID
        result_id: BacktestResult UUID（如果已提前创建占位记录）

    Returns:
        回测统计结果
    """
    from apps.agent.task_tracker import TaskTracker, tracker_context
    from apps.strategy_engine.registry import StrategyRegistry
    from apps.strategy_engine.backtest_mode import _resolve_strategy_name
    from apps.strategy_engine.runner import StrategyRunner

    # Ensure strategy path is set before discovery
    if not StrategyRegistry._strategy_path:
        StrategyRegistry.set_strategy_path('/root/.tradelogx/strategies')

    # Re-discover strategies to include any added after worker startup
    StrategyRegistry.discover()

    # Resolve strategy name to canonical registered name
    strategy_name = _resolve_strategy_name(strategy_name)

    # Setup tracker for progress monitoring
    tracker = TaskTracker(
        task_id=self.request.id, user_id=user_id or "", task_type="backtest"
    )
    token = tracker_context.set(tracker)
    tracker.start(f"开始回测：{symbol} {timeframe}")

    logger.info(
        f"[BacktestTask] running: strategy={strategy_name} symbol={symbol} "
        f"tf={timeframe} exchange={exchange}"
    )

    # Create placeholder BacktestResult so the UI shows the task immediately.
    # If result_id was already provided (e.g. from the REST create endpoint),
    # skip creating a duplicate.
    if not result_id:
        try:
            from apps.strategy_engine.backtest_mode import create_empty_result
            from apps.strategy_engine.backtest_mode import (
                _resolve_strategy_id as _resolve_sid,
            )
            import asyncio as _aio

            _sid = strategy_id or _aio.run(_resolve_sid(strategy_name))
            if _sid:
                _s = start_date or (date.today() - timedelta(days=30)).isoformat()
                _e = end_date or date.today().isoformat()
                result_id = create_empty_result(
                    strategy_id=_sid,
                    symbol=symbol,
                    timeframe=timeframe,
                    start_date=_s,
                    end_date=_e,
                    initial_capital=initial_capital,
                    parameters=parameters or {},
                    user_id=user_id,
                )
                strategy_id = _sid
                logger.info(
                    f"[BacktestTask] created placeholder result_id=%s", result_id
                )
        except Exception:
            logger.warning(
                "[BacktestTask] failed to create placeholder result", exc_info=True
            )

    try:
        # 1. 获取历史 OHLCV 数据
        tracker.milestone("正在获取历史K线数据...", progress=0.1)
        self.update_state(
            state="STARTED", meta={"step": "fetching_ohlcv", "symbol": symbol}
        )
        ohlcv_data = _fetch_ohlcv_sync(
            symbol, timeframe, exchange, start_date=start_date, end_date=end_date
        )
        if not ohlcv_data:
            tracker.fail(f"未能获取 {symbol} {timeframe} 的历史K线数据")
            raise ValueError(f"未能获取 {symbol} {timeframe} 的历史K线数据")

        tracker.milestone(f"已获取 {len(ohlcv_data)} 条K线数据，开始回测", progress=0.3)
        logger.info(f"[BacktestTask] OHLCV data fetched: {len(ohlcv_data)} bars")

        # 2. 执行回测
        tracker.milestone("正在执行策略回测...", progress=0.5)
        self.update_state(
            state="STARTED",
            meta={"step": "running_backtest", "bars": len(ohlcv_data)},
        )
        runner = StrategyRunner()

        loop = asyncio.new_event_loop()
        try:
            stats = loop.run_until_complete(
                runner.run_backtest(
                    strategy_name=strategy_name,
                    symbol=symbol,
                    timeframe=timeframe,
                    ohlcv_data=ohlcv_data,
                    initial_capital=Decimal(str(initial_capital)),
                    parameters=parameters,
                    strategy_id=strategy_id,
                    commission_rate=Decimal(str(commission_rate)),
                    result_id=result_id,
                    benchmark=benchmark,
                )
            )
        finally:
            _close_async_resources(loop)
            loop.close()

        total_trades = stats.get("total_trades", 0)
        tracker.complete(f"回测完成：{total_trades} 笔交易")
        logger.info(
            f"[BacktestTask] backtest complete: result_id={stats.get('result_id')} "
            f"trades={total_trades}"
        )
        return stats
    except Exception as e:
        tracker.fail(str(e))
        raise
    finally:
        tracker.stop()
        tracker_context.reset(token)


def _fetch_ohlcv_sync(
    symbol: str,
    timeframe: str,
    exchange: str = "binance",
    limit: int = 1000,
    start_date: str = "",
    end_date: str = "",
) -> list[dict]:
    """同步获取历史 OHLCV 数据（供 Celery task 使用）"""
    import ccxt.async_support as ccxt
    from django.conf import settings

    symbol_normalized = symbol.replace("-", "/").replace("_", "/")
    if "/" not in symbol_normalized:
        symbol_normalized = f"{symbol_normalized}/USDT"

    since_ms: int | None = None
    end_ms: int | None = None

    if start_date:
        try:
            since_ms = int(datetime.fromisoformat(start_date).timestamp() * 1000)
        except (ValueError, TypeError):
            logger.warning(f"[BacktestTask] invalid start_date={start_date}, ignoring")
    if end_date:
        try:
            end_ms = int(datetime.fromisoformat(end_date).timestamp() * 1000)
        except (ValueError, TypeError):
            logger.warning(f"[BacktestTask] invalid end_date={end_date}, ignoring")

    async def _fetch():
        exchange_class = getattr(ccxt, exchange.lower(), None)
        if exchange_class is None:
            logger.error(f"[BacktestTask] unsupported exchange: {exchange}")
            return []

        options = {"enableRateLimit": True}
        proxy = getattr(settings, "WEB_PROXY", "") or None
        if proxy:
            options["aiohttp_proxy"] = proxy

        ex = exchange_class(options)
        try:
            ohlcv = await ex.fetch_ohlcv(
                symbol_normalized, timeframe, since=since_ms, limit=limit
            )
        finally:
            await ex.close()

        if not ohlcv:
            logger.warning(f"[BacktestTask] no OHLCV data for {symbol_normalized}")
            return []

        result = []
        for candle in ohlcv:
            ts_ms = int(candle[0])
            # Filter by end_date
            if end_ms is not None and ts_ms > end_ms:
                continue
            result.append(
                {
                    "timestamp": datetime.fromtimestamp(ts_ms / 1000).isoformat(),
                    "open": float(candle[1]),
                    "high": float(candle[2]),
                    "low": float(candle[3]),
                    "close": float(candle[4]),
                    "volume": float(candle[5]),
                }
            )

        return result

    try:
        return asyncio.run(_fetch())
    except Exception as e:
        logger.error(f"[BacktestTask] OHLCV fetch failed: {e}", exc_info=True)
        return []


def _close_async_resources(loop: asyncio.AbstractEventLoop) -> None:
    """显式关闭 Redis 连接等异步资源，防止 __del__ 在 loop 关闭后报错。"""
    try:
        from apps.memory.redis_client import RedisPool

        client = RedisPool.get_client()

        async def _aclose():
            await client.aclose()

        loop.run_until_complete(_aclose())
        RedisPool.close_client()
    except Exception:
        pass
