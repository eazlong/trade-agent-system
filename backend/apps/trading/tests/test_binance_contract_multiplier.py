"""乘数合约（1000PEPEUSDT）的口径换算回归。

2026-11-27 txy 事故：paper 会话按现货名 `PEPE/USDT` 下单，币安 USD-M 合约里这个
品种叫 `1000PEPEUSDT`（每张 = 1000 PEPE），根本没有 `PEPEUSDT` → 适配器在
exchangeInfo 处 fail-closed 拒单（拦得对），但这条策略永远出不了单，持仓对账也按
`PEPEUSDT` 精确匹配 → 台账每小时被下钳成 0。

这里钉住四件事：
1. 映射表**不按名字前缀猜**（`1INCH`、`0G`、`1000SATS` 都不能被当成乘数合约）；
2. 下单数量 = 用户口径 ÷ 乘数、再按 stepSize 向下取整；委托价 = 用户价 × 乘数；
3. 成交/持仓/取价/精度规则一律换回**用户口径**（乘数 1 的品种逐位不变）；
4. 品种不在执行场所时仍然 fail-closed，且报错说的是「品种不存在」而不是「精度取不到」。
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from django.test import SimpleTestCase

from apps.strategy_engine.live_mode import LiveStrategyRunner, position_matches
from apps.trading.adapters.base import OrderRequest
from apps.trading.adapters.binance import BinanceAdapter
from apps.trading.adapters.binance_contracts import (
    contract_style_error,
    from_contract,
    to_contract,
)

PEPE_VENUE = "1000PEPEUSDT"


class _Resp:
    def __init__(self, status: int, payload=None, text: str = ""):
        self.status_code = status
        self._payload = (
            payload if payload is not None else ({} if status != 200 else [])
        )
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}: {self.text}")


def _pepe_rules() -> dict:
    return {
        PEPE_VENUE: {
            "stepSize": Decimal("1"),
            "tickSize": Decimal("0.0000001"),
            "minQty": Decimal("1"),
            "minNotional": Decimal("5"),
        },
        "BTCUSDT": {
            "stepSize": Decimal("0.001"),
            "tickSize": Decimal("0.1"),
            "minQty": Decimal("0.001"),
            "minNotional": Decimal("50"),
        },
    }


def _req(symbol: str = "PEPE/USDT", qty: str = "139159659.090909", price=None):
    return OrderRequest(
        exchange="binance",
        symbol=symbol,
        order_type="market",
        side="buy",
        quantity=Decimal(qty),
        price=Decimal(price) if price is not None else None,
    )


class TestContractMapping(SimpleTestCase):
    """映射表是显式的：不认识的名字一律原样、乘数 1（交给 exchangeInfo 判存在性）。"""

    def test_spot_name_maps_to_prefixed_contract(self):
        c = to_contract("PEPE/USDT")
        self.assertEqual(c.venue_symbol, PEPE_VENUE)
        self.assertEqual(c.multiplier, Decimal("1000"))
        self.assertTrue(c.needs_conversion)

    def test_without_slash_and_lowercase(self):
        for raw in ("PEPEUSDT", "pepe/usdt", " Pepe/Usdt "):
            with self.subTest(raw=raw):
                self.assertEqual(to_contract(raw).venue_symbol, PEPE_VENUE)

    def test_contract_style_configuration_is_the_same_order(self):
        """写 `PEPE/USDT` 与写 `1000PEPE/USDT` 是**同一笔单**（同乘数、同合约）。

        若这里取乘数 1，写路径会按「张」直发、读路径却按「个」换算——同一笔仓位在
        台账上差 1000 倍（review 2026-11-27 抓到的写读不对称）。
        """
        spot_style = to_contract("PEPE/USDT")
        contract_style = to_contract("1000PEPE/USDT")
        self.assertEqual(contract_style.venue_symbol, PEPE_VENUE)
        self.assertEqual(contract_style.multiplier, spot_style.multiplier)
        self.assertEqual(contract_style.multiplier, Decimal("1000"))

    def test_real_tickers_are_not_mistaken_for_multiplier_contracts(self):
        """`1INCH`/`0G`/`2Z` 是真名；把它们当前缀乘数会放大 1000 倍。"""
        for raw, venue in (
            ("1INCH/USDT", "1INCHUSDT"),
            ("0G/USDT", "0GUSDT"),
            ("2Z/USDT", "2ZUSDT"),
            ("BTC/USDT", "BTCUSDT"),
        ):
            with self.subTest(raw=raw):
                c = to_contract(raw)
                self.assertEqual(c.venue_symbol, venue)
                self.assertEqual(c.multiplier, Decimal("1"))

    def test_reverse_mapping_never_strips_a_real_prefix(self):
        self.assertEqual(from_contract(PEPE_VENUE), ("PEPEUSDT", Decimal("1000")))
        self.assertEqual(from_contract("BTCUSDT"), ("BTCUSDT", Decimal("1")))
        # 现货与合约同名（1000SATS 在两边都带前缀）→ 恒等，剥前缀会造出不存在的品种
        self.assertEqual(from_contract("1000SATSUSDT"), ("1000SATSUSDT", Decimal("1")))


class TestOrderQuantityConversion(SimpleTestCase):
    def setUp(self):
        self.a = BinanceAdapter("key", "secret", testnet=True)
        self.a._leverage_set.add(PEPE_VENUE)

    def _order_posts(self, client) -> list:
        return [c for c in client.post.call_args_list if "/order" in str(c.args[0])]

    def test_quantity_is_divided_by_multiplier_and_floored_to_step(self):
        """139,159,659.09 个 PEPE → 139,159 张（stepSize=1 向下取整）+ 成交换算回 1.39 亿个。"""
        client = AsyncMock()
        client.get.return_value = _Resp(
            200,
            {
                "symbols": [
                    {
                        "symbol": PEPE_VENUE,
                        "filters": [
                            {"filterType": "LOT_SIZE", "stepSize": "1", "minQty": "1"},
                            {"filterType": "PRICE_FILTER", "tickSize": "0.0000001"},
                        ],
                    }
                ]
            },
        )
        client.post.return_value = _Resp(
            200,
            {
                "orderId": 7,
                "status": "FILLED",
                "executedQty": "139159",
                "avgPrice": "0.0107000",
                "origQty": "139159",
                "symbol": PEPE_VENUE,
                "side": "BUY",
            },
        )
        self.a._client = client

        resp = asyncio.run(self.a.place_order(_req()))

        posts = self._order_posts(client)
        self.assertEqual(len(posts), 1, client.post.call_args_list)
        url = str(posts[0].args[0])
        self.assertIn(f"symbol={PEPE_VENUE}", url)
        self.assertIn("quantity=139159", url)
        self.assertNotIn("quantity=139159659", url)

        # 响应换回用户口径：张数 × 1000、每张价格 ÷ 1000
        self.assertEqual(resp.filled_qty, Decimal("139159000"))
        self.assertEqual(resp.avg_price, Decimal("0.0000107"))
        self.assertEqual(resp.quantity, Decimal("139159000"))
        self.assertEqual(resp.symbol, "PEPEUSDT")

    def test_limit_price_is_multiplied_by_multiplier(self):
        client = AsyncMock()
        client.get.return_value = _Resp(
            200,
            {
                "symbols": [
                    {
                        "symbol": PEPE_VENUE,
                        "filters": [
                            {"filterType": "LOT_SIZE", "stepSize": "1", "minQty": "1"},
                            {"filterType": "PRICE_FILTER", "tickSize": "0.0000001"},
                        ],
                    }
                ]
            },
        )
        client.post.return_value = _Resp(
            200,
            {"orderId": 8, "status": "NEW", "executedQty": "0", "avgPrice": "0"},
        )
        self.a._client = client

        asyncio.run(self.a.place_order(_req(qty="1000000", price="0.0000107")))

        url = str(self._order_posts(client)[0].args[0])
        # 0.0000107 × 1000 = 0.0107（按 tickSize 1e-7 对齐后仍是 0.0107）
        self.assertIn("price=0.0107", url)

    def test_unknown_symbol_still_fails_closed_and_says_why(self):
        """品种不在执行场所 → 拒单，且文案区分「品种不存在」与「精度取不到」。"""
        client = AsyncMock()
        client.get.return_value = _Resp(
            200, {"symbols": [{"symbol": "BTCUSDT", "filters": []}]}
        )
        self.a._client = client

        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(self.a.place_order(_req()))

        message = str(ctx.exception)
        self.assertIn("品种不在该执行场所", message)
        self.assertIn(PEPE_VENUE, message)
        self.assertEqual(self._order_posts(client), [])


class TestReadPathsConvertBackToUserScale(SimpleTestCase):
    def setUp(self):
        self.a = BinanceAdapter("key", "secret", testnet=True)
        self.a._symbol_rules = _pepe_rules()

    def test_mid_price_is_divided_by_multiplier(self):
        client = AsyncMock()
        client.get.return_value = _Resp(
            200, {"bidPrice": "0.0106900", "askPrice": "0.0107100"}
        )
        self.a._client = client

        price = asyncio.run(self.a.fetch_mid_price("PEPE/USDT"))

        self.assertIn(
            f"symbol={PEPE_VENUE}", str(client.get.call_args_list[-1].args[0])
        )
        self.assertEqual(price, Decimal("0.0000107"))

    def test_symbol_rules_and_min_notional_are_user_scale(self):
        """分片调用方手里的数量是「基础币个数」，所以 step/min_qty 必须乘回乘数。"""
        rules = asyncio.run(self.a.symbol_rules("PEPE/USDT"))
        self.assertEqual(rules.step_size, Decimal("1000"))
        self.assertEqual(rules.min_qty, Decimal("1000"))
        self.assertEqual(asyncio.run(self.a.min_notional("PEPE/USDT")), Decimal("5"))

        # 乘数 1 的品种逐位不变
        btc = asyncio.run(self.a.symbol_rules("BTC/USDT"))
        self.assertEqual(btc.step_size, Decimal("0.001"))
        self.assertEqual(btc.min_qty, Decimal("0.001"))

    def test_positions_are_translated_to_user_scale(self):
        client = AsyncMock()
        client.get.return_value = _Resp(
            200,
            [
                {
                    "symbol": PEPE_VENUE,
                    "positionAmt": "139159",
                    "entryPrice": "0.0107",
                    "unRealizedProfit": "69.5795",
                    "leverage": "5",
                    "markPrice": "0.0112",
                },
                {"symbol": "BTCUSDT", "positionAmt": "0", "entryPrice": "0"},
            ],
        )
        self.a._client = client

        positions = asyncio.run(self.a.get_positions())

        self.assertEqual(len(positions), 1, "空仓不应出现在持仓表里")
        pos = positions[0]
        self.assertEqual(pos.symbol, "PEPEUSDT")
        self.assertEqual(pos.venue_symbol, PEPE_VENUE)
        self.assertEqual(pos.quantity, Decimal("139159000"))
        self.assertEqual(pos.entry_price, Decimal("0.0000107"))
        self.assertEqual(pos.mark_price, Decimal("0.0000112"))
        # 名义价值换算前后相等：139159 张 × 0.0112 = 139159000 个 × 0.0000112
        self.assertEqual(
            pos.quantity * pos.mark_price,
            Decimal("139159") * Decimal("0.0112"),
        )

    def test_fetch_order_translates_fill_back(self):
        client = AsyncMock()
        client.get.return_value = _Resp(
            200,
            {
                "status": "FILLED",
                "executedQty": "139159",
                "avgPrice": "0.0107",
            },
        )
        self.a._client = client

        fill = asyncio.run(self.a.fetch_order("1", "PEPE/USDT"))

        self.assertEqual(fill.filled_quantity, Decimal("139159000"))
        self.assertEqual(fill.avg_fill_price, Decimal("0.0000107"))


class TestPositionReconciliation(SimpleTestCase):
    """live_mode 的对账要同时认「现货名配置」与「合约名配置」两种形态。"""

    def _pos(self, symbol: str, venue_symbol: str = ""):
        return SimpleNamespace(symbol=symbol, venue_symbol=venue_symbol)

    def test_spot_style_session_matches_user_scale_position(self):
        pos = self._pos("PEPEUSDT", PEPE_VENUE)
        self.assertTrue(position_matches("PEPE/USDT", pos))

    def test_contract_style_session_matches_through_venue_symbol(self):
        pos = self._pos("PEPEUSDT", PEPE_VENUE)
        self.assertTrue(position_matches("1000PEPE/USDT", pos))

    def test_other_symbol_does_not_match(self):
        pos = self._pos("PEPEUSDT", PEPE_VENUE)
        self.assertFalse(position_matches("BTC/USDT", pos))

    def test_empty_session_symbol_never_matches(self):
        self.assertFalse(position_matches("", self._pos("PEPEUSDT", PEPE_VENUE)))


class TestWriteReadSymmetry(SimpleTestCase):
    """写路径与读路径必须同一个乘数——否则同一笔仓位在台账上差 1000 倍。"""

    def setUp(self):
        self.a = BinanceAdapter("key", "secret", testnet=True)
        self.a._symbol_rules = _pepe_rules()
        self.a._leverage_set.add(PEPE_VENUE)

    def _client(self, post_payload=None):
        client = AsyncMock()
        client.get.return_value = _Resp(
            200,
            {"symbols": [{"symbol": PEPE_VENUE, "filters": []}]},
        )
        client.post.return_value = _Resp(
            200,
            post_payload
            or {
                "orderId": 9,
                "status": "NEW",
                "executedQty": "139159",
                "avgPrice": "0.0107",
                "origQty": "139159",
                "symbol": PEPE_VENUE,
                "side": "BUY",
            },
        )
        self.a._client = client
        return client

    def _order_post_url(self, client) -> str:
        posts = [c for c in client.post.call_args_list if "/order" in str(c.args[0])]
        self.assertEqual(len(posts), 1, client.post.call_args_list)
        return str(posts[0].args[0])

    def test_contract_style_order_sends_the_same_quantity(self):
        """1000PEPE/USDT 与 PEPE/USDT 配置下发的数量必须一致（都按基础币个数 ÷ 1000）。"""
        client = self._client()
        asyncio.run(self.a.place_order(_req(symbol="1000PEPE/USDT")))
        url = self._order_post_url(client)
        self.assertIn(f"symbol={PEPE_VENUE}", url)
        self.assertIn("quantity=139159", url)

    def test_quantity_below_one_contract_fails_before_sending(self):
        """500 个 PEPE = 0.5 张 → 取整成 0：必须在下单前抛错，绝不发单。"""
        client = self._client()
        with self.assertRaises(ValueError):
            asyncio.run(self.a.place_order(_req(qty="500")))
        self.assertEqual(
            [c for c in client.post.call_args_list if "/order" in str(c.args[0])], []
        )

    def test_open_orders_use_venue_symbol_and_user_scale(self):
        client = AsyncMock()
        client.get.return_value = _Resp(
            200,
            [
                {
                    "orderId": 3,
                    "status": "NEW",
                    "executedQty": "0",
                    "avgPrice": "0",
                    "origQty": "139159",
                    "symbol": PEPE_VENUE,
                    "side": "BUY",
                }
            ],
        )
        self.a._client = client

        orders = asyncio.run(self.a.fetch_open_orders("PEPE/USDT"))

        self.assertIn(
            f"symbol={PEPE_VENUE}", str(client.get.call_args_list[-1].args[0])
        )
        self.assertEqual(orders[0].quantity, Decimal("139159000"))
        self.assertEqual(orders[0].symbol, "PEPEUSDT")

    def test_reconcile_result_is_in_user_scale(self):
        """下单结果不明 → 按 clientOrderId 对账命中时，返回的量价也要换算。"""
        self.a.find_order_by_client_id = AsyncMock(
            return_value={
                "orderId": 11,
                "status": "NEW",
                "executedQty": "139159",
                "avgPrice": "0.0107",
                "origQty": "139159",
                "symbol": PEPE_VENUE,
                "side": "BUY",
            }
        )
        request = _req()
        request.client_order_id = "cid-1"

        resp = asyncio.run(self.a._reconcile_order(request, None))

        self.assertEqual(resp.filled_qty, Decimal("139159000"))
        self.assertEqual(resp.avg_price, Decimal("0.0000107"))
        self.assertEqual(resp.quantity, Decimal("139159000"))
        self.assertEqual(resp.symbol, "PEPEUSDT")

    def test_user_trades_are_user_scale_but_quote_and_pnl_untouched(self):
        client = AsyncMock()
        client.get.return_value = _Resp(
            200,
            [
                {
                    "id": 1,
                    "orderId": 5,
                    "qty": "139159",
                    "price": "0.0107",
                    "quoteQty": "1489.0013",
                    "realizedPnl": "1.25",
                    "commission": "0.7445",
                }
            ],
        )
        self.a._client = client

        trades = asyncio.run(self.a.fetch_user_trades("PEPE/USDT"))

        self.assertEqual(trades[0]["qty"], "139159000")
        self.assertEqual(trades[0]["price"], "0.0000107")
        # 名义额、盈亏、手续费是 USDT，与乘数无关
        self.assertEqual(trades[0]["quoteQty"], "1489.0013")
        self.assertEqual(trades[0]["realizedPnl"], "1.25")
        self.assertEqual(trades[0]["commission"], "0.7445")

    def test_stop_loss_price_is_converted(self):
        client = self._client()
        request = _req(qty="1000000", price="0.0000107")
        request.stop_loss = Decimal("0.0000095")

        asyncio.run(self.a.place_order(request))

        self.assertIn("stopPrice=0.0095", self._order_post_url(client))


class TestContractStyleConfigurationIsRefused(SimpleTestCase):
    """会话配置只接受**现货名**：合约名写法会让下单数量与 K 线口径差一个乘数。"""

    def test_spot_name_is_accepted(self):
        self.assertIsNone(contract_style_error("PEPE/USDT"))
        self.assertIsNone(contract_style_error("BTC/USDT"))
        # 现货与合约同名的品种（1000SATS）本来就该这么配，不能被误拦
        self.assertIsNone(contract_style_error("1000SATS/USDT"))

    def test_contract_name_is_refused_with_the_right_name(self):
        error = contract_style_error("1000PEPE/USDT")
        self.assertIsNotNone(error)
        self.assertIn("PEPE/USDT", error)
        self.assertIn("1000PEPE", error)

    def test_runner_refuses_to_start_on_contract_style_symbol(self):
        runner = object.__new__(LiveStrategyRunner)  # 只测这一条校验，绕过构造
        runner.symbol = "1000PEPE/USDT"
        with self.assertRaises(ValueError):
            runner._reject_contract_style_symbol()

        runner.symbol = "PEPE/USDT"
        runner._reject_contract_style_symbol()  # 不抛
