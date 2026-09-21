"""F2：精度规则不可用必须拒绝下单（不得把未归一化数量发出去 → -1111）。

2026-09-21 事故链：_load_symbol_rules 全捕获静默吞掉异常 → _normalize_quantity 原样
返回 → 5332.37953400 直发 DOGEUSDT（stepSize=1）→ 交易所 -1111。
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock

from django.test import SimpleTestCase

from apps.trading.adapters.base import OrderRequest
from apps.trading.adapters.binance import BinanceAdapter


class _Resp:
    def __init__(self, status: int, payload: dict | None = None, text: str = ""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def _req(symbol: str = "DOGE/USDT", qty: str = "5332.37953400") -> OrderRequest:
    return OrderRequest(
        exchange="binance",
        symbol=symbol,
        order_type="market",
        side="buy",
        quantity=Decimal(qty),
        price=None,
    )


class TestPrecisionRulesFailLoud(SimpleTestCase):
    def setUp(self):
        self.a = BinanceAdapter("key", "secret", testnet=True)
        self.a._leverage_set.add("DOGEUSDT")

    def _order_posts(self, client) -> list:
        return [c for c in client.post.call_args_list if "/order" in str(c.args[0])]

    def test_exchange_info_failure_refuses_order(self):
        """exchangeInfo 非 200 → 抛错，且绝不发下单请求。"""
        client = AsyncMock()
        client.get.return_value = _Resp(500, text="boom")
        self.a._client = client

        with self.assertRaises(RuntimeError):
            asyncio.run(self.a.place_order(_req()))

        self.assertEqual(self._order_posts(client), [])
        self.assertEqual(self.a._symbol_rules, None)

    def test_symbol_absent_from_rules_refuses_order(self):
        """规则里没有该 symbol（等价于精度未知）→ 拒绝下单。"""
        client = AsyncMock()
        self.a._client = client
        self.a._symbol_rules = {
            "BTCUSDT": {
                "stepSize": Decimal("0.001"),
                "tickSize": Decimal("0.01"),
                "minQty": Decimal("0.001"),
            }
        }

        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(self.a.place_order(_req()))

        self.assertIn("精度规则不可用", str(ctx.exception))
        self.assertEqual(self._order_posts(client), [])

    def test_valid_rules_still_normalize_and_send(self):
        """规则可用时行为不变：按 stepSize 归一化后正常下单。"""
        client = AsyncMock()
        client.post.return_value = _Resp(
            200,
            {"orderId": 1, "status": "NEW", "executedQty": "0", "avgPrice": "0"},
        )
        client.get.return_value = _Resp(
            200,
            {
                "symbols": [
                    {
                        "symbol": "DOGEUSDT",
                        "filters": [
                            {"filterType": "LOT_SIZE", "stepSize": "1", "minQty": "1"},
                            {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                        ],
                    }
                ]
            },
        )
        self.a._client = client

        asyncio.run(self.a.place_order(_req()))

        posts = self._order_posts(client)
        self.assertEqual(len(posts), 1, client.post.call_args_list)
        self.assertIn("quantity=5332", posts[0].args[0])
        self.assertNotIn("5332.37953400", posts[0].args[0])
