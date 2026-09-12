"""Tests for position_list view — 标记价必须是交易所实时标记价。"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APITestCase

from apps.trading.adapters.base import Position


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        }
    }
)
class TestPositionListView(APITestCase):
    """L2: /api/trading/positions/ 返回的 mark_price 应来自适配器实时数据"""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="t_mark_price",
            email="t_mark_price@example.com",
            password="x",
        )
        self.client.force_authenticate(self.user)

    def _mock_executor(self, positions: list[Position]) -> MagicMock:
        adapter = MagicMock()
        adapter.get_positions = AsyncMock(return_value=positions)
        executor = MagicMock()
        executor._running = True
        executor._adapters = {"binance": adapter}
        return executor

    def test_position_list_returns_realtime_mark_price(self):
        """mark_price 应等于交易所实时标记价，而不是 entry_price"""
        executor = self._mock_executor(
            [
                Position(
                    symbol="ETHUSDT",
                    side="long",
                    quantity=Decimal("100"),
                    entry_price=Decimal("49000"),
                    mark_price=Decimal("49500"),
                    unrealized_pnl=Decimal("100"),
                    leverage=10,
                )
            ]
        )
        with patch(
            "apps.trading.executor.OrderExecutor.get_instance",
            return_value=executor,
        ):
            resp = self.client.get("/api/trading/positions/")

        self.assertEqual(resp.status_code, 200)
        pos = resp.json()["positions"][0]
        self.assertEqual(pos["mark_price"], "49500")
        self.assertNotEqual(pos["mark_price"], pos["entry_price"])

    def test_position_list_falls_back_to_entry_price_when_mark_missing(self):
        """适配器无标记价时退回 entry_price，不返回空串"""
        executor = self._mock_executor(
            [
                Position(
                    symbol="ETHUSDT",
                    side="long",
                    quantity=Decimal("100"),
                    entry_price=Decimal("49000"),
                    unrealized_pnl=Decimal("100"),
                    leverage=10,
                )
            ]
        )
        with patch(
            "apps.trading.executor.OrderExecutor.get_instance",
            return_value=executor,
        ):
            resp = self.client.get("/api/trading/positions/")

        self.assertEqual(resp.status_code, 200)
        pos = resp.json()["positions"][0]
        self.assertEqual(pos["mark_price"], "49000")
