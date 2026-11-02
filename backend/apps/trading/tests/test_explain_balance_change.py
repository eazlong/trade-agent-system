"""`explain_balance_change` 命令与 `fetch_income` / `get_balance_detail` 的测试面。

这个命令回答的是「余额变了，但没有订单」。它最大的风险**不是算错，而是把「不知道」
说成「没有」**——一次网络失败打出一句「该区间内没有流水」，用户就会认定账是对的、
缺口不存在。所以这里钉的三件事按重要性排序：

1. **取不到 ≠ 没有**：适配器抛错时，命令必须明说「这次没读到」，且**不能**继续往下
   输出「本地都有对应订单」那种肯定结论。
2. **哪些流水本来就不该有订单**：资金费 / 划转没有 ``orderId`` 是正常的，把它们判成
   「账本缺记录」会造出一堆假告警，而假告警的代价是真告警也被忽略（CONTEXT.md 里
   那条「撤单快照」的决定就是这么写的）。真正要点名的是「带了 ``orderId``、本地却
   没有这一行」。
3. **不编数**：钱包 / 可用 / 已用保证金取不到时留空，不许用 0 或总额顶替。

纯函数用 ``SimpleTestCase``；命令端到端用 ``pytest.mark.django_db(transaction=True)``
（``db_async`` 走的是另一个线程，要它看得见 fixture 的行）。
"""

from __future__ import annotations

from decimal import Decimal
from io import StringIO
from unittest.mock import AsyncMock, patch

import pytest
from django.core.management import call_command
from django.test import SimpleTestCase

from apps.exchange.models import ExchangeAccount
from apps.trading.management.commands.explain_balance_change import (
    _group_income_by_type,
    _income_without_local_order,
    _sum_by_type,
    _ts,
)
from apps.trading.models import Order

_SYMBOL = "BTC/USDT"
_RAW = "BTCUSDT"


def _income(
    *,
    kind="FUNDING_FEE",
    income="-0.5",
    oid="",
    ts=1_700_000_000_000,
    symbol=_RAW,
):
    return {
        "symbol": symbol,
        "incomeType": kind,
        "income": income,
        "asset": "USDT",
        "time": ts,
        "tranId": f"t{ts}{kind}",
        "info": "",
        "orderId": oid,
    }


# --------------------------------------------------------------------------- #
# 纯函数
# --------------------------------------------------------------------------- #


class TestTimestamp(SimpleTestCase):
    def test_epoch_is_rendered_in_utc(self):
        """不带 `tz=` 会拿本机时区，于是表里每一行都偏移几小时（CLAUDE.md 的坑）。"""
        # 1700000000 = 2023-11-14 22:13:20 UTC
        assert _ts(1_700_000_000_000) == "2023-11-14 22:13:20"

    def test_missing_time_is_a_dash_not_epoch(self):
        """`None` 不许被读成 1970——那是「有时间戳」的一句假话。"""
        assert _ts(None) == "-"
        assert _ts("not-a-number") == "-"


class TestGrouping(SimpleTestCase):
    def test_groups_by_income_type(self):
        rows = [
            _income(kind="FUNDING_FEE", income="-0.5"),
            _income(kind="FUNDING_FEE", income="-0.25"),
            _income(kind="TRANSFER", income="100"),
        ]
        grouped = _group_income_by_type(rows)
        assert len(grouped["FUNDING_FEE"]) == 2
        assert len(grouped["TRANSFER"]) == 1

    def test_unknown_type_falls_back_not_dropped(self):
        """没见过的 `incomeType` 也必须出现——丢掉它就等于把一笔钱从账上抹掉。"""
        grouped = _group_income_by_type([_income(kind="SOME_NEW_TYPE", income="1")])
        assert "SOME_NEW_TYPE" in grouped


class TestSumByType(SimpleTestCase):
    def test_sums_per_type(self):
        rows = [
            _income(kind="FUNDING_FEE", income="-0.5"),
            _income(kind="FUNDING_FEE", income="-0.25"),
            _income(kind="TRANSFER", income="100"),
        ]
        totals = _sum_by_type(rows)
        assert totals["FUNDING_FEE"] == Decimal("-0.75")
        assert totals["TRANSFER"] == Decimal("100")

    def test_unparsable_amount_is_counted_not_silently_dropped(self):
        """解析不出来就计数，不落 0：落 0 会让合计看起来「刚好平了」。"""
        totals = _sum_by_type([_income(income="not-a-number")])
        assert totals["_unparsed"] == Decimal("1")
        assert "FUNDING_FEE" not in totals


class TestIncomeWithoutLocalOrder(SimpleTestCase):
    def test_funding_fee_has_no_order_and_is_reported(self):
        """资金费没有 orderId —— 它必然出现在「本地没有」里，但那是**正常**的。"""
        rows = [_income(kind="FUNDING_FEE")]
        assert len(_income_without_local_order(rows, set())) == 1

    def test_matched_order_id_is_excluded(self):
        rows = [_income(kind="REALIZED_PNL", income="9.9", oid="12345")]
        assert _income_without_local_order(rows, {"12345"}) == []

    def test_unmatched_order_id_is_kept(self):
        """交易所说有这张单、本地没有 → 这就是账本缺口，必须露出来。"""
        rows = [_income(kind="REALIZED_PNL", income="9.9", oid="99999")]
        missing = _income_without_local_order(rows, {"12345"})
        assert len(missing) == 1
        assert missing[0]["orderId"] == "99999"


