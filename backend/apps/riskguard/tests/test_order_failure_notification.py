"""下单失败必须让用户看得见（2026-09-21）。

事故背景：2026-09-21 的 DOGE 买单失败后**完全静默**——`record_order_failure` 只喂熔断器，
用户只能翻订单列表才发现；失败意图（买 13119 DOGE）被丢弃且无人知晓。

契约：
  1. 订单失败 → 除熔断计数外，写一条 `Notification(channel="web")`（带 user、可读原因与订单要素）。
  2. 通知写入失败**绝不能**影响订单主流程（只记 ERROR），否则通知变成新故障点。
  3. 无 user_id 时保持旧行为（直接返回，不写通知）。
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from apps.riskguard.guard import RiskGuard


class TestOrderFailureNotification(unittest.IsolatedAsyncioTestCase):
    async def test_order_failure_writes_web_notification(self):
        guard = RiskGuard("trading")
        with patch("apps.riskguard.guard.CircuitBreaker") as MockCB, patch(
            "apps.notify.models.Notification.objects"
        ) as mock_notif:
            MockCB.return_value.record_failure = AsyncMock()

            await guard.record_order_failure(
                "user-1",
                symbol="DOGE/USDT",
                side="buy",
                quantity="13119.21815906",
                error="ConnectTimeout（连接超时，请求未到达交易所）",
            )

        MockCB.return_value.record_failure.assert_awaited_once()
        mock_notif.create.assert_called_once()
        kwargs = mock_notif.create.call_args.kwargs
        self.assertEqual(kwargs["user_id"], "user-1")
        self.assertEqual(kwargs["channel"], "web")
        self.assertIn("DOGE/USDT", kwargs["message"])
        self.assertIn("buy", kwargs["message"])
        self.assertIn("13119.21815906", kwargs["message"])
        self.assertIn("ConnectTimeout", kwargs["message"])

    async def test_notification_failure_does_not_break_order_flow(self):
        guard = RiskGuard("trading")
        with patch("apps.riskguard.guard.CircuitBreaker") as MockCB, patch(
            "apps.notify.models.Notification.objects"
        ) as mock_notif:
            MockCB.return_value.record_failure = AsyncMock()
            mock_notif.create.side_effect = RuntimeError("db down")

            with self.assertLogs("apps.riskguard.guard", level="ERROR"):
                await guard.record_order_failure("user-1", symbol="DOGE/USDT", error="x")

        MockCB.return_value.record_failure.assert_awaited_once()

    async def test_without_user_id_is_noop(self):
        guard = RiskGuard("trading")
        with patch("apps.riskguard.guard.CircuitBreaker") as MockCB, patch(
            "apps.notify.models.Notification.objects"
        ) as mock_notif:
            MockCB.return_value.record_failure = AsyncMock()

            await guard.record_order_failure(None, symbol="DOGE/USDT", error="x")

        MockCB.return_value.record_failure.assert_not_awaited()
        mock_notif.create.assert_not_called()
