"""②d 机械层：``apps/trading/reduce.py``。

这一层是「怎么在**某一个账户**上把事做出来」，与「该不该做、减多少、通知谁」无关，
所以它可以整层用一个假适配器测完——**这里一次都不碰 ``ADAPTER_MAP``**。那正是把
机械层与策略层分开的直接收益：按交易所名解析适配器的那条老路（``OrderExecutor``）
在同名双账户下会拿错账户，而假适配器让「账户」这个概念根本不进入这一层的接口。

钉住的都是「错了不会报错、只会静默变成另一个意思」的地方：``unfinished`` 被读成
``rejected``（「不知道」→「确定没成交」）、枚举失败被读成「没有挂单」、取价失败被
读成「滑点完美」、撤单失败被读成「撤干净了」。
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock

from django.test import SimpleTestCase

from apps.trading.adapters.base import (
    BaseExchangeAdapter,
    OrderResponse,
    Position,
)
from apps.trading.reduce import (
    SHARD_STATUS_FILLED,
    SHARD_STATUS_PARTIAL,
    SHARD_STATUS_REJECTED,
    SHARD_STATUS_UNFINISHED,
    ShardOrder,
    account_positions,
    cancel_opening_orders,
    enumerate_open_orders,
    is_opening_order,
    mid_price,
    normalize_symbol,
    place_reduce_shard,
    slippage_pct,
    volume_weighted_price,
)

_SYMBOL = "DOGE/USDT"
_RAW = "DOGEUSDT"


def _resp(
    *, status: str = "FILLED", filled: str = "100", avg: str | None = "1", oid: str = "77"
) -> OrderResponse:
    return OrderResponse(
        exchange_order_id=oid,
        status=status,
        filled_qty=Decimal(filled),
        avg_price=Decimal(avg) if avg is not None else None,
        fee=None,
        raw={},
    )


def _order(qty: str = "100", side: str = "sell", cid: str = "cid-1") -> ShardOrder:
    return ShardOrder(client_order_id=cid, symbol=_SYMBOL, side=side, quantity=Decimal(qty))


class _FakeAdapter(BaseExchangeAdapter):
    """照脚本回答的适配器：``place_script`` 里每项要么是异常（抛），要么是响应（返回）。"""

    def __init__(
        self,
        *,
        place_script=None,
        positions=None,
        open_orders=None,
        enum_error=None,
        cancel_ok: bool = True,
        cancel_error: Exception | None = None,
        mid="1",
        mid_error: Exception | None = None,
    ):
        super().__init__("key", "secret")
        self.place_script = list(place_script or [])
        self.positions = list(positions or [])
        self.open_orders = dict(open_orders or {})
        self.enum_error = dict(enum_error or {})
        self.cancel_ok = cancel_ok
        self.cancel_error = cancel_error
        self.mid = Decimal(mid) if mid is not None else None
        self.mid_error = mid_error
        self.placed = []
        self.cancelled = []
        self.enumerated = []

    async def connect(self) -> None:  # pragma: no cover - 不参与本层
        return None

    async def disconnect(self) -> None:  # pragma: no cover
        return None

    async def get_balance(self):
        return {}

    async def fetch_order(self, exchange_order_id, symbol):  # pragma: no cover
        raise NotImplementedError

    async def place_order(self, request):
        self.placed.append(request)
        if not self.place_script:
            raise AssertionError("place_script 用完了：这一层多发了一次单")
        item = self.place_script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def cancel_order(self, exchange_order_id, symbol) -> bool:
        self.cancelled.append((exchange_order_id, symbol))
        if self.cancel_error is not None:
            raise self.cancel_error
        return self.cancel_ok

    async def fetch_open_orders(self, symbol):
        self.enumerated.append(symbol)
        if symbol in self.enum_error:
            raise self.enum_error[symbol]
        return list(self.open_orders.get(symbol, []))

    async def get_positions(self):
        return list(self.positions)

    async def fetch_mid_price(self, symbol):
        if self.mid_error is not None:
            raise self.mid_error
        return self.mid


# --------------------------------------------------------------------------- #
# 品种名与开仓判据
# --------------------------------------------------------------------------- #


class TestNormalizeSymbol(SimpleTestCase):
    def test_slash_is_stripped_and_case_is_folded(self):
        self.assertEqual(normalize_symbol("doge/usdt"), _RAW)
        self.assertEqual(normalize_symbol(_RAW), _RAW)


class TestIsOpeningOrder(SimpleTestCase):
    """CONTEXT.md:122 的判据：与当前持仓相反 = 开仓；没有持仓则全部是开仓意图。"""

    def test_no_position_means_every_order_is_opening(self):
        for side in ("buy", "sell", "BUY", "SELL"):
            with self.subTest(side=side):
                self.assertTrue(is_opening_order(side, None))

    def test_opposite_side_of_the_position_is_opening(self):
        self.assertTrue(is_opening_order("buy", "long"))
        self.assertTrue(is_opening_order("sell", "short"))

    def test_same_direction_as_the_position_is_not_opening(self):
        """与持仓相反的是**平仓**：平多卖出、平空买入，这两张单本来就在减仓。"""
        self.assertFalse(is_opening_order("sell", "long"))
        self.assertFalse(is_opening_order("buy", "short"))

    def test_unknown_side_or_position_side_fails_closed(self):
        """认不出的方向按开仓处理：漏撤一张开仓单的代价比误撤一张平仓单大。"""
        for side, pos in (("", "long"), ("buy_stop", "long"), ("buy", ""), ("buy", "flat")):
            with self.subTest(side=side, pos=pos):
                self.assertTrue(is_opening_order(side, pos))


# --------------------------------------------------------------------------- #
# 子单：只减不增 + 同一单号重发
# --------------------------------------------------------------------------- #


class TestPlaceReduceShard(SimpleTestCase):
    def test_request_carries_both_hard_prerequisites(self):
        """``reduce_only`` 与确定的 ``client_order_id`` 是同级硬前置（CONTEXT.md:128）。"""
        adapter = _FakeAdapter(place_script=[_resp()])

        asyncio.run(place_reduce_shard(adapter, _order()))

        self.assertEqual(len(adapter.placed), 1)
        sent = adapter.placed[0]
        self.assertTrue(sent.reduce_only)
        self.assertEqual(sent.client_order_id, "cid-1")
        self.assertEqual(sent.order_type, "market")
        self.assertEqual(sent.symbol, _SYMBOL)

    def test_retry_reuses_the_identical_client_order_id(self):
        """重发不消耗新的幂等配额，靠的就是单号**逐字相同**（CONTEXT.md:125）。"""
        adapter = _FakeAdapter(
            place_script=[RuntimeError("read timeout"), _resp()]
        )

        result = asyncio.run(place_reduce_shard(adapter, _order()))

        self.assertEqual([r.client_order_id for r in adapter.placed], ["cid-1", "cid-1"])
        self.assertEqual(result.sends, 2)
        self.assertEqual(result.status, SHARD_STATUS_FILLED)

    def test_argument_rejection_is_settled_immediately(self):
        """参数类错误（``ValueError``）再发两次只会得到同一个拒绝，所以不重发。"""
        adapter = _FakeAdapter(place_script=[ValueError("数量取整为 0"), _resp()])

        result = asyncio.run(place_reduce_shard(adapter, _order()))

        self.assertEqual(result.status, SHARD_STATUS_REJECTED)
        self.assertEqual(result.sends, 1)
        self.assertEqual(len(adapter.placed), 1)

    def test_exhaustion_is_unfinished_never_rejected(self):
        """发满了仍无结论 = **可能有一张活单**，与「确定没成交」是两件事。

        这条一旦退化成 ``rejected``，调用方会以为这一片没发出去、继续往下发，
        而那张活单可能已经成交了一部分——减仓减过头，且没有任何自动路径能补回来。
        """
        adapter = _FakeAdapter(
            place_script=[RuntimeError("a"), RuntimeError("b"), RuntimeError("c")]
        )

        result = asyncio.run(place_reduce_shard(adapter, _order()))

        self.assertEqual(result.status, SHARD_STATUS_UNFINISHED)
        self.assertFalse(result.settled)
        self.assertEqual(result.sends, 3)
        self.assertEqual(result.filled_qty, Decimal("0"))
        self.assertEqual(len(adapter.placed), 3)

    def test_no_more_than_three_sends(self):
        adapter = _FakeAdapter(place_script=[RuntimeError("x")] * 5)

        asyncio.run(place_reduce_shard(adapter, _order()))

        self.assertEqual(len(adapter.placed), 3)

    def test_partial_fill_is_a_settled_result(self):
        adapter = _FakeAdapter(
            place_script=[_resp(status="PARTIALLY_FILLED", filled="40", avg="1")]
        )

        result = asyncio.run(place_reduce_shard(adapter, _order()))

        self.assertEqual(result.status, SHARD_STATUS_PARTIAL)
        self.assertTrue(result.settled)
        self.assertEqual(result.filled_qty, Decimal("40"))

    def test_zero_fill_reads_as_rejected_even_if_the_exchange_says_new(self):
        """交易所回 ``NEW`` 却成交 0：对减仓而言这一片**没有成交**是确定的事实。"""
        adapter = _FakeAdapter(place_script=[_resp(status="NEW", filled="0")])

        result = asyncio.run(place_reduce_shard(adapter, _order()))

        self.assertEqual(result.status, SHARD_STATUS_REJECTED)
        self.assertTrue(result.settled)

    def test_missing_avg_price_does_not_become_zero(self):
        """成交了但没给成交价：``avg_price`` 保持 ``None``，不落成 0（那会污染加权均价）。"""
        adapter = _FakeAdapter(place_script=[_resp(filled="100", avg=None)])

        result = asyncio.run(place_reduce_shard(adapter, _order()))

        self.assertIsNone(result.avg_price)
        self.assertEqual(result.status, SHARD_STATUS_FILLED)


# --------------------------------------------------------------------------- #
# 枚举挂单
# --------------------------------------------------------------------------- #


class TestEnumerateOpenOrders(SimpleTestCase):
    def test_rows_are_parsed_and_the_symbol_is_normalized(self):
        adapter = _FakeAdapter(
            open_orders={
                _SYMBOL: [
                    _resp(status="NEW", filled="0", avg="0", oid="11"),
                ]
            }
        )
        adapter.open_orders[_SYMBOL][0].symbol = _RAW
        adapter.open_orders[_SYMBOL][0].side = "BUY"
        adapter.open_orders[_SYMBOL][0].quantity = Decimal("10")

        report = asyncio.run(enumerate_open_orders(adapter, [_SYMBOL]))

        self.assertTrue(report.complete)
        self.assertEqual(len(report.orders), 1)
        self.assertEqual(report.orders[0].symbol, _RAW)
        self.assertEqual(report.orders[0].side, "buy")
        self.assertEqual(report.orders[0].quantity, Decimal("10"))

    def test_one_failing_symbol_does_not_stop_the_others(self):
        adapter = _FakeAdapter(
            open_orders={_SYMBOL: []},
            enum_error={"BTC/USDT": RuntimeError("boom")},
        )

        report = asyncio.run(enumerate_open_orders(adapter, [_SYMBOL, "BTC/USDT"]))

        self.assertFalse(report.complete)
        self.assertIn("BTC/USDT", report.failed)
        self.assertIn("boom", report.failed["BTC/USDT"])
        # 两个品种都问过：一个失败不影响另一个
        self.assertEqual(adapter.enumerated, [_SYMBOL, "BTC/USDT"])

    def test_failed_enumeration_is_never_the_same_as_an_empty_one(self):
        """「没枚举成」与「确实一张都没有」必须能分开——混起来的后果是
        一次网络抖动被读成「敞口没有增加的风险」。
        """
        failed = asyncio.run(
            enumerate_open_orders(
                _FakeAdapter(enum_error={_SYMBOL: RuntimeError("boom")}), [_SYMBOL]
            )
        )
        empty = asyncio.run(
            enumerate_open_orders(_FakeAdapter(open_orders={_SYMBOL: []}), [_SYMBOL])
        )

        self.assertFalse(failed.complete)
        self.assertTrue(empty.complete)
        self.assertEqual(failed.orders, [])
        self.assertEqual(empty.orders, [])

    def test_closing_orders_are_left_alone(self):
        """有持仓时，与持仓同向的挂单是**平仓**意图，不在撤单范围内。"""
        adapter = _FakeAdapter(open_orders={_SYMBOL: []})
        row = _resp(status="NEW", filled="0", avg="0", oid="12")
        row.symbol, row.side, row.quantity = _RAW, "SELL", Decimal("5")
        adapter.open_orders[_SYMBOL] = [row]

        report = asyncio.run(
            enumerate_open_orders(adapter, [_SYMBOL], position_side={_RAW: "long"})
        )

        self.assertEqual(report.orders, [])

    def test_unknown_side_on_a_position_row_is_still_enumerated(self):
        """方向缺失 → 按开仓处理（fail-closed），不会被读成「可以不撤」。"""
        adapter = _FakeAdapter(open_orders={_SYMBOL: []})
        row = _resp(status="NEW", filled="0", avg="0", oid="13")
        row.symbol, row.side, row.quantity = _RAW, "", Decimal("5")
        adapter.open_orders[_SYMBOL] = [row]

        report = asyncio.run(
            enumerate_open_orders(adapter, [_SYMBOL], position_side={_RAW: "long"})
        )

        self.assertEqual(len(report.orders), 1)


# --------------------------------------------------------------------------- #
# 撤单
# --------------------------------------------------------------------------- #


def _row(oid: str = "11", side: str = "BUY") -> OrderResponse:
    row = _resp(status="NEW", filled="0", avg="0", oid=oid)
    row.symbol, row.side, row.quantity = _RAW, side, Decimal("10")
    return row


class TestCancelOpeningOrders(SimpleTestCase):
    def test_each_order_is_cancelled_serially(self):
        adapter = _FakeAdapter(open_orders={_SYMBOL: [_row("11"), _row("12")]})

        report = asyncio.run(cancel_opening_orders(adapter, [_SYMBOL]))

        self.assertEqual([r.order.exchange_order_id for r in report.cancelled], ["11", "12"])
        self.assertEqual(adapter.cancelled, [("11", _RAW), ("12", _RAW)])

    def test_a_raising_cancel_is_recorded_and_does_not_stop_the_rest(self):
        """撤单失败不阻拦减仓（CONTEXT.md:122），所以它是一条记录，不是一个异常。"""
        adapter = _FakeAdapter(open_orders={_SYMBOL: [_row("11"), _row("12")]})
        adapter.cancel_error = RuntimeError("boom")

        report = asyncio.run(cancel_opening_orders(adapter, [_SYMBOL]))

        self.assertEqual(len(report.failed), 2)
        self.assertIn("boom", report.failed[0].error)
        self.assertEqual(adapter.cancelled, [("11", _RAW), ("12", _RAW)])

    def test_a_false_return_means_not_cancelled(self):
        """交易所回非 200（-2011 之类）是「未确认撤销」，不能读成撤掉了。"""
        adapter = _FakeAdapter(open_orders={_SYMBOL: [_row()]}, cancel_ok=False)

        report = asyncio.run(cancel_opening_orders(adapter, [_SYMBOL]))

        self.assertEqual(len(report.failed), 1)
        self.assertEqual(report.failed[0].error, "交易所未确认撤销")

    def test_lines_name_the_uncancelled_orders(self):
        adapter = _FakeAdapter(open_orders={_SYMBOL: [_row("11"), _row("12")]})
        adapter.cancel_ok = False

        lines = asyncio.run(cancel_opening_orders(adapter, [_SYMBOL])).lines()

        self.assertTrue(any("**未撤下**" in line for line in lines), lines)

    def test_the_mandated_sentence_leads_when_enumeration_failed(self):
        """枚举失败那一句是**必写**的（CONTEXT.md:122），且它不是错误信息而是事实陈述。"""
        adapter = _FakeAdapter(
            open_orders={_SYMBOL: []}, enum_error={"BTC/USDT": RuntimeError("boom")}
        )

        report = asyncio.run(cancel_opening_orders(adapter, [_SYMBOL, "BTC/USDT"]))
        lines = report.lines()

        self.assertTrue(
            lines[0].startswith("**本次未能枚举挂单，敞口在窗口内仍可能增加**"), lines
        )
        self.assertIn("BTC/USDT", lines[0])

    def test_complete_and_empty_says_so_plainly(self):
        adapter = _FakeAdapter(open_orders={_SYMBOL: []})

        lines = asyncio.run(cancel_opening_orders(adapter, [_SYMBOL])).lines()

        self.assertEqual(lines, ["挂单：交易所侧没有开仓意图的挂单。"])

    def test_cancelled_orders_are_read_back_as_cancelled(self):
        adapter = _FakeAdapter(open_orders={_SYMBOL: [_row("11")]})

        outcome = asyncio.run(cancel_opening_orders(adapter, [_SYMBOL])).cancelled[0]

        self.assertTrue(outcome.cancelled)
        self.assertEqual(outcome.as_dict()["exchange_order_id"], "11")
        self.assertNotIn("未撤下", outcome.line())


# --------------------------------------------------------------------------- #
# 只读两件事：持仓与中间价
# --------------------------------------------------------------------------- #


def _position(symbol: str, side: str, qty: str) -> Position:
    return Position(
        symbol=symbol,
        side=side,
        quantity=Decimal(qty),
        entry_price=Decimal("1"),
        unrealized_pnl=Decimal("0"),
        leverage=1,
    )


class TestAccountPositions(SimpleTestCase):
    def test_zero_quantity_rows_are_dropped(self):
        adapter = _FakeAdapter(
            positions=[
                _position(_RAW, "long", "10"),
                _position("BTCUSDT", "long", "0"),
            ]
        )

        out = asyncio.run(account_positions(adapter))

        self.assertEqual(out, {_RAW: ("long", Decimal("10"))})

    def test_symbol_is_normalized(self):
        adapter = _FakeAdapter(positions=[_position(_SYMBOL, "short", "3")])

        self.assertIn(_RAW, asyncio.run(account_positions(adapter)))


class TestMidPrice(SimpleTestCase):
    def test_a_usable_price_is_returned_as_is(self):
        adapter = _FakeAdapter(mid="0.0701")
        self.assertEqual(asyncio.run(mid_price(adapter, _SYMBOL)), Decimal("0.0701"))

    def test_failure_reads_as_unavailable_never_zero(self):
        """0 会在滑点公式里变成「完美的价格」，而这条记录是减仓成本失控的唯一告警依据。"""
        for adapter in (
            _FakeAdapter(mid=None),
            _FakeAdapter(mid="0"),
            _FakeAdapter(mid="-1"),
            _FakeAdapter(mid_error=RuntimeError("boom")),
        ):
            with self.subTest(adapter=adapter):
                self.assertIsNone(asyncio.run(mid_price(adapter, _SYMBOL)))


# --------------------------------------------------------------------------- #
# 滑点
# --------------------------------------------------------------------------- #


def _fill(qty: str, price: str, side: str = "sell") -> object:
    from apps.trading.reduce import ShardResult

    return ShardResult(
        client_order_id="cid",
        symbol=_SYMBOL,
        side=side,
        requested_qty=Decimal(qty),
        filled_qty=Decimal(qty),
        avg_price=Decimal(price),
        status=SHARD_STATUS_FILLED,
        sends=1,
    )


class TestVolumeWeightedPrice(SimpleTestCase):
    def test_only_filled_sub_orders_participate(self):
        """被拒的、没发出去的那些按 0 成交量计进来，等于拿一个不存在的成交价去平均。"""
        from apps.trading.reduce import ShardResult

        rejected = ShardResult(
            client_order_id="c2",
            symbol=_SYMBOL,
            side="sell",
            requested_qty=Decimal("100"),
            filled_qty=Decimal("0"),
            avg_price=None,
            status=SHARD_STATUS_REJECTED,
            sends=1,
        )

        vwap = volume_weighted_price([_fill("1", "100"), _fill("3", "200"), rejected])

        self.assertEqual(vwap, Decimal("175"))

    def test_no_fills_reads_as_none(self):
        from apps.trading.reduce import ShardResult

        empty = ShardResult(
            client_order_id="c",
            symbol=_SYMBOL,
            side="sell",
            requested_qty=Decimal("1"),
            filled_qty=Decimal("0"),
            avg_price=None,
            status=SHARD_STATUS_REJECTED,
            sends=1,
        )

        self.assertIsNone(volume_weighted_price([]))
        self.assertIsNone(volume_weighted_price([empty]))


class TestSlippagePct(SimpleTestCase):
    def test_selling_below_the_reference_is_adverse_positive(self):
        """平多成交低于基准 = 不利，记正。"""
        self.assertEqual(slippage_pct(Decimal("100"), Decimal("99"), "sell"), Decimal("0.01"))

    def test_selling_above_the_reference_is_favourable_negative(self):
        self.assertEqual(slippage_pct(Decimal("100"), Decimal("101"), "sell"), Decimal("-0.01"))

    def test_buying_above_the_reference_is_adverse_positive(self):
        """平空成交高于基准 = 不利，同样记正——两边的「不利」必须同号，
        否则「要不要暂停自动减仓」这一问在两个方向上会得到相反的答案。
        """
        self.assertEqual(slippage_pct(Decimal("100"), Decimal("101"), "buy"), Decimal("0.01"))

    def test_missing_inputs_read_as_no_judgement(self):
        for ref, vwap in (
            (None, Decimal("100")),
            (Decimal("100"), None),
            (None, None),
            (Decimal("0"), Decimal("100")),
        ):
            with self.subTest(ref=ref, vwap=vwap):
                self.assertIsNone(slippage_pct(ref, vwap, "sell"))


# --------------------------------------------------------------------------- #
# 适配器侧新开的那个口（Q7）
# --------------------------------------------------------------------------- #


class _RawResp:
    def __init__(self, status: int, payload=None, text: str = ""):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


class TestBinanceSymbolRules(SimpleTestCase):
    """精度规则必须有公开口：分片要在**下单之前**知道取整完还剩多少（CONTEXT.md:127）。

    在 regime 侧自己解析一份规则，本地就会有两个关于「最小下单量是多少」的答案，
    而它们分叉的表现是一次分片成功、一次整片被拒。
    """

    def _adapter(self, rules):
        from apps.trading.adapters.binance import BinanceAdapter

        a = BinanceAdapter("key", "secret", testnet=True)
        a._symbol_rules = rules
        return a

    def test_rules_are_exposed_with_the_same_numbers_the_order_path_uses(self):
        a = self._adapter(
            {
                _RAW: {
                    "stepSize": Decimal("1"),
                    "tickSize": Decimal("0.01"),
                    "minQty": Decimal("5"),
                }
            }
        )

        rules = asyncio.run(a.symbol_rules(_SYMBOL))

        self.assertEqual(rules.step_size, Decimal("1"))
        self.assertEqual(rules.min_qty, Decimal("5"))

    def test_an_unknown_symbol_reads_as_none(self):
        """**取不到必须返回 ``None``**，绝不许拿一个编出来的 ``step_size`` 顶替。"""
        a = self._adapter({_RAW: {"stepSize": Decimal("1"), "minQty": Decimal("1")}})

        self.assertIsNone(asyncio.run(a.symbol_rules("BTC/USDT")))

    def test_the_base_contract_defaults_to_none(self):
        """基类默认「本适配器不声明该品种的精度规则」——与 ``min_notional`` 返回 0
        是同一句话的两种写法：**我们不知道**。
        """
        self.assertIsNone(asyncio.run(_FakeAdapter().symbol_rules(_SYMBOL)))

    def test_a_server_error_is_not_silently_swallowed_into_none(self):
        """规则表压根没加载起来时是**抛**（取不到 ≠ 没有这个品种），
        所以 ``None`` 只表示「规则表里确实没有它」这一种事实。
        """
        from apps.trading.adapters.binance import BinanceAdapter

        client = AsyncMock()
        client.get.return_value = _RawResp(500, text="boom")
        a = BinanceAdapter("key", "secret", testnet=True)
        a._client = client

        with self.assertRaises(RuntimeError):
            asyncio.run(a.symbol_rules(_SYMBOL))
