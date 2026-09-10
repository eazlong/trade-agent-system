"""Tests for exchange adapters (L1 — pure logic, mock httpx)."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import unittest

from apps.trading.adapters.base import (
    OrderNotFoundError,
    OrderRequest,
    OrderResponse,
    Position,
)
from apps.trading.adapters.binance import BinanceAdapter


class TestBinanceAdapterL1(unittest.TestCase):
    """L1: 验证签名构造、请求参数、响应解析"""

    def test_order_request_serialization(self):
        req = OrderRequest(
            exchange="binance",
            symbol="BTCUSDT",
            order_type="limit",
            side="buy",
            quantity=Decimal("0.001"),
            price=Decimal("50000"),
        )
        self.assertEqual(req.symbol, "BTCUSDT")
        self.assertEqual(req.side, "buy")
        self.assertIsNotNone(req.price)

    def test_position_model(self):
        pos = Position(
            symbol="BTCUSDT",
            side="long",
            quantity=Decimal("0.1"),
            entry_price=Decimal("49000"),
            unrealized_pnl=Decimal("100"),
            leverage=10,
        )
        self.assertEqual(pos.side, "long")
        self.assertGreater(pos.unrealized_pnl, Decimal("0"))

    def test_order_response_model(self):
        resp = OrderResponse(
            exchange_order_id="12345",
            status="NEW",
            filled_qty=Decimal("0"),
            avg_price=None,
            fee=None,
            raw={"orderId": 12345},
        )
        self.assertEqual(resp.exchange_order_id, "12345")
        self.assertEqual(resp.status, "NEW")

    def test_adapter_abstract_base(self):
        """验证 BaseExchangeAdapter 不能直接实例化"""
        with self.assertRaises(TypeError):
            from apps.trading.adapters.base import BaseExchangeAdapter

            BaseExchangeAdapter("key", "secret")

    def test_adapter_map_contains_exchanges(self):
        """验证 ADAPTER_MAP 包含目标交易所"""
        from apps.trading.adapters import ADAPTER_MAP

        self.assertIn("binance", ADAPTER_MAP)

    @patch("httpx.AsyncClient")
    def test_binance_connect_sets_client(self, mock_client_cls):
        """connect() 应初始化 httpx.AsyncClient"""
        adapter = BinanceAdapter("test_key", "test_secret")
        mock_client = AsyncMock()
        mock_client_cls.return_value = mock_client

        asyncio.run(adapter.connect())
        mock_client_cls.assert_called_once()
        self.assertIsNotNone(adapter._client)

    @patch("httpx.AsyncClient")
    def test_binance_disconnect_closes_client(self, mock_client_cls):
        """disconnect() 应关闭 httpx.AsyncClient"""
        adapter = BinanceAdapter("test_key", "test_secret")
        mock_client = AsyncMock()
        adapter._client = mock_client

        asyncio.run(adapter.disconnect())
        mock_client.aclose.assert_called_once()
        self.assertIsNone(adapter._client)

    @patch("httpx.AsyncClient")
    def test_binance_place_order_uses_signed_params(self, mock_client_cls):
        """place_order() 应使用签名参数"""
        adapter = BinanceAdapter("test_key", "test_secret")
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "orderId": 999,
            "status": "NEW",
            "executedQty": "0",
            "avgPrice": "50000",
        }
        mock_client.post.return_value = mock_response
        adapter._client = mock_client

        req = OrderRequest(
            exchange="binance",
            symbol="BTCUSDT",
            order_type="limit",
            side="buy",
            quantity=Decimal("0.001"),
            price=Decimal("50000"),
        )
        asyncio.run(adapter.place_order(req))

        # place_order 内部会先 POST 设置杠杆（/fapi/v1/leverage），
        # 再 POST 下单；断言存在下单调用即可（旧断言 called_once 过期）。
        order_calls = [
            c
            for c in mock_client.post.call_args_list
            if c.args[0].startswith("/fapi/v1/order")
        ]
        self.assertEqual(len(order_calls), 1, mock_client.post.call_args_list)
        self.assertIn("signature=", order_calls[0].args[0])
        self.assertIn("symbol=BTCUSDT", order_calls[0].args[0])
        self.assertIn("timestamp=", order_calls[0].args[0])

    @patch("httpx.AsyncClient")
    def test_binance_get_positions_filters_zero(self, mock_client_cls):
        """get_positions() 应过滤零仓位"""
        adapter = BinanceAdapter("test_key", "test_secret")
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {
                "positionAmt": "0",
                "entryPrice": "0",
                "unRealizedProfit": "0",
                "leverage": "1",
                "symbol": "BTCUSDT",
            },
            {
                "positionAmt": "100",
                "entryPrice": "49000",
                "unRealizedProfit": "100",
                "leverage": "10",
                "symbol": "ETHUSDT",
            },
        ]
        mock_client.get.return_value = mock_response
        adapter._client = mock_client

        positions = asyncio.run(adapter.get_positions())
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0].symbol, "ETHUSDT")

    @patch("httpx.AsyncClient")
    def test_binance_get_balance_returns_decimal(self, mock_client_cls):
        """get_balance() 应返回 Decimal 类型"""
        adapter = BinanceAdapter("test_key", "test_secret")
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {"asset": "USDT", "balance": "10000.5"},
            {"asset": "BTC", "balance": "0.5"},
        ]
        mock_client.get.return_value = mock_response
        adapter._client = mock_client

        balance = asyncio.run(adapter.get_balance())
        self.assertIsInstance(balance["USDT"], Decimal)
        self.assertEqual(balance["USDT"], Decimal("10000.5"))

    @patch("httpx.AsyncClient")
    def test_binance_disconnect_idempotent(self, mock_client_cls):
        """disconnect() 在未连接时不应抛异常"""
        adapter = BinanceAdapter("test_key", "test_secret")
        # _client is None by default
        asyncio.run(adapter.disconnect())  # should not raise

    @patch("apps.trading.adapters.binance.time")
    @patch("httpx.AsyncClient")
    def test_binance_sync_time_corrects_offset(self, mock_client_cls, mock_time):
        """connect() 应计算本地与服务器的时间偏差"""
        mock_time.time.return_value = 1000.0  # local = 1000000ms
        adapter = BinanceAdapter("test_key", "test_secret")
        mock_client = AsyncMock()
        mock_time_resp = MagicMock()
        mock_time_resp.status_code = 200
        mock_time_resp.json.return_value = {"serverTime": 1005000}  # server 5s ahead
        mock_client.get.return_value = mock_time_resp
        mock_client_cls.return_value = mock_client

        asyncio.run(adapter.connect())

        # offset = local - server = 1000000 - 1005000 = -5000
        self.assertEqual(adapter._time_offset, -5000)
        # Verify _sign uses corrected timestamp
        qs = adapter._sign({})
        self.assertIn("timestamp=1005000", qs)  # corrected to server time

    @patch("httpx.AsyncClient")
    def test_binance_sync_time_graceful_failure(self, mock_client_cls):
        """connect() 应在时间同步失败时正常降级"""
        adapter = BinanceAdapter("test_key", "test_secret")
        mock_client = AsyncMock()
        mock_client.get.side_effect = Exception("timeout")
        mock_client_cls.return_value = mock_client

        asyncio.run(adapter.connect())  # should not raise
        self.assertEqual(adapter._time_offset, 0)  # offset remains 0

    # ─── 精度归一化回归（Bug: 0.01 qty 触发 Binance -1111 精度错误）─ ──

    @staticmethod
    def _make_client_with_rules(symbol_rules: dict[str, dict]):
        """构造带 exchangeInfo 精度规则的 mock httpx client。

        symbol_rules: {"AVAXUSDT": {"stepSize": "0.1", "tickSize": "0.01", "minQty": "0.1"}}
        """
        mock_client = AsyncMock()
        exchange_info = {
            "symbols": [
                {
                    "symbol": sym,
                    "filters": [
                        {
                            "filterType": "LOT_SIZE",
                            "stepSize": rules["stepSize"],
                            "minQty": rules["minQty"],
                        },
                        {
                            "filterType": "PRICE_FILTER",
                            "tickSize": rules["tickSize"],
                        },
                    ],
                }
                for sym, rules in symbol_rules.items()
            ]
        }
        info_resp = MagicMock()
        info_resp.status_code = 200
        info_resp.json.return_value = exchange_info
        mock_client.get.return_value = info_resp
        return mock_client

    @patch("httpx.AsyncClient")
    def test_binance_place_order_rounds_quantity_to_stepsize(self, mock_client_cls):
        """数量必须按 LOT_SIZE stepSize 向下取整，避免 -1111 精度错误。"""
        adapter = BinanceAdapter("test_key", "test_secret")
        mock_client = self._make_client_with_rules(
            {"AVAXUSDT": {"stepSize": "0.1", "tickSize": "0.01", "minQty": "0.1"}}
        )
        order_resp = MagicMock()
        order_resp.status_code = 200
        order_resp.json.return_value = {
            "orderId": 1001,
            "status": "NEW",
            "executedQty": "0",
            "avgPrice": "0",
        }
        mock_client.post.return_value = order_resp
        adapter._client = mock_client

        req = OrderRequest(
            exchange="binance",
            symbol="AVAX/USDT",
            order_type="market",
            side="buy",
            quantity=Decimal("0.15"),
            price=None,
        )
        asyncio.run(adapter.place_order(req))

        # 保证 _load_symbol_rules 被调用
        mock_client.get.assert_called_once()
        # 校验签名 query 中的 quantity 已归一化到 stepSize=0.1 的整数倍
        call_args = mock_client.post.call_args
        query = call_args[0][0]
        self.assertIn("quantity=0.1", query, query)

    @patch("httpx.AsyncClient")
    def test_binance_place_order_rounds_price_to_tick(self, mock_client_cls):
        """限价单价格必须按 tickSize 归一化。"""
        adapter = BinanceAdapter("test_key", "test_secret")
        mock_client = self._make_client_with_rules(
            {"BTCUSDT": {"stepSize": "0.001", "tickSize": "0.01", "minQty": "0.001"}}
        )
        order_resp = MagicMock()
        order_resp.status_code = 200
        order_resp.json.return_value = {
            "orderId": 1002,
            "status": "NEW",
            "executedQty": "0",
            "avgPrice": "0",
        }
        mock_client.post.return_value = order_resp
        adapter._client = mock_client

        req = OrderRequest(
            exchange="binance",
            symbol="BTC/USDT",
            order_type="limit",
            side="buy",
            quantity=Decimal("0.001"),
            price=Decimal("50000.123"),
        )
        asyncio.run(adapter.place_order(req))

        call_args = mock_client.post.call_args
        query = call_args[0][0]
        self.assertIn("price=50000.12", query, query)
        self.assertIn("quantity=0.001", query, query)

    @patch("httpx.AsyncClient")
    def test_binance_place_order_rejects_quantity_below_step(self, mock_client_cls):
        """数量小于 stepSize 时归一化为 0，必须拒绝而非发送无效订单。"""
        adapter = BinanceAdapter("test_key", "test_secret")
        mock_client = self._make_client_with_rules(
            {"AVAXUSDT": {"stepSize": "1", "tickSize": "0.01", "minQty": "1"}}
        )
        adapter._client = mock_client

        req = OrderRequest(
            exchange="binance",
            symbol="AVAX/USDT",
            order_type="market",
            side="buy",
            quantity=Decimal("0.5"),
            price=None,
        )
        with self.assertRaises(ValueError):
            asyncio.run(adapter.place_order(req))
        mock_client.post.assert_not_called()

    @patch("httpx.AsyncClient")
    def test_binance_fetch_order_maps_filled(self, mock_client_cls):
        """fetch_order() 应归一化交易对并解析 FILLED 成交状态。"""
        adapter = BinanceAdapter("test_key", "test_secret")
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "symbol": "DOGEUSDT",
            "orderId": 2336219508,
            "status": "FILLED",
            "executedQty": "100",
            "avgPrice": "0.1099",
        }
        mock_client.get.return_value = mock_response
        adapter._client = mock_client

        fill = asyncio.run(adapter.fetch_order("2336219508", "DOGE/USDT"))

        self.assertEqual(fill.status, "filled")
        self.assertEqual(fill.filled_quantity, Decimal("100"))
        self.assertEqual(fill.avg_fill_price, Decimal("0.1099"))
        called_url = mock_client.get.call_args.args[0]
        self.assertIn("symbol=DOGEUSDT", called_url)
        self.assertIn("orderId=2336219508", called_url)

    @patch("httpx.AsyncClient")
    def test_binance_fetch_order_maps_partial_and_cancelled(self, mock_client_cls):
        """fetch_order() 应映射 PARTIALLY_FILLED / CANCELED 状态。"""
        adapter = BinanceAdapter("test_key", "test_secret")
        for exchange_status, expected in (
            ("PARTIALLY_FILLED", "partial"),
            ("CANCELED", "cancelled"),
            ("EXPIRED", "cancelled"),
            ("REJECTED", "failed"),
        ):
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "symbol": "DOGEUSDT",
                "orderId": 1,
                "status": exchange_status,
                "executedQty": "40",
                "avgPrice": "0.11",
            }
            mock_client.get.return_value = mock_response
            adapter._client = mock_client

            fill = asyncio.run(adapter.fetch_order("1", "DOGE/USDT"))
            self.assertEqual(fill.status, expected)
            self.assertEqual(fill.filled_quantity, Decimal("40"))

    @patch("httpx.AsyncClient")
    def test_binance_fetch_order_raises_not_found(self, mock_client_cls):
        """交易所返回 -2013 (订单不存在) 时应抛 OrderNotFoundError。"""
        adapter = BinanceAdapter("test_key", "test_secret")
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {"code": -2013, "msg": "Order does not exist"}
        mock_response.text = '{"code":-2013}'
        mock_client.get.return_value = mock_response
        adapter._client = mock_client

        with self.assertRaises(OrderNotFoundError):
            asyncio.run(adapter.fetch_order("2336219508", "DOGE/USDT"))
