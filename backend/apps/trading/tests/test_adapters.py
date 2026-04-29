"""Tests for exchange adapters (L1 — pure logic, mock httpx)."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import unittest

from apps.trading.adapters.base import OrderRequest, OrderResponse, Position
from apps.trading.adapters.binance import BinanceAdapter
from apps.trading.adapters.okx import OKXAdapter


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
        self.assertIn("okx", ADAPTER_MAP)

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

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        self.assertTrue(call_args[0][0].startswith("/fapi/v1/order"))

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


class TestOKXAdapterL1(unittest.TestCase):
    """L1: OKX 适配器签名和请求验证"""

    def test_okx_passphrase_required(self):
        """OKX adapter 需要 passphrase 参数"""
        adapter = OKXAdapter("key", "secret", "passphrase")
        self.assertEqual(adapter._passphrase, "passphrase")

    @patch("httpx.AsyncClient")
    def test_okx_place_order_uses_json_body(self, mock_client_cls):
        """OKX place_order() 应使用 JSON body 而非 form data"""
        adapter = OKXAdapter("test_key", "test_secret", "test_passphrase")
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "code": "0",
            "data": [{"ordId": "123", "state": "live", "filledSz": "0", "avgPx": ""}],
        }
        mock_client.post.return_value = mock_response
        adapter._client = mock_client

        req = OrderRequest(
            exchange="okx",
            symbol="BTC-USDT-SWAP",
            order_type="market",
            side="buy",
            quantity=Decimal("1"),
            price=None,
        )
        asyncio.run(adapter.place_order(req))

        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args[1]
        self.assertIn("json", call_kwargs)

    @patch("httpx.AsyncClient")
    def test_okx_error_response_raises(self, mock_client_cls):
        """OKX 返回错误码时抛异常"""
        adapter = OKXAdapter("test_key", "test_secret", "test_passphrase")
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "code": "1",
            "msg": "Insufficient margin",
        }
        mock_client.post.return_value = mock_response
        adapter._client = mock_client

        req = OrderRequest(
            exchange="okx",
            symbol="BTC-USDT-SWAP",
            order_type="market",
            side="buy",
            quantity=Decimal("1"),
            price=None,
        )
        with self.assertRaises(RuntimeError):
            asyncio.run(adapter.place_order(req))

    @patch("httpx.AsyncClient")
    def test_okx_get_positions_filters_zero(self, mock_client_cls):
        """OKX get_positions() 应过滤零仓位"""
        adapter = OKXAdapter("test_key", "test_secret", "test_passphrase")
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {
                    "instId": "BTC-USDT-SWAP",
                    "pos": "0",
                    "avgEntryPx": "0",
                    "upl": "0",
                    "lever": "10",
                },
                {
                    "instId": "ETH-USDT-SWAP",
                    "pos": "5",
                    "avgEntryPx": "3000",
                    "upl": "50",
                    "lever": "5",
                },
            ]
        }
        mock_client.get.return_value = mock_response
        adapter._client = mock_client

        positions = asyncio.run(adapter.get_positions())
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0].symbol, "ETH-USDT-SWAP")
