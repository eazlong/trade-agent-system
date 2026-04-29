"""Tests for RiskGuard."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import unittest

from apps.riskguard.guard import RiskGuard
from apps.trading.adapters.base import OrderRequest


class TestRiskGuardL2(unittest.TestCase):
    """L2: RiskGuard 前置校验逻辑（mock 内部查询方法）"""

    def setUp(self):
        self.guard = RiskGuard(mode="trading")
        RiskGuard._instance = None

    def tearDown(self):
        RiskGuard._instance = None

    def test_pre_trade_check_passes_without_user(self):
        """无 user_id 时直接通过"""
        request = OrderRequest(
            exchange="binance",
            symbol="BTCUSDT",
            order_type="market",
            side="buy",
            quantity=Decimal("0.001"),
            price=None,
        )
        result = asyncio.run(self.guard.pre_trade_check(request, None))
        self.assertEqual(result, (True, "OK"))

    def test_pre_trade_check_circuit_open_rejects(self):
        """熔断器打开时拒绝下单"""
        request = OrderRequest(
            exchange="binance",
            symbol="BTCUSDT",
            order_type="market",
            side="buy",
            quantity=Decimal("0.001"),
            price=None,
        )

        mock_cb = AsyncMock()
        mock_cb.is_open.return_value = True

        with patch.object(self.guard, "_is_circuit_open", mock_cb.is_open):
            with patch.object(
                self.guard, "_get_daily_trade_count", AsyncMock(return_value=0)
            ):
                with patch.object(
                    self.guard,
                    "_check_position_limit",
                    AsyncMock(return_value=(True, "")),
                ):
                    with patch.object(
                        self.guard,
                        "_check_drawdown",
                        AsyncMock(return_value=(True, "")),
                    ):
                        result = asyncio.run(
                            self.guard.pre_trade_check(request, "user123")
                        )

        self.assertEqual(result[0], False)
        self.assertIn("熔断器触发", result[1])

    def test_pre_trade_check_daily_trade_limit_rejects(self):
        """日内交易次数超限时拒绝"""
        request = OrderRequest(
            exchange="binance",
            symbol="BTCUSDT",
            order_type="market",
            side="buy",
            quantity=Decimal("0.001"),
            price=None,
        )

        with patch.object(
            self.guard, "_is_circuit_open", AsyncMock(return_value=False)
        ):
            with patch.object(
                self.guard, "_get_daily_trade_count", AsyncMock(return_value=50)
            ):
                with patch.object(
                    self.guard,
                    "_check_position_limit",
                    AsyncMock(return_value=(True, "")),
                ):
                    with patch.object(
                        self.guard,
                        "_check_drawdown",
                        AsyncMock(return_value=(True, "")),
                    ):
                        result = asyncio.run(
                            self.guard.pre_trade_check(request, "user123")
                        )

        self.assertEqual(result[0], False)
        self.assertIn("日内交易次数", result[1])

    def test_pre_trade_check_position_limit_rejects(self):
        """仓位超过20%时拒绝"""
        request = OrderRequest(
            exchange="binance",
            symbol="BTCUSDT",
            order_type="limit",
            side="buy",
            quantity=Decimal("1"),
            price=Decimal("50000"),
        )

        with patch.object(
            self.guard, "_is_circuit_open", AsyncMock(return_value=False)
        ):
            with patch.object(
                self.guard, "_get_daily_trade_count", AsyncMock(return_value=0)
            ):
                with patch.object(
                    self.guard,
                    "_check_position_limit",
                    AsyncMock(return_value=(False, "单笔仓位 500.0% 超过上限 20%")),
                ):
                    with patch.object(
                        self.guard,
                        "_check_drawdown",
                        AsyncMock(return_value=(True, "")),
                    ):
                        result = asyncio.run(
                            self.guard.pre_trade_check(request, "user123")
                        )

        self.assertEqual(result[0], False)
        self.assertIn("超过上限", result[1])

    def test_pre_trade_check_all_checks_pass(self):
        """所有校验都通过"""
        request = OrderRequest(
            exchange="binance",
            symbol="BTCUSDT",
            order_type="limit",
            side="buy",
            quantity=Decimal("0.01"),
            price=Decimal("50000"),
        )

        with patch.object(
            self.guard, "_is_circuit_open", AsyncMock(return_value=False)
        ):
            with patch.object(
                self.guard, "_get_daily_trade_count", AsyncMock(return_value=5)
            ):
                with patch.object(
                    self.guard,
                    "_check_position_limit",
                    AsyncMock(return_value=(True, "")),
                ):
                    with patch.object(
                        self.guard,
                        "_check_drawdown",
                        AsyncMock(return_value=(True, "")),
                    ):
                        result = asyncio.run(
                            self.guard.pre_trade_check(request, "user123")
                        )

        self.assertEqual(result, (True, "OK"))

    def test_pre_trade_check_order(self):
        """校验按正确顺序执行（熔断→次数→仓位→回撤）"""
        request = OrderRequest(
            exchange="binance",
            symbol="BTCUSDT",
            order_type="limit",
            side="buy",
            quantity=Decimal("1"),
            price=Decimal("50000"),
        )
        call_order = []

        async def track_circuit(*a):
            call_order.append("circuit")
            return False

        async def track_count(*a):
            call_order.append("count")
            return 0

        async def track_pos(*a):
            call_order.append("position")
            return True, ""

        async def track_drawdown(*a):
            call_order.append("drawdown")
            return True, ""

        with patch.object(self.guard, "_is_circuit_open", track_circuit):
            with patch.object(self.guard, "_get_daily_trade_count", track_count):
                with patch.object(self.guard, "_check_position_limit", track_pos):
                    with patch.object(self.guard, "_check_drawdown", track_drawdown):
                        asyncio.run(self.guard.pre_trade_check(request, "user123"))

        self.assertEqual(call_order, ["circuit", "count", "position", "drawdown"])


class TestRiskGuardLifecycle(unittest.TestCase):
    """RiskGuard 生命周期测试"""

    def test_singleton_set_on_start(self):
        """start() 应设置 _instance"""
        guard = RiskGuard()
        asyncio.run(guard.start())
        self.assertIs(RiskGuard.get_instance(), guard)
        asyncio.run(guard.stop())

    def test_monitor_loop_not_started_in_paper_mode(self):
        """mode='paper' 时不启动 monitor loop"""
        guard = RiskGuard(mode="paper")
        asyncio.run(guard.start())
        self.assertIsNone(guard._monitor_task)
        asyncio.run(guard.stop())

    def test_stop_cancels_monitor_task(self):
        """stop() 应取消 monitor task 并重置 _instance"""
        guard = RiskGuard(mode="trading")

        async def run_test():
            async def fake_monitor():
                while True:
                    await asyncio.sleep(60)

            guard._monitor_task = asyncio.create_task(fake_monitor())
            await asyncio.sleep(0)  # 让 task 开始执行
            await guard.stop()
            # stop() 将 _monitor_task 设为 None，将 _instance 清空
            self.assertIsNone(guard._monitor_task)
            self.assertIsNone(RiskGuard._instance)

        asyncio.run(run_test())


class TestRiskGuardRecordOrder(unittest.TestCase):
    """record_order_success / record_order_failure 测试"""

    def test_record_success_calls_circuit_breaker(self):
        """record_order_success 应调用 CircuitBreaker.record_success"""
        mock_cb = AsyncMock()

        with patch("apps.riskguard.guard.CircuitBreaker", return_value=mock_cb):
            asyncio.run(RiskGuard().record_order_success("user123"))

        mock_cb.record_success.assert_called_once()

    def test_record_failure_calls_circuit_breaker(self):
        """record_order_failure 应调用 CircuitBreaker.record_failure"""
        mock_cb = AsyncMock()

        with patch("apps.riskguard.guard.CircuitBreaker", return_value=mock_cb):
            asyncio.run(RiskGuard().record_order_failure("user123"))

        mock_cb.record_failure.assert_called_once()

    def test_record_skipped_without_user(self):
        """无 user_id 时跳过记录"""
        guard = RiskGuard()
        # 不应抛异常
        asyncio.run(guard.record_order_success(None))
        asyncio.run(guard.record_order_failure(None))
