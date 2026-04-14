"""
Celery tasks for backtest execution.

异步回测任务，供 BacktestAgent 和 API 调用。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
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
    user_id: str | None = None,
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
        user_id: 发起回测的用户 ID

    Returns:
        回测统计结果
    """
    from apps.strategy_engine.runner import StrategyRunner

    logger.info(
        f"[BacktestTask] running: strategy={strategy_name} symbol={symbol} "
        f"tf={timeframe} exchange={exchange}"
    )

    # 1. 获取历史 OHLCV 数据
    self.update_state(state="STARTED", meta={"step": "fetching_ohlcv", "symbol": symbol})
    ohlcv_data = _fetch_ohlcv_sync(symbol, timeframe, exchange)
    if not ohlcv_data:
        raise ValueError(f"未能获取 {symbol} {timeframe} 的历史K线数据")

    logger.info(f"[BacktestTask] OHLCV data fetched: {len(ohlcv_data)} bars")

    # 2. 执行回测
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
            )
        )
    finally:
        loop.close()

    logger.info(
        f"[BacktestTask] backtest complete: result_id={stats.get('result_id')} "
        f"trades={stats.get('total_trades')}"
    )

    return stats


def _fetch_ohlcv_sync(
    symbol: str,
    timeframe: str,
    exchange: str = "binance",
    limit: int = 500,
) -> list[dict]:
    """同步获取历史 OHLCV 数据（供 Celery task 使用）"""
    import ccxt.async_support as ccxt
    from django.conf import settings

    symbol_normalized = symbol.replace("-", "/").replace("_", "/")
    if "/" not in symbol_normalized:
        symbol_normalized = f"{symbol_normalized}/USDT"

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
            ohlcv = await ex.fetch_ohlcv(symbol_normalized, timeframe, limit=limit)
        finally:
            await ex.close()

        if not ohlcv:
            logger.warning(f"[BacktestTask] no OHLCV data for {symbol_normalized}")
            return []

        result = []
        for candle in ohlcv:
            result.append({
                "timestamp": datetime.fromtimestamp(candle[0] / 1000).isoformat(),
                "open": float(candle[1]),
                "high": float(candle[2]),
                "low": float(candle[3]),
                "close": float(candle[4]),
                "volume": float(candle[5]),
            })

        return result

    try:
        return asyncio.run(_fetch())
    except Exception as e:
        logger.error(f"[BacktestTask] OHLCV fetch failed: {e}", exc_info=True)
        return []
