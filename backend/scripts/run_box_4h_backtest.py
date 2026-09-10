#!/usr/bin/env python
"""
箱体突破策略 4h BNB 回测（下沿出场）

用法：
    cd backend
    DJANGO_SETTINGS_MODULE=core.settings.dev .venv/bin/python scripts/run_box_4h_backtest.py

说明：
    - 策略：box_time_range_breakout_strategy（~/.tradelogx/strategies/ 已改为 position_pct 开仓）
    - 标的：BNB/USDT  周期：4h  交易所：binance
    - 出场：exit_at_mid=False -> 跌破箱体下沿才平仓
    - 开仓：position_pct=0.1（总资金 10%）* 默认 initial_capital=10000 USDT
    - 数据：ccxt 从 Binance 分页拉取，默认最近 ~1000 根 4h K 线（约 167 天）
    - 纯引擎运行，不写数据库（结果只打印到 stdout）
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent  # backend/
STRATEGY_PATH = Path.home() / ".tradelogx" / "strategies" / "box_time_range_breakout_strategy.py"

SYMBOL = "BNB/USDT"
TIMEFRAME = "4h"
EXCHANGE = "binance"
INITIAL_CAPITAL = Decimal("10000")
COMMISSION_RATE = Decimal("0.001")
# 下沿出场 + 10% 资金开仓
PARAMS = {"exit_at_mid": False, "position_pct": 0.1}
LIMIT = 1000          # 每批 K 线数（ccxt 上限）
START_DATE = ""       # 留空 = 最近数据；例 "2025-01-01"
END_DATE = ""


def _fetch_ohlcv_sync(
    symbol: str,
    timeframe: str,
    exchange: str = "binance",
    limit: int = 1000,
    start_date: str = "",
    end_date: str = "",
) -> list[dict]:
    """与 apps/backtest/tasks.py:_fetch_ohlcv_sync 同源：分页拉取历史 K 线。"""
    import ccxt.async_support as ccxt
    from django.conf import settings

    symbol_normalized = symbol.replace("-", "/").replace("_", "/")
    if "/" not in symbol_normalized:
        symbol_normalized = f"{symbol_normalized}/USDT"

    since_ms = end_ms = None
    if start_date:
        since_ms = int(datetime.fromisoformat(start_date).timestamp() * 1000)
    if end_date:
        end_ms = int(datetime.fromisoformat(end_date).timestamp() * 1000)

    async def _fetch():
        exchange_class = getattr(ccxt, exchange.lower(), None)
        if exchange_class is None:
            return []
        options = {"enableRateLimit": True}
        proxy = getattr(settings, "WEB_PROXY", "") or None
        if proxy:
            options["aiohttp_proxy"] = proxy
        ex = exchange_class(options)
        try:
            all_candles = []
            batch_since = since_ms
            while True:
                ohlcv = await ex.fetch_ohlcv(
                    symbol_normalized, timeframe, since=batch_since, limit=limit
                )
                if not ohlcv:
                    break
                raw_count = len(ohlcv)
                if end_ms is not None:
                    ohlcv = [c for c in ohlcv if c[0] <= end_ms]
                all_candles.extend(ohlcv)
                if raw_count < limit:
                    break
                if not ohlcv:
                    break
                batch_since = ohlcv[-1][0] + 1
                if end_ms is not None and batch_since > end_ms:
                    break
        finally:
            await ex.close()
        return all_candles

    return asyncio.run(_fetch())


def _load_strategy_module():
    """直接从 ~/.tradelogx/strategies/ 加载策略模块（无需 /root 路径、无需注册中心）。"""
    spec = importlib.util.spec_from_file_location(
        "box_time_range_breakout_strategy", STRATEGY_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _fmt(v) -> str:
    try:
        return f"{v:.8f}"
    except Exception:
        return str(v)


def main() -> int:
    if str(BASE) not in sys.path:
        sys.path.insert(0, str(BASE))

    print(f"拉取 {SYMBOL} {TIMEFRAME} @ {EXCHANGE} ...")
    ohlcv = _fetch_ohlcv_sync(
        SYMBOL, TIMEFRAME, EXCHANGE, limit=LIMIT,
        start_date=START_DATE, end_date=END_DATE,
    )
    if not ohlcv:
        print("❌ 未取到 K 线数据（检查网络 / WEB_PROXY / ccxt 可用性）")
        return 1
    print(f"✅ 取到 {len(ohlcv)} 根 K 线（{datetime.fromtimestamp(ohlcv[0][0]/1000).date()} ~ "
          f"{datetime.fromtimestamp(ohlcv[-1][0]/1000).date()}）")

    # OHLCV(ccxt 元组) -> dict（与引擎期望一致）
    ohlcv_data = [
        {
            "timestamp": datetime.fromtimestamp(ts / 1000).isoformat(),
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": v,
        }
        for ts, o, h, l, c, v in ohlcv
    ]

    mod = _load_strategy_module()
    from apps.strategy_engine.backtest_mode import BacktestEngine
    from apps.strategy_engine.base import StrategyContext

    ctx = StrategyContext(
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        mode="backtest",
        params=dict(PARAMS),
        balance=INITIAL_CAPITAL,
    )
    strategy = mod.BoxTimeRangeBreakoutStrategy(ctx)

    print(f"运行回测：exit_at_mid={PARAMS['exit_at_mid']} position_pct={PARAMS['position_pct']} "
          f"initial_capital={INITIAL_CAPITAL} commission={COMMISSION_RATE}")
    engine = BacktestEngine(
        strategy=strategy,
        ohlcv_data=ohlcv_data,
        initial_capital=INITIAL_CAPITAL,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        commission_rate=COMMISSION_RATE,
    )
    stats = asyncio.run(engine.run())

    print("\n===== 回测统计 =====")
    for key in ("total_trades", "win_rate", "total_return_pct", "final_equity",
                "sharpe_ratio", "max_drawdown", "profit_factor"):
        if key in stats:
            print(f"  {key}: {stats[key]}")

    print(f"\n===== 交易明细（共 {len(engine._trades)} 笔）=====")
    for t in engine._trades:
        print(
            f"  {t['entry_time'][:16]} -> {t['exit_time'][:16] if t['exit_time'] else '---'} "
            f"{t['side']:<5} qty={_fmt(t['quantity'])} "
            f"in={_fmt(t['entry_price'])} out={_fmt(t['exit_price']) if t['exit_price'] else '---'} "
            f"pnl={_fmt(t['pnl']) if t['pnl'] is not None else '---'} ({t['signal']}/{t['exit_reason']})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())