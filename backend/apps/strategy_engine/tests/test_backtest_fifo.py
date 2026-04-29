"""
Test FIFO entry time tracking in backtest engine.

Verifies that entry_time and exit_time are correctly tracked
when multiple buy orders are placed before selling.
Each buy/open/add/close is recorded as an independent trade.
"""

from datetime import datetime
from decimal import Decimal

import pytest

from apps.strategy_engine.backtest_mode import BacktestEngine
from apps.strategy_engine.base import BaseStrategy, OrderSignal, StrategyContext


class _FakeStrategy(BaseStrategy):
    """Minimal strategy for testing."""

    name = "test_fifo"
    description = "Test FIFO tracking"
    params_schema = {}

    def __init__(self, context: StrategyContext):
        super().__init__(context)

    def on_bar(self, kline: dict, history: list[dict]):
        return None

    def on_start(self):
        pass

    def on_stop(self):
        pass


def _make_ctx() -> StrategyContext:
    return StrategyContext(
        symbol="BTC/USDT",
        timeframe="1h",
        mode="backtest",
        params={},
    )


def _make_kline(ts: str, close: float) -> dict:
    return {
        "timestamp": ts,
        "open": close - 10,
        "high": close + 20,
        "low": close - 20,
        "close": close,
        "volume": 100.0,
    }


@pytest.fixture()
def ohlcv_data() -> list[dict]:
    """10 bars of test data with distinct timestamps."""
    base = datetime(2024, 1, 1, 0, 0)
    return [
        _make_kline(
            ts=datetime.isoformat(base.replace(hour=i)),
            close=42000.0 + i * 100,
        )
        for i in range(10)
    ]


