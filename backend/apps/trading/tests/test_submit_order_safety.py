"""submit_order 的幂等键与可诊断错误（2026-09-21）。

事故背景：2026-09-21 00:05:44 的 DOGE 买单因适配器卡死而 10 秒超时失败，
库里 `error_message` 是**空字符串**（`str(httpx.ConnectTimeout(""))` 为空）。
且当时没有传 clientOrderId，一旦引入"传输层重试"就可能重复下单。

契约：
  1. 下单前必须把订单行的 `request_id`（唯一 UUID）作为 `client_order_id` 传给适配器
     → 交易所侧有幂等键，重试/对账都以它为准。
  2. 失败落库的 `error_message` 必须带异常类型名，不能是空串（否则以后翻单只能靠猜）。
"""

from __future__ import annotations

import asyncio
import unittest
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from apps.trading.executor import OrderExecutor


class TestSubmitOrderSafety(unittest.TestCase):
    def setUp(self):
        self.executor = OrderExecutor()
        self.executor._running = True
        self.executor._riskguard = None
        self.executor._adapters = {}

    def tearDown(self):
        OrderExecutor._instance = None

    def _mock_response(self):
        resp = MagicMock()
        resp.exchange_order_id = "123456"
        resp.status = "NEW"
        resp.filled_qty = Decimal("0")
        resp.avg_price = None
        return resp

    @patch("apps.trading.models.Order.objects")
    def test_place_order_receives_request_id_as_client_order_id(self, mock_order_mgr):
        mock_adapter = AsyncMock()
        mock_adapter.place_order.return_value = self._mock_response()
        self.executor._adapters = {"binance": mock_adapter}

        rid = uuid.UUID("11111111-2222-3333-4444-555555555555")
        mock_order = MagicMock()
        mock_order.id = "order-uuid-1"
        mock_order.request_id = rid
        mock_order_mgr.create.return_value = mock_order
        mock_order_mgr.filter.return_value.update = MagicMock()

        asyncio.run(
            self.executor.submit_order(
                exchange="binance",
                symbol="DOGEUSDT",
                side="buy",
                order_type="market",
                quantity=Decimal("13119.21815906"),
                price=None,
                exchange_account_id="uuid-placeholder",
            )
        )

        sent = mock_adapter.place_order.call_args[0][0]
        self.assertEqual(
            sent.client_order_id,
            str(rid),
            "下单必须带订单行 request_id 作为幂等键（交易所据此去重/供对账）",
        )

    @patch("apps.trading.models.Order.objects")
    def test_failure_records_typed_error_message(self, mock_order_mgr):
        """httpx 超时异常 message 为空 → 落库也必须能看出异常类型。"""
        mock_adapter = AsyncMock()
        mock_adapter.place_order.side_effect = httpx.ConnectTimeout("")
        self.executor._adapters = {"binance": mock_adapter}

        mock_order = MagicMock()
        mock_order.id = "order-uuid-fail"
        mock_order.request_id = uuid.uuid4()
        mock_order_mgr.create.return_value = mock_order
        mock_order_mgr.filter.return_value.update = MagicMock()

        with self.assertRaises(httpx.ConnectTimeout):
            asyncio.run(
                self.executor.submit_order(
                    exchange="binance",
                    symbol="DOGEUSDT",
                    side="buy",
                    order_type="market",
                    quantity=Decimal("13119.21815906"),
                    price=None,
                    exchange_account_id="uuid-placeholder",
                )
            )

        updates = [
            call.kwargs
            for call in mock_order_mgr.filter.return_value.update.call_args_list
            if call.kwargs.get("status") == "failed"
        ]
        self.assertEqual(len(updates), 1, "必须落一条 failed")
        msg = updates[0]["error_message"]
        self.assertIn("ConnectTimeout", msg, "空消息异常也必须写清类型名")
        self.assertNotEqual(msg.strip(), "", "error_message 不能为空串")
