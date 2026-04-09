"""Tests for backtest module."""
from __future__ import annotations

from decimal import Decimal
from datetime import date

from django.test import TestCase

from apps.backtest.models import BacktestResult


class TestBacktestResultModel(TestCase):
    """测试回测结果模型"""

    def setUp(self):
        from apps.trading.models import Strategy
        self.strategy = Strategy.objects.create(
            name='Test Strategy',
            code_path='strategies/test.py',
        )

    def test_create_backtest_result(self):
        result = BacktestResult.objects.create(
            strategy=self.strategy,
            symbol='BTC/USDT',
            timeframe='1h',
            start_date=date(2024, 1, 1),
            end_date=date(2024, 6, 1),
            initial_capital=Decimal('10000.00'),
            final_capital=Decimal('12000.00'),
            total_return_pct=20.0,
            sharpe_ratio=1.5,
            max_drawdown_pct=-5.0,
            win_rate=0.65,
            total_trades=100,
        )
        self.assertEqual(result.symbol, 'BTC/USDT')
        self.assertEqual(result.total_return_pct, 20.0)
        self.assertEqual(result.sharpe_ratio, 1.5)
        self.assertEqual(result.win_rate, 0.65)

    def test_backtest_result_with_parameters(self):
        """测试带参数的回测结果"""
        result = BacktestResult.objects.create(
            strategy=self.strategy,
            symbol='ETH/USDT',
            timeframe='4h',
            start_date=date(2024, 1, 1),
            end_date=date(2024, 3, 1),
            initial_capital=Decimal('5000.00'),
            final_capital=Decimal('5500.00'),
            total_return_pct=10.0,
            parameters={'rsi_period': 14, 'ma_fast': 10, 'ma_slow': 30},
        )
        self.assertEqual(result.parameters['rsi_period'], 14)
        self.assertEqual(result.parameters['ma_fast'], 10)

    def test_backtest_result_git_commit(self):
        """测试带 git commit hash 的回测结果"""
        result = BacktestResult.objects.create(
            strategy=self.strategy,
            symbol='BTC/USDT',
            timeframe='1d',
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            initial_capital=Decimal('10000.00'),
            final_capital=Decimal('10000.00'),
            total_return_pct=0.0,
            git_commit_hash='abc123def456',
        )
        self.assertEqual(result.git_commit_hash, 'abc123def456')

    def test_backtest_result_optional_fields(self):
        """测试可选字段为 None 的情况"""
        result = BacktestResult.objects.create(
            strategy=self.strategy,
            symbol='SOL/USDT',
            timeframe='15m',
            start_date=date(2024, 6, 1),
            end_date=date(2024, 6, 2),
            initial_capital=Decimal('1000.00'),
            final_capital=Decimal('1000.00'),
            total_return_pct=0.0,
            sharpe_ratio=None,
            max_drawdown_pct=None,
            win_rate=None,
        )
        self.assertIsNone(result.sharpe_ratio)
        self.assertIsNone(result.max_drawdown_pct)
        self.assertIsNone(result.win_rate)
