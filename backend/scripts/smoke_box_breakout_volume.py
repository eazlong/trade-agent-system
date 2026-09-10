"""
smoke_box_breakout_volume.py

最小验证：拉取 8b32586e-78e1-4205-934b-bd3378bdc74a 那次回测的 ohlcv_data，
用同一段历史对 box_time_range_breakout_strategy 跑两遍：
  1) volume_filter=False（与原回测 122 笔等价）
  2) volume_filter=True, volume_mult=1.2（开启量能门）
打印两次的 box_breakout_long 入场信号次数 + 时间，验证量能门真的过滤了单。

不模拟成交、不入库，只看入场信号的差异。

用法（backend 容器内）：
  docker-compose exec backend python scripts/smoke_box_breakout_volume.py
或本地：
  cd backend && DJANGO_SETTINGS_MODULE=core.settings.dev python scripts/smoke_box_breakout_volume.py
"""

from __future__ import annotations

import os
import sys
import django

# 让脚本能 import apps.* —— 走与 manage.py 相同的路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.dev")
django.setup()

from decimal import Decimal

from apps.strategy_engine.base import StrategyContext
# 直接 import 用户策略模块：~/.tradelogx/strategies/box_time_range_breakout_strategy.py
# 该文件在 register_strategy 时已被 apps.strategy_engine.registry 装饰
from apps.strategy_engine.registry import StrategyRegistry

BACKTEST_ID = "8b32586e-78e1-4205-934b-bd3378bdc74a"


def load_ohlcv(backtest_id: str) -> list[dict]:
    from apps.backtest.models import BacktestResult
    r = BacktestResult.objects.get(id=backtest_id)
    # ohlcv_data 已在表结构里看到是 [{"open","high","low","close","volume","timestamp"}, ...]
    return list(r.ohlcv_data)


def run_once(history: list[dict], params: dict) -> list[dict]:
    """单次跑完一根 on_bar 流，收集所有 buy 信号。"""
    cls = StrategyRegistry.get("box_time_range_breakout_strategy")
    ctx = StrategyContext(
        symbol="BNB/USDT",
        timeframe="4h",
        mode="backtest",
        params=params,
        balance=Decimal("10000"),
        position=Decimal("0"),
    )
    strategy = cls(ctx)
    entries: list[dict] = []
    for kline in history:
        sig = strategy.on_bar(kline, history[: history.index(kline) + 1])
        if sig is not None and getattr(sig, "side", None) == "buy":
            entries.append(
                {
                    "time": kline.get("timestamp"),
                    "close": kline.get("close"),
                    "signal_name": sig.signal_name,
                }
            )
    return entries


def main() -> None:
    history = load_ohlcv(BACKTEST_ID)
    print(f"loaded {len(history)} klines for {BACKTEST_ID}")

    base_params = {
        "lookback": 120,
        "max_width_pct": 0.04,
        "pivot_window": 2,
        "min_gap_bars": 3,
        "min_pivots": 2,
        "min_touches": 2,
        "atr_period": 14,
        "exit_at_mid": True,
        "position_pct": 0.1,
    }
    off_params = {**base_params, "volume_filter": False}
    on_params = {**base_params, "volume_filter": True, "volume_mult": 1.2}

    print("\n=== run 1: volume_filter=False ===")
    off = run_once(history, off_params)
    print(f"buy signals: {len(off)}")

    print("\n=== run 2: volume_filter=True, volume_mult=1.2 ===")
    on = run_once(history, on_params)
    print(f"buy signals: {len(on)}")

    off_set = {e["time"] for e in off}
    on_set = {e["time"] for e in on}
    blocked = sorted(off_set - on_set)
    print(f"\nblocked by volume filter: {len(blocked)}")
    for t in blocked[:20]:
        print(f"  {t}")
    if len(blocked) > 20:
        print(f"  ... and {len(blocked) - 20} more")


if __name__ == "__main__":
    main()
