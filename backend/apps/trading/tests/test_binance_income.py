"""`BinanceAdapter.fetch_income` / `get_balance_detail` 的测试面。

这两个口是「余额变了、订单里没有」那条问题的数据来源，所以钉的都是**口径与分页**
里最容易出错、且出错后**看起来完全正常**的几处：

1. **空 ≠ 失败**：拉不到就抛，绝不返回空列表。把「不支持/超时」读成「该区间没有流水」，
   会让一次没查成的对账变成一句「什么都没发生」——这是本次修复要消灭的那类沉默。
2. **不给起点只回 7 天**：所以 `startTime` 必须原样向下传（`None` 就是不传），
   不许偷偷补一个默认起点。
3. **区间超 limit 时只回前 `limit` 条**：分页必须靠把 `startTime` 推到「本页最后一笔
   的时间 +1ms」，而不是靠不存在的 `fromId`；同一毫秒多笔靠 `tranId` 去重。
4. **游标不前进就停**：否则死循环，而外表看起来只是「卡住了」。

另外钉住 `get_balance_detail` 的**不编数**：非数字字段（`updateTime`）必须被跳过，
而不是落进字典冒充金额。
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.trading.adapters.binance import BinanceAdapter


def _response(payload, status_code=200, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload
    resp.text = text
    resp.raise_for_status = MagicMock()
    return resp


def _adapter(client) -> BinanceAdapter:
    adapter = BinanceAdapter("k", "s")
    # `_client_loop is None` 时请求走注入的 `_client`（见适配器里那条注释）
    adapter._client = client
    return adapter


class TestFetchIncomePagination:
    def test_single_page_returns_all_rows(self):
        rows = [
            {
                "incomeType": "FUNDING_FEE",
                "income": "-0.5",
                "time": 1000,
                "tranId": "a",
            },
            {"incomeType": "TRANSFER", "income": "100", "time": 2000, "tranId": "b"},
        ]
        client = AsyncMock()
        client.get.return_value = _response(rows)

        got = asyncio.run(_adapter(client).fetch_income(limit=1000))

        assert len(got) == 2
        assert got[0]["incomeType"] == "FUNDING_FEE"

    def test_start_time_is_passed_through_not_defaulted(self):
        """不给起点就是不传：交易所默认只回 7 天，而「我查了 7 天」不能伪装成「查了全部」。"""
        client = AsyncMock()
        client.get.return_value = _response([])

        asyncio.run(_adapter(client).fetch_income(limit=10))

        url = client.get.await_args.args[0]
        assert "startTime" not in url

    def test_explicit_start_and_end_are_sent(self):
        client = AsyncMock()
        client.get.return_value = _response([])

        asyncio.run(_adapter(client).fetch_income(start_ms=111, end_ms=222, limit=10))

        url = client.get.await_args.args[0]
        assert "startTime=111" in url
        assert "endTime=222" in url

    def test_income_type_filter_is_sent(self):
        client = AsyncMock()
        client.get.return_value = _response([])

        asyncio.run(_adapter(client).fetch_income(income_type="FUNDING_FEE"))

        assert "incomeType=FUNDING_FEE" in client.get.await_args.args[0]

    def test_symbol_is_normalized_to_native(self):
        """`BTC/USDT` → `BTCUSDT`：传斜杠口径会被交易所 -1121 拒掉。"""
        client = AsyncMock()
        client.get.return_value = _response([])

        asyncio.run(_adapter(client).fetch_income(symbol="BTC/USDT"))

        assert "symbol=BTCUSDT" in client.get.await_args.args[0]

    def test_full_page_advances_cursor_to_last_time_plus_one(self):
        """满页 → 用「最后一笔时间 +1ms」作下一轮 `startTime`（这个口没有 fromId）。"""
        page1 = [
            {"incomeType": "FUNDING_FEE", "income": "-1", "time": 1000, "tranId": "a"},
            {"incomeType": "FUNDING_FEE", "income": "-2", "time": 2000, "tranId": "b"},
        ]
        page2 = [
            {"incomeType": "FUNDING_FEE", "income": "-3", "time": 3000, "tranId": "c"},
        ]
        client = AsyncMock()
        client.get.side_effect = [_response(page1), _response(page2)]

        got = asyncio.run(_adapter(client).fetch_income(start_ms=500, limit=2))

        assert len(got) == 3
        second_url = client.get.await_args_list[1].args[0]
        assert "startTime=2001" in second_url, (
            "第二页必须从上一页最后一笔的时间 +1ms 开始"
        )

    def test_identical_row_replayed_across_pages_is_counted_once(self):
        """整行完全相同的重放要去掉——否则一笔资金费被算两遍。"""
        page1 = [
            {"incomeType": "FUNDING_FEE", "income": "-1", "time": 1000, "tranId": "a"},
            {"incomeType": "FUNDING_FEE", "income": "-2", "time": 1000, "tranId": "b"},
            {"incomeType": "FUNDING_FEE", "income": "-3", "time": 1000, "tranId": "c"},
        ]
        page2 = [
            # 重放上一页的最后一笔（整行相同）+ 一笔新的（短页 → 循环在这页结束）
            {"incomeType": "FUNDING_FEE", "income": "-3", "time": 1000, "tranId": "c"},
            {"incomeType": "FUNDING_FEE", "income": "-4", "time": 2000, "tranId": "d"},
        ]
        client = AsyncMock()
        client.get.side_effect = [_response(page1), _response(page2)]

        got = asyncio.run(_adapter(client).fetch_income(start_ms=500, limit=3))

        tran_ids = [r["tranId"] for r in got]
        assert tran_ids == ["a", "b", "c", "d"], (
            f"整行重放必须只算一次，实得 {tran_ids}"
        )
        assert client.get.await_count == 2

    def test_same_tran_id_with_different_income_type_is_kept(self):
        """**回归用例（2026-11-02 生产数据实测）**：``tranId`` 在币安**不唯一**。

        同一笔成交会在 ``REALIZED_PNL`` 与 ``COMMISSION`` 下各占一行而**共用同一个
        ``tranId``**。按 ``tranId`` 单键去重会把手续费整行当重复丢掉，于是：

        * 手续费合计少算一半（实测 -26.90 → -13.43）；
        * 「流水合计 − 当前余额」不再为 0，对账表出现查不出原因的黑洞（实测 13.46）。

        而这个 bug **看起来完全正常**：条数、类型、字段都对，只是少了一半。所以去重键
        必须是整行签名，同 ``tranId`` 的不同 ``incomeType`` 一律留下。
        """
        same_tran = "90650143980871"
        rows = [
            {
                "symbol": "DOGEUSDT",
                "incomeType": "REALIZED_PNL",
                "income": "31.14751000",
                "time": 1788869176000,
                "tranId": same_tran,
            },
            {
                "symbol": "DOGEUSDT",
                "incomeType": "COMMISSION",
                "income": "-0.19491584",
                "time": 1788869176000,
                "tranId": same_tran,
            },
        ]
        client = AsyncMock()
        client.get.return_value = _response(rows)

        got = asyncio.run(_adapter(client).fetch_income())

        assert len(got) == 2, (
            "同 tranId 不同 incomeType 是两笔独立的钱，绝不能去重掉一笔"
        )
        kinds = {r["incomeType"] for r in got}
        assert kinds == {"REALIZED_PNL", "COMMISSION"}

    def test_non_advancing_cursor_terminates(self):
        """游标推不动就停：否则死循环，而外表看起来只是「卡住了」。"""
        stuck = [
            {"incomeType": "FUNDING_FEE", "income": "-1", "time": 1000, "tranId": "a"},
            {"incomeType": "FUNDING_FEE", "income": "-1", "time": 1000, "tranId": "a"},
        ]
        client = AsyncMock()
        client.get.return_value = _response(stuck)

        got = asyncio.run(_adapter(client).fetch_income(start_ms=1000, limit=2))

        assert len(got) == 1
        assert client.get.await_count <= 2, "不许无限拉同一个游标"

    def test_page_without_time_terminates(self):
        """满页但没有时间字段 → 无法推进游标，停。不猜、不死循环。"""
        client = AsyncMock()
        client.get.return_value = _response(
            [
                {"incomeType": "FUNDING_FEE", "income": "-1", "tranId": "a"},
                {"incomeType": "FUNDING_FEE", "income": "-2", "tranId": "b"},
                {"incomeType": "FUNDING_FEE", "income": "-3", "tranId": "c"},
            ]
        )

        got = asyncio.run(_adapter(client).fetch_income(limit=3))

        assert len(got) == 3, "这一页本身要全部收下"
        assert client.get.await_count == 1, "没有时间字段就不许再拉第二页"

    def test_non_list_payload_raises(self):
        """交易所回了别的形状 → 抛，不许当成「没有流水」。"""
        client = AsyncMock()
        client.get.return_value = _response({"code": -1, "msg": "err"})

        with pytest.raises(RuntimeError, match="非列表"):
            asyncio.run(_adapter(client).fetch_income())

    def test_transport_error_propagates(self):
        """**空 ≠ 失败**：网络挂了必须抛，调用方才能把它和「确实没有」分开。"""
        client = AsyncMock()
        client.get.side_effect = RuntimeError("network down")

        with pytest.raises(RuntimeError, match="network down"):
            asyncio.run(_adapter(client).fetch_income())

    def test_limit_is_capped_at_exchange_maximum(self):
        client = AsyncMock()
        client.get.return_value = _response([])

        asyncio.run(_adapter(client).fetch_income(limit=99999))

        assert "limit=1000" in client.get.await_args.args[0]


class TestGetBalanceDetail:
    def test_returns_raw_fields_per_asset(self):
        client = AsyncMock()
        client.get.return_value = _response(
            [
                {
                    "asset": "USDT",
                    "balance": "1000.5",
                    "crossWalletBalance": "1010",
                    "availableBalance": "800",
                    "crossUnPnl": "-10",
                    "updateTime": 1700000000000,
                }
            ]
        )

        got = asyncio.run(_adapter(client).get_balance_detail())

        row = got["USDT"]
        assert row["crossWalletBalance"] == Decimal("1010")
        assert row["availableBalance"] == Decimal("800")
        assert row["crossUnPnl"] == Decimal("-10")
        assert "updateTime" in row  # 数字型时间戳，能解析就保留

    def test_non_numeric_fields_are_skipped_not_zeroed(self):
        """`updateTime` 是毫秒时间戳，但万一某字段不是数字，不许落 0 冒充金额。"""
        client = AsyncMock()
        client.get.return_value = _response(
            [{"asset": "USDT", "balance": "10", "someText": "abc"}]
        )

        row = asyncio.run(_adapter(client).get_balance_detail())["USDT"]

        assert "someText" not in row
        assert row["balance"] == Decimal("10")

    def test_object_shape_is_supported(self):
        client = AsyncMock()
        client.get.return_value = _response(
            {"balance": {"walletBalance": "50", "crossUnPnl": "1"}}
        )

        got = asyncio.run(_adapter(client).get_balance_detail())

        assert got["USDT"]["walletBalance"] == Decimal("50")

    def test_empty_account_yields_empty_dict(self):
        """没有资产就没有这一格——调用方据此留空，不是 0。"""
        client = AsyncMock()
        client.get.return_value = _response([])

        assert asyncio.run(_adapter(client).get_balance_detail()) == {}


class TestBaseAdapterDefaults:
    def test_get_balance_detail_defaults_to_empty(self):
        """默认 `{}` = 「不声明细分口径」，调用方退回单值口径（同 min_notional 的纪律）。"""
        from apps.trading.adapters.base import BaseExchangeAdapter

        class _Minimal(BaseExchangeAdapter):
            async def connect(self):
                pass

            async def disconnect(self):
                pass

            async def place_order(self, request):
                raise NotImplementedError

            async def cancel_order(self, exchange_order_id, symbol):
                return False

            async def fetch_open_orders(self, symbol):
                return []

            async def fetch_order(self, exchange_order_id, symbol):
                raise NotImplementedError

            async def get_positions(self):
                return []

            async def get_balance(self):
                return {}

            async def fetch_mid_price(self, symbol):
                return None

        minimal = _Minimal("k", "s")
        assert asyncio.run(minimal.get_balance_detail()) == {}

    def test_fetch_income_default_raises(self):
        """**空 ≠ 失败**：不支持的适配器必须抛，不许静默返回空列表。"""
        from apps.trading.adapters.base import BaseExchangeAdapter

        class _Minimal(BaseExchangeAdapter):
            async def connect(self):
                pass

            async def disconnect(self):
                pass

            async def place_order(self, request):
                raise NotImplementedError

            async def cancel_order(self, exchange_order_id, symbol):
                return False

            async def fetch_open_orders(self, symbol):
                return []

            async def fetch_order(self, exchange_order_id, symbol):
                raise NotImplementedError

            async def get_positions(self):
                return []

            async def get_balance(self):
                return {}

            async def fetch_mid_price(self, symbol):
                return None

        with pytest.raises(NotImplementedError, match="income"):
            asyncio.run(_Minimal("k", "s").fetch_income())