class TestFifoEntryTimeTracking:
    """Test FIFO entry time tracking in BacktestEngine."""

    def test_single_buy_sell_different_times(self, ohlcv_data):
        """Single buy and single sell: buy recorded as 'open', sell as 'close'."""
        ctx = _make_ctx()
        strategy = _FakeStrategy(ctx)
        engine = BacktestEngine(
            strategy=strategy,
            ohlcv_data=ohlcv_data,
            initial_capital=Decimal("100000"),
            symbol="BTC/USDT",
            timeframe="1h",
        )

        kline_buy = ohlcv_data[2]
        kline_sell = ohlcv_data[5]

        buy_signal = OrderSignal(
            side="buy",
            quantity=Decimal("0.1"),
            order_type="market",
            price=Decimal(str(kline_buy["close"])),
            signal_name="test_buy",
            exchange="binance",
            metadata={},
        )
        engine._process_signal(buy_signal, kline_buy)

        sell_signal = OrderSignal(
            side="sell",
            quantity=Decimal("0.1"),
            order_type="market",
            price=Decimal(str(kline_sell["close"])),
            signal_name="test_sell",
            exchange="binance",
            metadata={},
        )
        engine._process_signal(sell_signal, kline_sell)

        # 2 trades: 1 open + 1 close
        assert len(engine._trades) == 2
        open_trade = engine._trades[0]
        close_trade = engine._trades[1]

        assert open_trade["trade_type"] == "open"
        assert open_trade["entry_time"] == kline_buy["timestamp"]
        assert open_trade["exit_time"] is None

        assert close_trade["trade_type"] == "close"
        assert close_trade["entry_time"] == kline_buy["timestamp"]
        assert close_trade["exit_time"] == kline_sell["timestamp"]
        assert close_trade["entry_time"] != close_trade["exit_time"]

    def test_multiple_buys_fifo_sell(self, ohlcv_data):
        """Multiple buys then partial sell: FIFO should match earliest entry."""
        ctx = _make_ctx()
        strategy = _FakeStrategy(ctx)
        engine = BacktestEngine(
            strategy=strategy,
            ohlcv_data=ohlcv_data,
            initial_capital=Decimal("100000"),
            symbol="BTC/USDT",
            timeframe="1h",
        )

        buy1_signal = OrderSignal(
            side="buy",
            quantity=Decimal("0.1"),
            order_type="market",
            price=Decimal(str(ohlcv_data[1]["close"])),
            signal_name="buy1",
            exchange="binance",
            metadata={},
        )
        engine._process_signal(buy1_signal, ohlcv_data[1])

        buy2_signal = OrderSignal(
            side="buy",
            quantity=Decimal("0.1"),
            order_type="market",
            price=Decimal(str(ohlcv_data[3]["close"])),
            signal_name="buy2",
            exchange="binance",
            metadata={},
        )
        engine._process_signal(buy2_signal, ohlcv_data[3])

        # Sell 0.1 on bar 7 - should match the FIRST buy (bar 1) via FIFO
        sell_signal = OrderSignal(
            side="sell",
            quantity=Decimal("0.1"),
            order_type="market",
            price=Decimal(str(ohlcv_data[7]["close"])),
            signal_name="sell1",
            exchange="binance",
            metadata={},
        )
        engine._process_signal(sell_signal, ohlcv_data[7])

        # 3 trades: 2 opens (buy1, buy2) + 1 close (sell)
        assert len(engine._trades) == 3
        assert engine._trades[0]["trade_type"] == "open"
        assert engine._trades[1]["trade_type"] == "add"
        assert engine._trades[2]["trade_type"] == "close"

        # FIFO: close should use the first buy's entry time
        close_trade = engine._trades[2]
        assert close_trade["entry_time"] == ohlcv_data[1]["timestamp"]
        assert close_trade["exit_time"] == ohlcv_data[7]["timestamp"]

    def test_end_of_backtest_close(self, ohlcv_data):
        """Position held at end of backtest should have correct entry time."""
        ctx = _make_ctx()
        strategy = _FakeStrategy(ctx)
        engine = BacktestEngine(
            strategy=strategy,
            ohlcv_data=ohlcv_data,
            initial_capital=Decimal("100000"),
            symbol="BTC/USDT",
            timeframe="1h",
        )

        buy_signal = OrderSignal(
            side="buy",
            quantity=Decimal("0.1"),
            order_type="market",
            price=Decimal(str(ohlcv_data[3]["close"])),
            signal_name="buy",
            exchange="binance",
            metadata={},
        )
        engine._process_signal(buy_signal, ohlcv_data[3])

        engine._close_position(ohlcv_data[-1], "end_of_backtest")

        # 2 trades: 1 open + 1 close
        assert len(engine._trades) == 2
        open_trade = engine._trades[0]
        close_trade = engine._trades[1]

        assert open_trade["trade_type"] == "open"
        assert open_trade["entry_time"] == ohlcv_data[3]["timestamp"]

        assert close_trade["trade_type"] == "close"
        assert close_trade["entry_time"] == ohlcv_data[3]["timestamp"]
        assert close_trade["exit_time"] == ohlcv_data[-1]["timestamp"]
        assert close_trade["entry_time"] != close_trade["exit_time"]

    def test_entry_times_cleared_after_full_close(self, ohlcv_data):
        """After fully closing position, entry_times should be empty."""
        ctx = _make_ctx()
        strategy = _FakeStrategy(ctx)
        engine = BacktestEngine(
            strategy=strategy,
            ohlcv_data=ohlcv_data,
            initial_capital=Decimal("100000"),
            symbol="BTC/USDT",
            timeframe="1h",
        )

        buy1_signal = OrderSignal(
            side="buy",
            quantity=Decimal("0.1"),
            order_type="market",
            price=Decimal(str(ohlcv_data[1]["close"])),
            signal_name="buy1",
            exchange="binance",
            metadata={},
        )
        engine._process_signal(buy1_signal, ohlcv_data[1])

        buy2_signal = OrderSignal(
            side="buy",
            quantity=Decimal("0.1"),
            order_type="market",
            price=Decimal(str(ohlcv_data[3]["close"])),
            signal_name="buy2",
            exchange="binance",
            metadata={},
        )
        engine._process_signal(buy2_signal, ohlcv_data[3])

        assert len(engine._entry_times) == 2

        engine._close_position(ohlcv_data[-1], "end_of_backtest")

        assert len(engine._entry_times) == 0
        assert engine._entry_time == ""
