"""Tests for backtest module."""

from __future__ import annotations

from decimal import Decimal
from datetime import date, datetime

from django.test import TestCase

from apps.backtest.models import BacktestResult, BacktestTrade


class TestBacktestResultModel(TestCase):
    """测试回测结果模型"""

    def setUp(self):
        from apps.trading.models import Strategy

        self.strategy = Strategy.objects.create(
            name="Test Strategy",
            code_path="strategies/test.py",
        )

    def test_create_backtest_result(self):
        result = BacktestResult.objects.create(
            strategy=self.strategy,
            symbol="BTC/USDT",
            timeframe="1h",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 6, 1),
            initial_capital=Decimal("10000.00"),
            final_capital=Decimal("12000.00"),
            total_return_pct=20.0,
            sharpe_ratio=1.5,
            max_drawdown_pct=-5.0,
            win_rate=0.65,
            total_trades=100,
        )
        self.assertEqual(result.symbol, "BTC/USDT")
        self.assertEqual(result.total_return_pct, 20.0)
        self.assertEqual(result.sharpe_ratio, 1.5)
        self.assertEqual(result.win_rate, 0.65)

    def test_backtest_result_with_parameters(self):
        """测试带参数的回测结果"""
        result = BacktestResult.objects.create(
            strategy=self.strategy,
            symbol="ETH/USDT",
            timeframe="4h",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 3, 1),
            initial_capital=Decimal("5000.00"),
            final_capital=Decimal("5500.00"),
            total_return_pct=10.0,
            parameters={"rsi_period": 14, "ma_fast": 10, "ma_slow": 30},
        )
        self.assertEqual(result.parameters["rsi_period"], 14)
        self.assertEqual(result.parameters["ma_fast"], 10)

    def test_backtest_result_git_commit(self):
        """测试带 git commit hash 的回测结果"""
        result = BacktestResult.objects.create(
            strategy=self.strategy,
            symbol="BTC/USDT",
            timeframe="1d",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            initial_capital=Decimal("10000.00"),
            final_capital=Decimal("10000.00"),
            total_return_pct=0.0,
            git_commit_hash="abc123def456",
        )
        self.assertEqual(result.git_commit_hash, "abc123def456")

    def test_backtest_result_optional_fields(self):
        """测试可选字段为 None 的情况"""
        result = BacktestResult.objects.create(
            strategy=self.strategy,
            symbol="SOL/USDT",
            timeframe="15m",
            start_date=date(2024, 6, 1),
            end_date=date(2024, 6, 2),
            initial_capital=Decimal("1000.00"),
            final_capital=Decimal("1000.00"),
            total_return_pct=0.0,
            sharpe_ratio=None,
            max_drawdown_pct=None,
            win_rate=None,
        )
        self.assertIsNone(result.sharpe_ratio)
        self.assertIsNone(result.max_drawdown_pct)
        self.assertIsNone(result.win_rate)

    def test_backtest_result_equity_curve(self):
        """测试权益曲线 JSON 字段"""
        curve = [
            {"timestamp": "2024-01-01T00:00:00Z", "equity": 10000.0, "drawdown": 0.0},
            {
                "timestamp": "2024-01-02T00:00:00Z",
                "equity": 10150.0,
                "drawdown": -0.002,
            },
        ]
        dd_curve = [
            {"timestamp": "2024-01-01T00:00:00Z", "drawdown": 0.0},
            {"timestamp": "2024-01-02T00:00:00Z", "drawdown": -0.002},
        ]
        result = BacktestResult.objects.create(
            strategy=self.strategy,
            symbol="BTC/USDT",
            timeframe="1d",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            initial_capital=Decimal("10000.00"),
            final_capital=Decimal("10150.00"),
            total_return_pct=1.5,
            equity_curve=curve,
            drawdown_curve=dd_curve,
        )
        self.assertEqual(len(result.equity_curve), 2)
        self.assertEqual(result.equity_curve[0]["equity"], 10000.0)
        self.assertEqual(len(result.drawdown_curve), 2)

    def test_backtest_trade(self):
        """测试交易日志模型"""
        result = BacktestResult.objects.create(
            strategy=self.strategy,
            symbol="BTC/USDT",
            timeframe="1h",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            initial_capital=Decimal("10000.00"),
            final_capital=Decimal("10500.00"),
            total_return_pct=5.0,
        )
        trade = BacktestTrade.objects.create(
            backtest=result,
            entry_time=datetime(2024, 1, 5, 8, 0),
            exit_time=datetime(2024, 1, 6, 14, 0),
            symbol="BTC/USDT",
            side="long",
            entry_price=Decimal("42000.00"),
            exit_price=Decimal("42800.00"),
            quantity=Decimal("0.5"),
            pnl=Decimal("400.00"),
            pnl_pct=1.90,
            cumulative_pnl=Decimal("400.00"),
            fees=Decimal("4.20"),
            tags=["take_profit"],
        )
        self.assertEqual(trade.side, "long")
        self.assertEqual(trade.pnl, Decimal("400.00"))
        self.assertEqual(trade.tags, ["take_profit"])

    def test_backtest_trade_related_name(self):
        """测试通过 backtest.trades 反向查询"""
        result = BacktestResult.objects.create(
            strategy=self.strategy,
            symbol="ETH/USDT",
            timeframe="4h",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            initial_capital=Decimal("5000.00"),
            final_capital=Decimal("5200.00"),
            total_return_pct=4.0,
        )
        for i in range(3):
            BacktestTrade.objects.create(
                backtest=result,
                entry_time=datetime(2024, 1, 5 + i, 8, 0),
                exit_time=datetime(2024, 1, 6 + i, 14, 0),
                symbol="ETH/USDT",
                side="long" if i % 2 == 0 else "short",
                entry_price=Decimal("2200.00"),
                exit_price=Decimal("2250.00"),
                quantity=Decimal("1.0"),
                pnl=Decimal("50.00"),
            )
        self.assertEqual(result.trades.count(), 3)
        self.assertEqual(result.trades.filter(side="long").count(), 2)