# --------------------------------------------------------------------------- #
# 命令端到端
# --------------------------------------------------------------------------- #


class _FakeAdapter:
    """一个能回答三个口的假适配器。"""

    def __init__(self, *, income=None, income_error=None, detail=None):
        self._income = income or []
        self._income_error = income_error
        self._detail = detail
        self.disconnected = False

    async def get_balance(self):
        return {"USDT": Decimal("1000")}

    async def get_balance_detail(self):
        if self._detail is None:
            return {}
        return self._detail

    async def fetch_income(self, **kwargs):
        if self._income_error is not None:
            raise self._income_error
        return list(self._income)

    async def disconnect(self):
        self.disconnected = True


def _make_account(*, label="实盘账户", testnet=False):
    return ExchangeAccount.objects.create(
        exchange="binance",
        label=label,
        testnet=testnet,
        is_active=True,
        api_key_enc=b"k",
        api_secret_enc=b"s",
    )


def _run_command(*args):
    out = StringIO()
    call_command("explain_balance_change", *args, stdout=out)
    return out.getvalue()


@pytest.mark.django_db(transaction=True)
class TestCommandReportsHonestly:
    def _patch(self, adapter):
        return patch(
            "apps.trading.pending_reconcile.build_account_adapter",
            AsyncMock(return_value=adapter),
        )

    def test_fetch_failure_is_not_reported_as_no_income(self):
        """**核心用例**：拉取失败时必须说「这次没读到」，不许说「没有流水」。"""
        account = _make_account()
        adapter = _FakeAdapter(income_error=RuntimeError("network down"))

        with self._patch(adapter):
            text = _run_command("--account", str(account.id))

        assert "资金流水拉取失败" in text
        assert "这不是「没有流水」" in text
        # 不能出现肯定性的「本地都有对应订单」结论——那次对照压根没做成。
        assert "本地都有对应的 Order 行" not in text
        assert adapter.disconnected, "适配器必须在 finally 里断开"

    def test_empty_income_is_reported_as_empty_not_as_failure(self):
        """空与失败要长得不一样：这次请求成功了，所以「空」是可信的。"""
        account = _make_account()
        with self._patch(_FakeAdapter(income=[])):
            text = _run_command("--account", str(account.id))
        assert "没有返回任何流水" in text
        assert "拉取失败" not in text

    def test_funding_fee_is_explained_as_no_order_by_design(self):
        """资金费改余额、按设计不产生 Order 行——这句话必须出现在输出里。"""
        account = _make_account()
        adapter = _FakeAdapter(income=[_income(kind="FUNDING_FEE", income="-23.5")])
        with self._patch(adapter):
            text = _run_command("--account", str(account.id))

        assert "FUNDING_FEE" in text
        assert "资金费" in text
        assert "按设计不产生 Order 行" in text
        # 资金费不是账本缺口，不许被点名成「账本缺记录」
        assert "账本缺记录" not in text

    def test_missing_local_order_is_flagged(self):
        """带了 orderId 但本地没有那一行 → 这才是真缺口。"""
        account = _make_account()
        adapter = _FakeAdapter(
            income=[_income(kind="REALIZED_PNL", income="9.9", oid="99999")]
        )
        with self._patch(adapter):
            text = _run_command("--account", str(account.id))

        assert "账本缺记录" in text
        assert "99999" in text
        assert "reconcile_realized_pnl_from_exchange" in text

    def test_matched_local_order_is_confirmed(self):
        account = _make_account()
        Order.objects.create(
            exchange_account_id=account.id,
            symbol=_SYMBOL,
            side="sell",
            order_type="market",
            quantity=Decimal("1"),
            status="filled",
            exchange_order_id="12345",
            filled_quantity=Decimal("1"),
            avg_fill_price=Decimal("100"),
        )
        adapter = _FakeAdapter(
            income=[_income(kind="REALIZED_PNL", income="9.9", oid="12345")]
        )
        with self._patch(adapter):
            text = _run_command("--account", str(account.id))

        assert "本地都有对应的 Order 行" in text
        assert "账本缺记录" not in text

    def test_balance_detail_is_printed(self):
        account = _make_account()
        adapter = _FakeAdapter(
            detail={
                "USDT": {
                    "balance": Decimal("1000"),
                    "crossWalletBalance": Decimal("1010"),
                    "availableBalance": Decimal("800"),
                    "crossUnPnl": Decimal("-10"),
                }
            }
        )
        with self._patch(adapter):
            text = _run_command("--account", str(account.id))

        # 钱包口径取 crossWalletBalance（明确不含浮盈），不是 balance
        assert "1010.00000000" in text
        assert "800.00000000" in text
        assert "-10.00000000" in text

    def test_days_must_be_positive(self):
        from django.core.management.base import CommandError

        with pytest.raises(CommandError):
            _run_command("--days", "0")

    def test_command_writes_nothing(self):
        """只读命令：跑完之后 orders 表一行都不许多。"""
        account = _make_account()
        adapter = _FakeAdapter(
            income=[
                _income(kind="FUNDING_FEE", income="-1"),
                _income(oid="777", kind="REALIZED_PNL"),
            ]
        )
        before = Order.objects.count()
        with self._patch(adapter):
            _run_command("--account", str(account.id))
        assert Order.objects.count() == before
