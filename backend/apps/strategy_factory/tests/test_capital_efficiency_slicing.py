"""资金效率**回测侧适配层**的用例（spec #30 D7 / D8；子票 #35）。

`test_capital_efficiency.py` 钉的是**纯模块**的口径（时间加权、三道判不了的门、样本标注）；
这个文件钉的是把那个纯函数接进回测保存路径的那一层。每个用例回答一个「写错了照样跑」
的问题：

1. **适配层只认 `close` 行**——`open` / `add` 行被计数挡在外面；`close` 行却缺
   `exit_time` / `pnl` 是**数据异常**（不是「未平仓」），单独计数并告警。混成一个数会
   让「有一笔没平完」看起来像「有一行写坏了」。
2. **`notional` 取的是建仓成本那一侧**（`quantity × entry_price`），不是平仓名义值。
   取错那一侧会让同一笔仓位只因行情最后涨了 50% 就显得当初多占了 50% 的钱。
3. **本金必须是 `Decimal`**。传 float 会被纯模块判成 `invalid_capital`；本层不替调用方
   兜底转换——那会把一次口径错误变成一次静默的成功。
4. **窗口右端是 `end_date + 1 天`**（左闭右开）。少这一天，最后一天的仓位会被整段裁掉，
   `time_in_market_pct` 系统性偏低。
5. **写 `metrics` 只动自己那一格**——`metrics` 里已经住着 `regime_slice` 与
   `walk_forward`。读-改-写整字段是唯一安全写法（`slicing.save_slice` 的同一段代码形状）。
6. **`is_stale` 只看输入**——版本、门槛、窗口。`computed_at` 天天在变，它不是输入；拿它
   当陈旧等于每次重算都是全表重写。
7. **投递失败绝不抛**——一次成功的回测不该因为一个附加产物投不出去而被标成失败。
8. **`BacktestEngine` 一行未动**——见 `TestEngineUntouched`。

DB 用例用真事务回滚的 `TestCase`（与 `test_slicing.py` 同形），纯逻辑一律
`SimpleTestCase`，不碰库。
"""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from apps.strategy_factory import capital_efficiency_slicing as ces
from apps.strategy_factory import tasks
from apps.strategy_factory.capital_efficiency import (
    CAPITAL_EFFICIENCY_VERSION,
    CapitalEfficiencyReason,
    capital_efficiency_keys,
)
from apps.strategy_factory.config import CONFIG

#: 12 天窗口（左闭右开 → 12 个整日）。与 `test_slicing.py` 的 `WINDOW` 同形，便于对照。
DAY0 = date(2026, 1, 1)
WINDOW_END = DAY0 + timedelta(days=11)
CAP = Decimal("10000.00")

#: 缩小后的门槛。规则一模一样，只是让「样本充足」在 2 笔上就能翻过去。
#:
#: 用 `dataclasses.replace` 派生而不是造一个假 config：`compute_capital_efficiency` 收的
#: 是一个**真的** `FactoryConfig`（`config.min_trades` 与 `config.snapshot()` 都被读），
#: 一个 `SimpleNamespace` 替身只会在别处炸成 AttributeError。
SMALL = replace(CONFIG, min_trades=2)


def at(day: int, hour: int = 12) -> datetime:
    return datetime(2026, 1, 1, hour, tzinfo=timezone.utc) + timedelta(days=day)


_MISSING = object()


def row(
    *,
    trade_type: str = "close",
    entry: datetime | None = None,
    exit: datetime | None = _MISSING,
    pnl=Decimal("100.00"),
    quantity=Decimal("1.00000000"),
    entry_price=Decimal("100.00000000"),
):
    """`BacktestTrade` 行的替身：适配层只用这七个属性（见 `adapt_trades`）。

    `exit` 的缺省与**显式传 `None`** 必须能分开——「平仓行却缺 exit_time」正是要测的那
    一类数据异常，而一个 `exit if exit is not None else at(1)` 会把那次显式传参悄悄变成
    「有一个正常的 exit_time」，于是那条用例测的东西不见了。
    """
    entry = entry if entry is not None else at(1)
    if exit is _MISSING:
        exit = entry if trade_type == "close" else None
    return SimpleNamespace(
        trade_type=trade_type,
        entry_time=entry,
        exit_time=exit,
        pnl=pnl if trade_type == "close" else None,
        quantity=quantity,
        entry_price=entry_price,
        exit_price=Decimal("101.00000000"),
    )


def result_stub(
    *, start=DAY0, end=WINDOW_END, metrics=None, capital=CAP
) -> SimpleNamespace:
    """`BacktestResult` 的替身：`is_stale` 只读窗口、`metrics` 与 `initial_capital`。"""
    return SimpleNamespace(
        start_date=start, end_date=end, metrics=metrics, initial_capital=capital
    )


# --------------------------------------------------------------------------- #
# 适配层：行 → 占用区间
# --------------------------------------------------------------------------- #


class TestTradeAdaptation(SimpleTestCase):
    """只认 `close` 行；三类行分开计数。"""

    def test_a_closed_row_becomes_a_held_interval(self):
        intervals, attribution = ces.adapt_trades([row(entry=at(1), exit=at(3))])

        assert attribution == {
            "open_trades_excluded": 0,
            "incomplete_close_rows": 0,
            "closed_trades": 1,
        }
        assert len(intervals) == 1
        assert intervals[0].start == at(1)
        assert intervals[0].end == at(3)
        assert intervals[0].kind == "held"
        assert intervals[0].pnl == Decimal("100.00")

    def test_open_and_add_rows_are_excluded_and_counted(self):
        """一笔从未平仓的交易静默消失，看起来与「这个窗口没有交易」一模一样。"""
        rows = [
            row(trade_type="open"),
            row(trade_type="add"),
            row(entry=at(1), exit=at(2)),
        ]

        intervals, attribution = ces.adapt_trades(rows)

        assert len(intervals) == 1
        assert attribution["open_trades_excluded"] == 2
        assert attribution["closed_trades"] == 1

    def test_a_close_row_missing_pnl_is_excluded_and_flagged(self):
        """平仓行缺 pnl 是数据异常，与「还没平仓」不是一回事——所以单独吼一声。"""
        rows = [
            row(entry=at(1), exit=at(2)),
            row(entry=at(3), exit=at(4), pnl=None),
        ]

        with self.assertLogs(
            "apps.strategy_factory.capital_efficiency_slicing", level="WARNING"
        ) as captured:
            intervals, attribution = ces.adapt_trades(rows)

        assert len(intervals) == 1
        assert attribution["incomplete_close_rows"] == 1
        assert attribution["open_trades_excluded"] == 1
        assert "缺 exit_time 或 pnl" in "\n".join(captured.output)

    def test_a_close_row_missing_exit_time_is_also_flagged(self):
        rows = [row(entry=at(2), exit=None, pnl=Decimal("50.00"))]

        with self.assertLogs(
            "apps.strategy_factory.capital_efficiency_slicing", level="WARNING"
        ):
            intervals, attribution = ces.adapt_trades(rows)

        assert intervals == []
        assert attribution["incomplete_close_rows"] == 1

    def test_the_notional_is_quantity_times_the_entry_cost(self):
        """`notional` = 成交量 × 建仓成本，**不是** quantity × exit_price。

        取平仓名义值会让同一笔仓位只因行情最后涨了 50% 就显得当初多占了 50% 的钱——
        而「占用」是持有期间的资金压力，不该由离场那一刻的行情改写。
        """
        closed = row(
            entry=at(1),
            exit=at(3),
            quantity=Decimal("2.00000000"),
            entry_price=Decimal("100.00000000"),
        )

        intervals, _ = ces.adapt_trades([closed])

        assert intervals[0].notional == Decimal("200.00000000")
        assert intervals[0].notional != Decimal("2") * closed.exit_price

    def test_the_notional_stays_decimal(self):
        """全程 Decimal：纯模块的加权平均在 Decimal 上做，转换点只有纯模块内部那一处。"""
        intervals, _ = ces.adapt_trades([row(quantity=Decimal("1.5"))])

        assert isinstance(intervals[0].notional, Decimal)
        assert isinstance(intervals[0].pnl, Decimal)

    def test_an_unreadable_notional_is_never_silently_turned_into_zero(self):
        """占用金额推不出来时，**不许**用 0 顶替（纯模块口径 5 在适配层的落点）。

        `quantity` / `entry_price` 是 NOT NULL 字段，所以这一行理论上推不出 `None`；
        但真出现时，`row.quantity * None` 会抛 `TypeError`（整批挂在一条坏行上），而
        `or Decimal("0")` 那一类兜底会把「这一段没占钱」写成结论——加权平均被系统性
        低估，「占用更多钱 ≠ 效率更高」这件事于是反过来。两条都不对，所以这里钉的是
        **第三**条路：`notional` 原样传 `None`，由纯模块判成整段 `invalid_capital`。
        """
        broken = row(entry=at(1), exit=at(3), entry_price=None)

        intervals, _ = ces.adapt_trades([broken])

        assert intervals[0].notional is None

        payload = ces.capital_efficiency_payload(
            rows=[broken],
            window=ces.efficiency_window(DAY0, WINDOW_END),
            initial_capital=CAP,
        )
        assert payload["computed"] is False
        assert payload["reason"] == CapitalEfficiencyReason.INVALID_CAPITAL.value
        assert payload["avg_capital_used"] is None

    def test_a_naive_moment_is_read_as_utc(self):
        """naive 交给 `astimezone()` 会按进程 TZ 解释，而进程 TZ 是 Django 的副作用。"""
        naive = datetime(2026, 1, 2, 12, 0)

        intervals, _ = ces.adapt_trades([row(entry=naive, exit=naive)])

        assert intervals[0].start.tzinfo == timezone.utc
        assert intervals[0].start.hour == 12

    def test_an_aware_moment_from_another_offset_is_normalised_to_utc(self):
        beijing = timezone(timedelta(hours=8))
        moment = datetime(2026, 1, 2, 20, 0, tzinfo=beijing)

        intervals, _ = ces.adapt_trades([row(entry=moment, exit=moment)])

        assert intervals[0].start == datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)

    def test_an_empty_batch_is_not_an_error(self):
        intervals, attribution = ces.adapt_trades([])

        assert intervals == []
        assert attribution["closed_trades"] == 0


# --------------------------------------------------------------------------- #
# 窗口 / 本金
# --------------------------------------------------------------------------- #


class TestWindow(SimpleTestCase):
    """`[start_date, end_date]`（含端点）→ 左闭右开的瞬时窗口。"""

    def test_the_end_date_is_inclusive_so_the_window_reaches_the_next_midnight(self):
        """少这一天，最后一天的仓位会被整段裁掉——`time_in_market_pct` 系统性偏低。"""
        window = ces.efficiency_window(DAY0, WINDOW_END)

        assert window.start == datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert window.end == datetime(2026, 1, 13, tzinfo=timezone.utc)
        assert window.duration == 12 * 86400

    def test_a_single_day_window_is_a_full_day_not_an_empty_one(self):
        """`start_date == end_date` 是「回测了一天」，不是空窗。"""
        window = ces.efficiency_window(DAY0, DAY0)

        assert window.duration == 86400

    def test_a_whole_day_of_holding_is_measured_as_the_whole_window(self):
        """一日窗口里满仓跑满全天 = 100%：整日语义在**结果**上也成立，不只是时长上。"""
        window = ces.efficiency_window(DAY0, DAY0)

        payload = ces.capital_efficiency_payload(
            rows=[
                row(
                    entry=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    exit=datetime(2026, 1, 2, tzinfo=timezone.utc),
                    quantity=Decimal("100.00000000"),
                    entry_price=Decimal("100.00000000"),
                )
            ],
            window=window,
            initial_capital=CAP,
        )

        assert payload["capital_utilization"] == 100.0
        assert payload["time_in_market_pct"] == 100.0


class TestCapitalHandling(SimpleTestCase):
    """本金是这条链路上的钱，必须全程 `Decimal`。"""

    def _payload(self, capital):
        return ces.capital_efficiency_payload(
            rows=[row(entry=at(0), exit=at(1))],
            window=ces.efficiency_window(DAY0, WINDOW_END),
            initial_capital=capital,
        )

    def test_a_decimal_capital_is_accepted(self):
        payload = self._payload(CAP)

        assert payload["computed"] is True
        assert payload["reason"] is None
        assert payload["avg_capital_used"] is not None

    def test_a_float_capital_is_refused_rather_than_silently_converted(self):
        """**这一条是刻意留的**：收一个 float 意味着上游已经悄悄转过一次。

        本层不替调用方 `Decimal(str(...))`——那会把一次口径错误变成一次静默的成功，
        而 `BacktestResult.initial_capital` 是 `DecimalField(20,2)`，读出来就是 Decimal。
        """
        payload = self._payload(10000.0)

        assert payload["computed"] is False
        assert payload["reason"] == CapitalEfficiencyReason.INVALID_CAPITAL.value
        assert payload["capital_utilization"] is None

    def test_a_none_capital_is_a_conclusion_not_a_crash(self):
        payload = self._payload(None)

        assert payload["computed"] is False
        assert payload["reason"] == CapitalEfficiencyReason.INVALID_CAPITAL.value

    def test_a_zero_capital_is_a_conclusion_not_a_zero_utilization(self):
        """本金 0 时利用率不是 0——0 会被读成「完全没用钱、效率真高」。"""
        payload = self._payload(Decimal("0"))

        assert payload["computed"] is False
        assert payload["capital_utilization"] is None


# --------------------------------------------------------------------------- #
# 载荷信封
# --------------------------------------------------------------------------- #


class TestPayloadEnvelope(SimpleTestCase):
    """`payload_from_result` 盖上去的那几样，每一样都有人依赖。"""

    def _payload(self, *, rows=None, now=None) -> dict:
        return ces.capital_efficiency_payload(
            rows=rows if rows is not None else [row(entry=at(0), exit=at(2))],
            window=ces.efficiency_window(DAY0, WINDOW_END),
            initial_capital=CAP,
            config=SMALL,
            now=now,
        )

    def test_the_payload_carries_the_version(self):
        """版本是 `is_stale` 的第一条判据；口径变了它必须 +1，否则老载荷永远不重算。"""
        assert self._payload()["version"] == CAPITAL_EFFICIENCY_VERSION

    def test_the_payload_carries_the_threshold_snapshot(self):
        """D8 的口径快照：门槛改了，`sample_sufficient` 的读法就变了。

        少了它，半年后没人答得出「这个 sample_sufficient 是哪条门槛判的」。
        """
        payload = self._payload(
            rows=[
                row(entry=at(0), exit=at(1)),
                row(entry=at(2), exit=at(3), pnl=Decimal("-20.00")),
            ]
        )

        assert payload["sample_threshold"] == SMALL.min_trades == 2
        assert payload["sample_sufficient"] is True

    def test_a_thinner_sample_than_the_threshold_is_flagged(self):
        """门槛真的在读数：1 笔 < 2 笔 → 标注为不足（不是把数字抹成 None）。"""
        payload = self._payload(rows=[row(entry=at(0), exit=at(1))])

        assert payload["sample_sufficient"] is False
        assert payload["avg_capital_used"] is not None

    def test_the_threshold_snapshot_is_read_from_the_top_level(self):
        """`is_stale` 按**顶层**读它。它一旦被挪进子字典，陈旧判定会静默退化成「永不陈旧」。"""
        payload = self._payload()

        assert isinstance(payload["sample_threshold"], int)

    def test_computed_at_is_an_iso_instant_we_can_read_back(self):
        payload = self._payload(now=datetime(2026, 9, 22, tzinfo=timezone.utc))

        assert datetime.fromisoformat(payload["computed_at"]) == datetime(
            2026, 9, 22, tzinfo=timezone.utc
        )

    def test_the_notional_basis_is_spelled_out_in_the_payload(self):
        """口径不能只写在源码注释里：看载荷的人不会来读源码。"""
        payload = self._payload()

        assert payload["notional_basis"] == ces.NOTIONAL_BASIS
        joined = "\n".join(payload["notes"])
        assert "建仓成本" in joined
        assert "无杠杆" in joined or "现货" in joined
        assert "未平仓" in joined

    def test_the_payload_stays_key_complete_when_it_cannot_be_computed(self):
        """判不了与「算出来了」同一组键：前端不必为判不了单独写一条分支。

        但 `computed` / `reason` 必须能分开——「算过、结论是无从判断」与「从没算过」
        是两件事（后者是**键根本不存在**，见 `TestGracefulDegradation`）。
        """
        uncomputable = self._payload(rows=[row(trade_type="open")])
        computed = self._payload()

        assert set(uncomputable) == set(computed)
        assert uncomputable["computed"] is False
        assert uncomputable["reason"] == CapitalEfficiencyReason.NO_TRADES.value
        assert uncomputable["avg_capital_used"] is None

    def test_the_six_metric_keys_are_all_present(self):
        payload = self._payload()

        for key in capital_efficiency_keys():
            assert key in payload

    def test_the_attribution_counts_are_filled_in_by_the_adapter(self):
        """纯模块看不到被挡在外面的行，占位由本层补上。"""
        payload = self._payload(
            rows=[row(trade_type="open"), row(entry=at(1), exit=at(2))]
        )

        assert payload["attribution"]["open_trades_excluded"] == 1
        assert payload["attribution"]["closed_trades"] == 1

    def test_the_payload_is_json_safe(self):
        """载荷要进 `JSONField`：金额是定点字符串，没有 `Decimal` 漏出去。

        断言「能序列化」与「金额是 8 位定点串」两件事，而不是断言某个具体的数——
        具体的数归口径用例。
        """
        import json

        payload = self._payload()
        round_tripped = json.loads(json.dumps(payload))

        assert isinstance(payload["avg_capital_used"], str)
        assert round_tripped["avg_capital_used"] == payload["avg_capital_used"]
        assert Decimal(payload["avg_capital_used"]).as_tuple().exponent == -8


# --------------------------------------------------------------------------- #
# 陈旧判定
# --------------------------------------------------------------------------- #


class TestStaleness(SimpleTestCase):
    """三条输入各看一条，记录类字段一律不看。"""

    def _stored(self, **overrides) -> dict:
        payload = {
            "version": CAPITAL_EFFICIENCY_VERSION,
            "sample_threshold": CONFIG.min_trades,
            "window_seconds": ces.efficiency_window(DAY0, WINDOW_END).duration,
            "computed_at": "2020-01-01T00:00:00+00:00",
        }
        payload.update(overrides)
        return payload

    def test_no_payload_at_all_is_stale(self):
        """老回测根本没有这个键——这正是「要补算」的那一类。"""
        assert ces.is_stale(result_stub(metrics={})) is True

    def test_a_missing_metrics_field_is_not_an_error(self):
        assert ces.is_stale(result_stub(metrics=None)) is True

    def test_a_matching_payload_is_not_stale(self):
        stored = self._stored()
        result = result_stub(metrics={ces.METRICS_KEY: stored})

        assert ces.is_stale(result) is False

    def test_a_version_bump_makes_everything_stale(self):
        stored = self._stored(version=CAPITAL_EFFICIENCY_VERSION + 1)
        result = result_stub(metrics={ces.METRICS_KEY: stored})

        assert ces.is_stale(result) is True

    def test_a_threshold_change_makes_it_stale(self):
        """门槛只开关一个标注，但那个标注是载荷的一部分——载荷必须自足。"""
        stored = self._stored(sample_threshold=1)
        result = result_stub(metrics={ces.METRICS_KEY: stored})

        assert ces.is_stale(result) is True

    def test_a_missing_threshold_block_is_stale(self):
        """老载荷没有这一块：读作「不知道当初用的什么门槛」→ 重算。"""
        stored = self._stored()
        del stored["sample_threshold"]
        result = result_stub(metrics={ces.METRICS_KEY: stored})

        assert ces.is_stale(result) is True

    def test_a_window_change_makes_it_stale(self):
        """行被重建后窗口变了：同一批行会被裁到另一个区间里，结论不同。"""
        stored = self._stored()
        result = result_stub(
            end=WINDOW_END + timedelta(days=5), metrics={ces.METRICS_KEY: stored}
        )

        assert ces.is_stale(result) is True

    def test_a_missing_window_block_is_stale(self):
        stored = self._stored()
        del stored["window_seconds"]
        result = result_stub(metrics={ces.METRICS_KEY: stored})

        assert ces.is_stale(result) is True

    def test_a_non_numeric_window_is_stale_rather_than_a_crash(self):
        """一个被人手改坏的载荷不该把整条重算命令砸掉（那是 4067 行里的 1 行）。"""
        stored = self._stored(window_seconds="十二天")
        result = result_stub(metrics={ces.METRICS_KEY: stored})

        assert ces.is_stale(result) is True

    def test_a_bool_window_is_stale(self):
        """`True == 1` 会让一个误写成 `true` 的载荷看起来「窗口是 1 秒」。宁可判陈旧。"""
        stored = self._stored(window_seconds=True)
        result = result_stub(metrics={ces.METRICS_KEY: stored})

        assert ces.is_stale(result) is True

    def test_computed_at_alone_does_not_make_it_stale(self):
        """**这一条是重算入口存在的理由**：算得早不是陈旧，否则每次都是全表重写。"""
        stored = self._stored(computed_at="1999-01-01T00:00:00+00:00")
        result = result_stub(metrics={ces.METRICS_KEY: stored})

        assert ces.is_stale(result) is False

    def test_a_neighbouring_metrics_key_does_not_make_it_stale(self):
        """`metrics` 里住着别的写方（regime_slice / walk_forward），它们不是本载荷的输入。"""
        stored = self._stored()
        result = result_stub(
            metrics={ces.METRICS_KEY: stored, "regime_slice": {"version": 99}}
        )

        assert ces.is_stale(result) is False


# --------------------------------------------------------------------------- #
# 投递
# --------------------------------------------------------------------------- #


class TestDispatchNeverRaises(SimpleTestCase):
    """调用点是回测的收尾：投递失败不能把成功回测变成失败回测。"""

    def test_an_empty_batch_is_not_dispatched(self):
        with patch.object(tasks.compute_capital_efficiency_task, "delay") as delay:
            assert tasks.dispatch_capital_efficiency([]) is False
            assert tasks.dispatch_capital_efficiency(None) is False

        delay.assert_not_called()

    def test_a_batch_is_dispatched_as_one_task(self):
        with patch.object(tasks.compute_capital_efficiency_task, "delay") as delay:
            assert tasks.dispatch_capital_efficiency(["a", "b"]) is True

        delay.assert_called_once_with(["a", "b"])

    def test_a_broker_failure_is_swallowed_and_logged(self):
        with patch.object(
            tasks.compute_capital_efficiency_task,
            "delay",
            side_effect=RuntimeError("broker down"),
        ):
            with self.assertLogs(
                "apps.strategy_factory.tasks", level="ERROR"
            ) as captured:
                assert tasks.dispatch_capital_efficiency(["a"]) is False

        assert "投递失败" in "\n".join(captured.output)

    def test_a_broker_failure_does_not_raise_at_all(self):
        """**绝不抛**：调用方不该为它写 `try`——那正是「附加产物拖垮主产物」的入口。

        测的是「任何 `Exception` 子类都被吞掉」这件事本身，包括那些不像 broker 故障的
        形状（`celery_app` 里 `task.delay` 可能抛的属性错误、序列化错误）。吞掉之后返回
        `False`，调用方拿到的是一个布尔，而不是一次中断。

        **`BaseException`（`KeyboardInterrupt` / `SystemExit`）刻意仍然穿过去**：那两条
        不是「投递失败」，是「进程现在要停」。把它们也吞掉会让一次 Ctrl-C 变成一次静默
        的成功投递，而 `except Exception` 的语义恰好在这里是对的——所以这条用例只断言
        `Exception` 那一档。
        """
        for failure in (
            AttributeError("no broker configured"),
            ValueError("bad payload"),
            OSError("connection refused"),
        ):
            with patch.object(
                tasks.compute_capital_efficiency_task, "delay", side_effect=failure
            ):
                with self.assertLogs("apps.strategy_factory.tasks", level="ERROR"):
                    assert tasks.dispatch_capital_efficiency(["a"]) is False

    def test_a_keyboard_interrupt_is_not_swallowed(self):
        """「绝不抛」的边界是 `Exception`，不是 `BaseException`。

        吞掉 `KeyboardInterrupt` 会让一次 Ctrl-C 变成一次静默的成功投递——那比一次失败
        的附加产物坏得多，因为它让「我要停」这个动作消失。这一条钉的是 `except Exception`
        的**语义**（而不是 `except BaseException`），所以它必须能看见异常穿过去。
        """
        with patch.object(
            tasks.compute_capital_efficiency_task,
            "delay",
            side_effect=KeyboardInterrupt,
        ):
            with self.assertRaises(KeyboardInterrupt):
                tasks.dispatch_capital_efficiency(["a"])

    def test_a_single_id_is_normalised_into_a_list(self):
        """裸字符串是可迭代的：`list("abc")` 会变成三个不存在的 id，而投递方一切正常。"""
        with patch.object(tasks.compute_capital_efficiency_task, "delay") as delay:
            tasks.dispatch_capital_efficiency("abc")

        delay.assert_called_once_with(["abc"])

    def test_empty_ids_are_dropped_rather_than_dispatched(self):
        with patch.object(tasks.compute_capital_efficiency_task, "delay") as delay:
            assert tasks.dispatch_capital_efficiency(["a", None, "", "b"]) is True

        delay.assert_called_once_with(["a", "b"])

    def test_id_normalisation_coerces_to_str(self):
        assert tasks._as_id_list([1, "b"]) == ["1", "b"]
        assert tasks._as_id_list([]) == []


# --------------------------------------------------------------------------- #
# 落库与任务（碰 DB）
# --------------------------------------------------------------------------- #


class _StubbedConnectionReset:
    """把长循环里的 `close_old_connections()` 换成假的。

    与 `apps/regime/tests/test_slicing.py` 的同名类逐字同因：它在 `TestCase` 里**必然**
    把测试事务的连接关掉（`close_if_unusable_or_obsolete` 第一条就判 autocommit 状态），
    之后每一条 ORM 都是 `InterfaceError: connection already closed`。生产里没有外层
    事务，它是正确且必要的（CLAUDE.md 的长循环连接纪律），所以只换掉它，**另用一条
    用例断言它确实被调到了**，免得把「删掉这行」这种回归一起放行。
    """

    def setUp(self):
        super().setUp()
        patcher = patch("django.db.close_old_connections")
        patcher.start()
        self.addCleanup(patcher.stop)


class _BacktestFixture(TestCase):
    """造 `BacktestResult` / `BacktestTrade` 行的共用夹具。"""

    @classmethod
    def setUpTestData(cls):
        from apps.trading.models import Strategy

        cls.strategy = Strategy.objects.create(
            name=f"CapEff{cls.__name__}", code_path="/test", git_commit_hash="abc123"
        )

    def make_result(self, **overrides):
        from apps.backtest.models import BacktestResult

        fields = {
            "strategy": self.strategy,
            "symbol": "BTC/USDT",
            "timeframe": "1d",
            "start_date": DAY0,
            "end_date": WINDOW_END,
            "initial_capital": CAP,
            "final_capital": Decimal("10500.00"),
            "total_return_pct": 5.0,
            "metrics": {},
        }
        fields.update(overrides)
        return BacktestResult.objects.create(**fields)

    def add_trade(
        self,
        result,
        *,
        entry_day: int = 1,
        exit_day: int = 3,
        pnl=Decimal("100.00"),
        quantity=Decimal("1.00000000"),
        entry_price=Decimal("100.00000000"),
        trade_type: str = "close",
    ):
        from apps.backtest.models import BacktestTrade

        return BacktestTrade.objects.create(
            backtest=result,
            entry_time=at(entry_day),
            exit_time=at(exit_day) if trade_type == "close" else None,
            side="long",
            entry_price=entry_price,
            exit_price=Decimal("101.00000000") if trade_type == "close" else None,
            quantity=quantity,
            pnl=pnl if trade_type == "close" else None,
            trade_type=trade_type,
        )


class TestMaterialisation(_BacktestFixture):
    """写 `metrics` 只动自己那一格；取数只有一个入口。"""

    def test_save_replaces_only_its_own_key(self):
        """`metrics` 里还要住着 `regime_slice` / `walk_forward`——读-改-写不能顺手抹掉。"""
        result = self.make_result(
            metrics={"sharpe": 1.5, "regime_slice": {"version": 1}, "note": "keep me"}
        )

        ces.save_capital_efficiency(result.id, {"version": CAPITAL_EFFICIENCY_VERSION})

        result.refresh_from_db()
        assert result.metrics["sharpe"] == 1.5
        assert result.metrics["regime_slice"] == {"version": 1}
        assert result.metrics["note"] == "keep me"
        assert result.metrics[ces.METRICS_KEY] == {
            "version": CAPITAL_EFFICIENCY_VERSION
        }

    def test_compute_writes_the_payload_it_returns(self):
        result = self.make_result()
        self.add_trade(result, entry_day=1, exit_day=3)

        payload = ces.compute_capital_efficiency_for(result)

        result.refresh_from_db()
        assert ces.stored_payload(result) == payload
        assert payload["computed"] is True
        assert payload["attribution"]["closed_trades"] == 1

    def test_a_row_whose_entry_precedes_the_window_is_clipped(self):
        """窗口开始前已开仓 → 只计窗口内那一截（纯模块口径 4 在真行上的落点）。

        `at(-1)` 是 **2025-12-31 12:00**，窗口是 `2026-01-01 00:00 ~ 01-13 00:00`，
        `at(6)` 是 `01-07 12:00` —— 于是窗口内那一截恰好是 6.5 天。写死这个数是为了让
        「窗口右端是不是 `end_date + 1 天`」这件事在这条用例里也被看着：窗口若少了一天，
        占用秒数不变（区间右端仍在窗口内），但 `window_seconds` 会变，下面两行一起红。
        """
        result = self.make_result()
        self.add_trade(result, entry_day=-1, exit_day=6)

        payload = ces.compute_capital_efficiency_for(result)

        assert payload["computed"] is True
        assert payload["window_seconds"] == 12 * 86400
        assert payload["occupied_seconds"] == 6.5 * 86400

    def test_a_row_closed_after_the_window_is_clipped_at_the_window_end(self):
        """窗口结束后才平仓 → 只计窗口内那一截（与上一条对称）。

        这一条才是「右端取 `end_date + 1 天`」的直接判据：`at(0)` 是 01-01 12:00、`at(20)`
        远在窗口外，窗口内那一截从 01-01 12:00 到 01-13 00:00 = 11.5 天。窗口少算一天
        （到 01-12 00:00）这个数会变成 10.5 天，立刻红——两天的差不会被浮点噪声吃掉。
        """
        result = self.make_result()
        self.add_trade(result, entry_day=0, exit_day=20)

        payload = ces.compute_capital_efficiency_for(result)

        assert payload["window_seconds"] == 12 * 86400
        assert payload["occupied_seconds"] == 11.5 * 86400

    def test_the_final_day_of_the_backtest_is_inside_the_window(self):
        """一笔恰好落在 `end_date` 那一整天里的持仓必须算进窗口。

        这是「右端 = `end_date + 1 天`」最容易被漏掉的那一天：区间取「`end_date` 当天
        00:00 ~ 12:00」，窗口若在 `end_date` 当天 00:00 就结束，这一整笔会被 `_clip_to_window`
        整段丢掉（右开：`start == window.end` 属于下一个窗口），于是笔数变成 0。
        """
        result = self.make_result()
        self.add_trade(
            result,
            entry_day=11,
            exit_day=11,
            # 当日 00:00 开、12:00 平——整段都在 `end_date` 这一天里。
        )
        self.add_trade(result, entry_day=11, exit_day=11, pnl=Decimal("-50.00"))

        payload = ces.compute_capital_efficiency_for(result)

        assert payload["computed"] is True
        assert payload["attribution"]["closed_trades"] == 2
        assert payload["trade_count"] == 2

    def test_an_open_position_is_excluded_and_counted(self):
        result = self.make_result()
        self.add_trade(result, entry_day=1, exit_day=3)
        self.add_trade(result, entry_day=5, exit_day=6, trade_type="open")

        payload = ces.compute_capital_efficiency_for(result)

        assert payload["attribution"]["closed_trades"] == 1
        assert payload["attribution"]["open_trades_excluded"] == 1

    def test_the_stored_payload_of_an_untouched_result_is_none(self):
        """老回测：键**根本不存在**（不是 null），前端因此走降级态。"""
        result = self.make_result()

        assert ces.stored_payload(result) is None


class TestCapitalEfficiencyTask(_StubbedConnectionReset, _BacktestFixture):
    """任务以「一批 id」为形状：批就是那个计数，重投整批是安全的收敛策略。"""

    def test_an_empty_batch_is_a_clean_no_op(self):
        """收敛闸：没东西可算就干净地结束——这正是「重投一批已完成的」该有的样子。"""
        assert tasks.compute_capital_efficiency_task([]) == {
            "computed": 0,
            "skipped": 0,
            "missing": [],
            "failed": [],
        }
        assert tasks.compute_capital_efficiency_task(None)["computed"] == 0

    def test_a_fresh_result_gets_a_payload(self):
        result = self.make_result()
        self.add_trade(result)

        summary = tasks.compute_capital_efficiency_task([str(result.id)])

        assert summary["computed"] == 1
        result.refresh_from_db()
        assert ces.stored_payload(result) is not None

    def test_a_vanished_result_is_recorded_not_raised(self):
        """「投了却找不到」与「投漏了」看起来一样，所以它得进 summary。"""
        ghost = "00000000-0000-0000-0000-000000000000"

        summary = tasks.compute_capital_efficiency_task([ghost])

        assert summary["missing"] == [ghost]
        assert summary["computed"] == 0

    def test_an_up_to_date_result_is_skipped_on_a_second_pass(self):
        """幂等：同样的行 → 同样的载荷，「已经有一份不陈旧的」就跳过。"""
        result = self.make_result()
        self.add_trade(result)

        tasks.compute_capital_efficiency_task([str(result.id)])
        summary = tasks.compute_capital_efficiency_task([str(result.id)])

        assert summary == {"computed": 0, "skipped": 1, "missing": [], "failed": []}

    def test_the_rewrite_is_idempotent_payload_for_payload(self):
        """跳过之外的证明：强制重写**算出来的数**必须与上一次逐字相同。"""
        result = self.make_result()
        self.add_trade(result, entry_day=1, exit_day=3)

        first = tasks.compute_capital_efficiency_task([str(result.id)])
        assert first["computed"] == 1
        result.refresh_from_db()
        before = ces.stored_payload(result)

        tasks.compute_capital_efficiency_task([str(result.id)], only_stale=False)
        result.refresh_from_db()
        after = ces.stored_payload(result)

        # `computed_at` 是唯一允许变的那一格；其余每一个数都必须逐字相同。
        assert {k: v for k, v in before.items() if k != "computed_at"} == {
            k: v for k, v in after.items() if k != "computed_at"
        }

    def test_only_stale_off_forces_a_rewrite(self):
        result = self.make_result()
        self.add_trade(result)

        tasks.compute_capital_efficiency_task([str(result.id)])
        summary = tasks.compute_capital_efficiency_task(
            [str(result.id)], only_stale=False
        )

        assert summary["computed"] == 1

    def test_the_batch_loop_refreshes_the_connection_per_item(self):
        """长循环的连接纪律（CLAUDE.md）：一次网格搜索可能投上百条。

        这一条单独断言「确实调了」——上面所有用例都把它换成了假的，删掉那行也不会红。
        """
        first, second = self.make_result(), self.make_result()

        with patch("django.db.close_old_connections") as reset:
            tasks.compute_capital_efficiency_task([str(first.id), str(second.id)])

        assert reset.call_count == 2

    def test_a_broken_result_does_not_stop_the_rest_of_the_batch(self):
        """坏的那一条不该挡住其余几百条；批末统一抛出去交给 Celery 重投整批。"""
        good = self.make_result()
        bad = self.make_result()
        self.add_trade(good)
        self.add_trade(bad)
        calls = []

        def flaky(result, **kwargs):
            calls.append(str(result.id))
            if str(result.id) == str(bad.id):
                raise ValueError("坏数据")
            return original(result, **kwargs)

        original = ces.compute_capital_efficiency_for

        with patch(
            "apps.strategy_factory.capital_efficiency_slicing."
            "compute_capital_efficiency_for",
            side_effect=flaky,
        ):
            with self.assertLogs("apps.strategy_factory.tasks", level="ERROR"):
                # 直接调用（非 worker）时 Celery 把重试原样抛回来——这正是「交给 Celery
                # 决定重试」在测试里的样子。
                with self.assertRaises(RuntimeError):
                    tasks.compute_capital_efficiency_task([str(bad.id), str(good.id)])

        assert len(calls) == 2
        good.refresh_from_db()
        assert ces.stored_payload(good) is not None


class TestGracefulDegradation(_BacktestFixture):
    """老回测没有这个键时要能**照常读出来**——降级展示是 #36 的事，键的缺失状态是本票的。"""

    def test_an_old_result_stays_readable_without_the_key(self):
        old = self.make_result()
        self.add_trade(old)

        assert ces.METRICS_KEY not in (old.metrics or {})
        assert old.total_return_pct == 5.0

    def test_the_key_is_genuinely_absent_not_null(self):
        """「缺失」与「null」是两件事：前者是「从没算过」，后者是「算了个空」。"""
        old = self.make_result(metrics={"sharpe": 1.5})

        stored = ces.stored_payload(old)

        assert stored is None
        assert "capital_efficiency" not in old.metrics

    def test_is_stale_reads_a_missing_key_as_stale(self):
        old = self.make_result()

        assert ces.is_stale(old) is True

    def test_the_serializer_passes_the_new_key_through_unchanged(self):
        """`metrics` 是**整字典直通**（无 allowlist），所以新键到 API 不需要改序列化器。"""
        from apps.backtest.serializers import BacktestResultSerializer

        result = self.make_result()
        self.add_trade(result)
        payload = ces.compute_capital_efficiency_for(result)

        result.refresh_from_db()
        serialized = BacktestResultSerializer(result).data

        assert serialized["metrics"][ces.METRICS_KEY] == payload


class TestRecomputeCommand(_StubbedConnectionReset, _BacktestFixture):
    """`recompute_regime_slices` 里资金效率那一段：与切片串联、共用一次扫描。"""

    def run_command(self, *args):
        out = StringIO()
        call_command("recompute_regime_slices", *args, stdout=out)
        return out.getvalue()

    def test_all_computes_efficiency_for_every_result(self):
        a, b = self.make_result(), self.make_result()
        self.add_trade(a)
        self.add_trade(b)

        report = self.run_command("--all")

        assert "资金效率重算" in report
        a.refresh_from_db()
        b.refresh_from_db()
        assert ces.stored_payload(a) is not None
        assert ces.stored_payload(b) is not None

    def test_a_second_run_over_the_same_set_skips_the_efficiency_stage(self):
        """幂等：反复运行不产生副作用（与切片那一段同一条）。"""
        self.make_result()
        self.run_command("--all")

        report = self.run_command("--all")

        assert "资金效率重算：重算 0，跳过（不陈旧）1，失败 0" in report

    def test_a_stale_efficiency_payload_is_refreshed(self):
        """**验收要求的这一条**：旧版本的载荷必须被重算刷掉。

        用一个「假装是上一版口径算的」载荷（version 减一）来触发陈旧——比改门槛稳，
        因为它不依赖配置面的当期值。
        """
        result = self.make_result()
        self.add_trade(result)
        result.metrics = {
            ces.METRICS_KEY: {
                "version": CAPITAL_EFFICIENCY_VERSION - 1,
                "sample_threshold": CONFIG.min_trades,
                "window_seconds": 12 * 86400,
                "avg_capital_used": "999.00000000",
            }
        }
        result.save(update_fields=["metrics"])

        report = self.run_command("--all")

        assert "资金效率重算" in report
        result.refresh_from_db()
        stored = ces.stored_payload(result)
        assert stored["version"] == CAPITAL_EFFICIENCY_VERSION
        assert stored["avg_capital_used"] != "999.00000000"

    def test_a_threshold_change_refreshes_the_snapshot(self):
        """门槛变了 → 载荷里那个快照必须跟着变，否则「样本不足」会一直挂在旧门槛上。"""
        result = self.make_result()
        self.add_trade(result)
        result.metrics = {
            ces.METRICS_KEY: {
                "version": CAPITAL_EFFICIENCY_VERSION,
                "sample_threshold": CONFIG.min_trades + 7,
                "window_seconds": 12 * 86400,
            }
        }
        result.save(update_fields=["metrics"])

        self.run_command("--all")

        result.refresh_from_db()
        assert ces.stored_payload(result)["sample_threshold"] == CONFIG.min_trades

    def test_force_rewrites_even_the_fresh_ones(self):
        result = self.make_result()
        self.add_trade(result)
        self.run_command("--all")

        report = self.run_command("--all", "--force")

        assert "资金效率重算：重算 1" in report

    def test_dry_run_writes_nothing(self):
        """`--dry-run` 下面挂了两段重算：第二段偷偷写，是那种跑完才发现的故障。"""
        result = self.make_result()
        self.add_trade(result)

        report = self.run_command("--all", "--dry-run")

        assert "会重算资金效率" in report
        result.refresh_from_db()
        assert ces.stored_payload(result) is None

    def test_the_efficiency_stage_reuses_the_heavy_column_defer(self):
        """两段共用同一次 `.defer()` 扫描：分成两趟扫就是把这个 2.4 GB 的坑踩两遍。

        判据取「行上真的被推迟了哪些列」（`get_deferred_fields`）——与
        `test_slicing.py::test_the_scan_never_hauls_the_heavy_columns` 同一读法，只是
        这里盯着的是**资金效率那一段**拿到的那一行。
        """
        from apps.regime.management.commands.recompute_regime_slices import (
            HEAVY_COLUMNS,
        )

        self.make_result()
        seen: list[set] = []
        real_is_stale = ces.is_stale

        def spy(row, config=CONFIG):
            seen.append(row.get_deferred_fields())
            return real_is_stale(row, config)

        with patch(
            "apps.strategy_factory.capital_efficiency_slicing.is_stale", side_effect=spy
        ):
            self.run_command("--all")

        assert seen, "这次运行一行都没经过资金效率那一段，下面的断言无意义"
        missing = [sorted(set(HEAVY_COLUMNS) - deferred) for deferred in seen]
        assert [m for m in missing if m] == []

    def test_only_the_named_result_is_touched(self):
        named, other = self.make_result(), self.make_result()
        self.add_trade(named)
        self.add_trade(other)

        self.run_command("--result", str(named.id))

        named.refresh_from_db()
        other.refresh_from_db()
        assert ces.stored_payload(named) is not None
        assert ces.stored_payload(other) is None

    def test_a_broken_efficiency_stage_does_not_stop_the_slice_stage(self):
        """两条口径各自计数：一条坏了不该让另一条也停摆（它们能分别修）。"""
        result = self.make_result()
        self.add_trade(result)

        with patch(
            "apps.strategy_factory.capital_efficiency_slicing."
            "compute_capital_efficiency_for",
            side_effect=ValueError("坏数据"),
        ):
            with self.assertRaises(CommandError):
                self.run_command("--all")

        result.refresh_from_db()
        # 切片那一半照常落了载荷——坏的是效率那一段，不是整条命令。
        assert "regime_slice" in result.metrics
        assert ces.stored_payload(result) is None

    def test_a_broken_slice_stage_does_not_stop_the_efficiency_stage(self):
        """反向也成立：切片坏了，资金效率照样算（用户故事 22 的两套数字能分别修）。"""
        result = self.make_result()
        self.add_trade(result)

        with patch(
            "apps.regime.slicing.compute_slice", side_effect=ValueError("坏数据")
        ):
            with self.assertRaises(CommandError):
                self.run_command("--all")

        result.refresh_from_db()
        assert ces.stored_payload(result) is not None

    def test_both_stages_failing_reports_both_counts(self):
        self.make_result()

        with patch("apps.regime.slicing.compute_slice", side_effect=ValueError("坏")):
            with patch(
                "apps.strategy_factory.capital_efficiency_slicing."
                "compute_capital_efficiency_for",
                side_effect=ValueError("坏"),
            ):
                with self.assertRaises(CommandError) as caught:
                    self.run_command("--all")

        message = str(caught.exception)
        assert "1 条切片失败" in message
        assert "1 条资金效率失败" in message

    def test_the_slice_stage_still_reports_its_own_line(self):
        """旧的报告行不能因为多了一段而改名——它在既有的用例里被断言着。"""
        self.make_result()
        self.add_trade(self.make_result())

        report = self.run_command("--all")

        assert "切片重算：" in report
        assert "资金效率重算：" in report


# --------------------------------------------------------------------------- #
# 回测保存路径的接线
# --------------------------------------------------------------------------- #


class TestBacktestSavePathWiring(TestCase):
    """`apps/backtest/tasks.py` 三处 dispatch 的接线（spec #30 D11；#54 按函数重新校准）。

    ## 为什么不数个数

    本类原来断言「`dispatch_capital_efficiency([` 出现 2 次」与「import 出现 2 次」。
    第二种在 b088ffb 加了第 3 处（网格搜索**部分结果**收尾）之后转红；第一种则一直是
    **假绿**——它数的是带方括号的调用，而 `_finalize_partial_grid` 那一处传的是
    `result_ids` 变量（没有方括号），于是「少一处真接线」也能让计数刚好回到 2。

    实测过（干净 worktree，删掉那处 import + dispatch、保留 `try/except` 结构）：
    `TestBacktestSavePathWiring` **四条全绿**，而真实后果是「网格搜索超时/异常时的
    部分结果永远不算资金效率」。

    所以判据改成**按函数名逐个断言**：三个收尾点各一次，一个都不许少、也不许多。
    函数用 AST 定位（`ast.FunctionDef.lineno/end_lineno`），不靠字符串切分、不靠行号常量
    ——重命名会立刻转红，注释里出现同名也不会误判。
    """

    #: 三个收尾点，各自的位置不同、理由不同，所以要逐个点名：
    #:
    #: * `run_backtest_task`：单条回测跑完，投这一个结果；
    #: * `run_grid_search_task`：网格搜索**成功**收尾，循环外一次性投整批（不逐组合投：
    #:   那个循环带 7200 秒硬超时，逐组合多背 200 次 broker 往返）；
    #: * `_finalize_partial_grid`：网格搜索**超时/异常**收尾，把已经产出的那批也投出去
    #:   ——漏了它，部分结果就永远不算资金效率，而那恰恰是最需要看效率的场景。
    TAILS = ("run_backtest_task", "run_grid_search_task", "_finalize_partial_grid")

    def test_every_tail_dispatches_exactly_once(self):
        """三个收尾点**各**投一次。删掉任何一处都必须转红。"""
        source = _backtest_tasks_source()
        tree = ast.parse(source)

        missing: list[str] = []
        for name in self.TAILS:
            body = _function_source(source, tree, name)
            calls = body.count("dispatch_capital_efficiency(")
            if calls != 1:
                missing.append(f"{name}: {calls} 处调用")

        assert missing == [], "收尾点的资金效率投递不是各一次：" + "；".join(missing)

    def test_the_dispatch_import_stays_inside_the_function(self):
        """**这一条防的是一类真故障**：`celery_app.py:22` 先 import `apps.backtest.tasks`，
        而 `apps.strategy_factory.tasks` 顶部 `from celery_app import app`。

        把 `from apps.strategy_factory.tasks import ...` 挪到本模块顶部 → 循环导入
        （`ImportError: partially initialized module`），**整个 Django 起不来**。
        `dispatch_slice` 那条边能放在顶部只是导入顺序的巧合（`celery_app.py:27` 排在
        22 之后），不是一条可以照抄的许可。

        判据用**模块级 import 的模块名集合**（AST），而不是「顶部文本里有没有这句话」：
        后者会被注释、docstring、以及「在函数 A 里出现过就先命中」这三件事模糊掉。
        """
        source = _backtest_tasks_source()
        tree = ast.parse(source)

        module_level = _module_level_import_modules(tree)

        assert "apps.strategy_factory.tasks" not in module_level, (
            "`apps.strategy_factory.tasks` 不许在模块顶部 import（循环导入会让 Django 起不来）"
        )
        # 每个收尾点自己那条 import 仍然要留着（不然 `dispatch_capital_efficiency`
        # 这个名字在函数里没有来处，运行期才炸 NameError）。
        for name in self.TAILS:
            body = _function_source(source, tree, name)
            assert "from apps.strategy_factory.tasks import" in body, (
                f"{name} 里缺少 `dispatch_capital_efficiency` 的函数内 import"
            )

    def test_the_three_tails_carry_the_right_payloads(self):
        """三处投的载荷各不相同，各按自己的形状断言（合并成一个「有调用」会掩盖投错）。"""
        source = _backtest_tasks_source()
        tree = ast.parse(source)

        backtest = _function_source(source, tree, "run_backtest_task")
        assert 'dispatch_capital_efficiency([stats.get("result_id")])' in backtest

        grid_success = _function_source(source, tree, "run_grid_search_task")
        assert (
            'dispatch_capital_efficiency([r.get("result_id") for r in results])'
            in grid_success
        )

        partial = _function_source(source, tree, "_finalize_partial_grid")
        assert "dispatch_capital_efficiency(result_ids)" in partial

    def test_the_dispatches_are_next_to_each_other(self):
        """两条附加产物并排投：一个失败不影响另一个，读的人也不必去别处找。"""
        source = _backtest_tasks_source()

        assert 'dispatch_slice([stats.get("result_id")])' in source
        assert 'dispatch_capital_efficiency([stats.get("result_id")])' in source


def _backtest_tasks_source() -> str:
    from pathlib import Path

    import apps.backtest.tasks as module

    return Path(module.__file__).read_text(encoding="utf-8")


def _function_source(source: str, tree: ast.Module, name: str) -> str:
    """按 AST 取出一个模块级函数/方法的**源码片段**（含装饰器之后的 `def` 到函数结束）。

    不用 `source.split("def <name>")`：那样在「注释里提到同名函数」或「先出现的
    `def` 是另一个名字的前缀」时会切错，而切错的片段仍然能跑断言——于是守卫会在错误的
    范围上绿。AST 给的是语法结构上的那个函数，没有歧义。

    找不到就 `KeyError`（Fail Loud）：要点名的函数被改名/删掉，本来就应该让用例炸出来，
    而不是安静地断言一个空字符串。
    """
    for node in tree.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            lines = source.splitlines()
            return "\n".join(lines[node.lineno - 1 : node.end_lineno])
    raise KeyError(f"`apps/backtest/tasks.py` 里找不到模块级函数 {name}")


def _module_level_import_modules(tree: ast.Module) -> set[str]:
    """模块**顶层** import 到的模块名集合（`from a.b import c` → `a.b`，`import a.b` → `a.b`）。

    只看 `tree.body`：函数体内的 import 不进这个集合——那正是本类要区分的东西。
    """
    modules: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def _identifiers(node: ast.AST) -> set[str]:
    """一个 AST 子树里出现过的**标识符**：名字、属性名、关键字参数名、形参名、import 名。

    「标识符」而不是「源码子串」——`capital_efficiency` 出现在注释或 docstring 里不算
    「引用了它」，出现在 `x.capital_efficiency` / `from ... import capital_efficiency`
    里才算。这个区别正是 #54 那两条误报的来源。
    """
    found: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            found.add(child.id)
        elif isinstance(child, ast.Attribute):
            found.add(child.attr)
        elif isinstance(child, ast.keyword) and child.arg:
            found.add(child.arg)
        elif isinstance(child, ast.arg):
            found.add(child.arg)
        elif isinstance(child, ast.ImportFrom):
            if child.module:
                found.add(child.module)
            found.update(alias.name for alias in child.names)
        elif isinstance(child, ast.Import):
            found.update(alias.name for alias in child.names)
    return found


def _function_node(tree: ast.Module, name: str) -> ast.AST:
    """按名字取模块级函数/方法的 AST 节点（找不到就 `KeyError`，与 `_function_source` 同纪律）。"""
    for node in tree.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            return node
    raise KeyError(f"找不到模块级函数 {name}")


def _assigns_attribute(function: ast.AST, attribute: str) -> bool:
    """`function` 里有没有 `obj.<attribute> = ...` 这样的**赋值目标**。

    刻意**不**用 `"<attribute>" in <源码>`：注释、docstring、日志文案里的同一个词会让
    「删掉这行写入」这件事看不出来。本文件对这个区别做过变异实测：子串版在写入被删掉
    之后仍然绿（注释里还剩 8 个 `metrics`）。
    """
    for node in ast.walk(function):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and target.attr == attribute:
                    return True
    return False


def _passes_keyword(function: ast.AST, keyword_name: str) -> bool:
    """`function` 里有没有调用把 `keyword_name=` 作为关键字实参传出去（`create(...)` 那条路）。"""
    for node in ast.walk(function):
        if isinstance(node, ast.Call) and any(
            keyword.arg == keyword_name for keyword in node.keywords
        ):
            return True
    return False


def _class_node(tree: ast.Module, name: str) -> ast.ClassDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise KeyError(f"`apps/strategy_engine/backtest_mode.py` 里找不到类 {name}")


class TestEngineUntouched(TestCase):
    """验收条目：**`BacktestEngine` 一行未动**。

    「没动」不能靠「我记得没动」证明。这里读的是引擎源文件本身，钉住三样本票绝不能碰的
    东西：文件的（内容）哈希稳定性要靠 diff 证明，测试能证明的是**引擎不 import 本票
    的任何东西**——它一旦反过来依赖资金效率，本票就不再是「消费已落库的结果」，
    而成了「回测引擎的第二个职责」。

    ## #54：判据从「整个文件」收窄到「类体」

    原来这两条读的是 `backtest_mode.py` **全文**，而那个文件里既有被守的 `BacktestEngine`，
    也有 `trade_quality_metrics()` 与 `save_backtest_result()`——**后者就是「保存路径」，
    本来就该写 `metrics`**（ea66db6 加的，是对的）。于是「文件级 `not in`」把正当代码
    当成了违规，两条从此常年红。

    现在判据是 **`BacktestEngine` 类体里的标识符**：意图一个字没变（引擎不许反向依赖
    资金效率层、不许自己写 `metrics`），范围对准实现。往类体里加一句 `self.x = "metrics"`
    或一个 `capital_efficiency` 引用会立刻转红；写在类体**之外**保持绿——那是保存路径的
    正当位置，也正是当前误报的形态。
    """

    def _engine_source(self) -> str:
        from pathlib import Path

        from apps.strategy_engine import backtest_mode

        return Path(backtest_mode.__file__).read_text(encoding="utf-8")

    def test_the_engine_does_not_import_the_capital_efficiency_layer(self):
        """引擎不引用资金效率层，也不反向依赖整个候选漏斗。"""
        tree = ast.parse(self._engine_source())
        identifiers = _identifiers(_class_node(tree, "BacktestEngine"))

        assert not [i for i in identifiers if "capital_efficiency" in i], (
            "`BacktestEngine` 类体里出现了资金效率层的引用（它只该消费已落库的结果）"
        )
        assert not [i for i in identifiers if "strategy_factory" in i], (
            "`BacktestEngine` 类体里出现了候选漏斗的引用"
        )

    def test_the_engine_does_not_write_metrics(self):
        """引擎返回 `stats` 字典就结束；写 `metrics` 是保存路径的事（本票也只在投递侧）。"""
        tree = ast.parse(self._engine_source())
        identifiers = _identifiers(_class_node(tree, "BacktestEngine"))

        assert not [i for i in identifiers if "metrics" in i], (
            "`BacktestEngine` 类体里出现了 `metrics`（写库是 `save_backtest_result` 的事）"
        )

    def test_the_metrics_write_still_lives_in_the_save_path(self):
        """反向的一条：收窄范围不许顺手把「保存路径确实写 metrics」这件事也丢掉。

        没有这一条，「引擎不写 metrics」可以用「谁都不写 metrics」来满足——那时前端
        `metrics.profit_factor` 会静默变成空，而上面两条全绿。

        **判据必须是结构而不是子串**：本文件刚为这件事踩过一次——第一版这里写的是
        `"metrics" in save_fn`，把写入真的删掉之后它**照样绿**，因为注释里还有 8 个
        `metrics`。所以这里找的是两种**语法形态**：`X.metrics = ...` 的赋值目标，
        或调用里的 `metrics=` 关键字实参。两者都是「真的在写这一列」的形状。
        """
        tree = ast.parse(self._engine_source())
        assert "metrics" not in _identifiers(_class_node(tree, "BacktestEngine"))

        save_fn = _function_node(tree, "save_backtest_result")
        # **两条写入路径分别断言**，不是「至少有一处」。它们各管一种情形，坏一条
        # 就是一类静默故障：
        #
        #  * `obj.metrics = ...`（更新已有行）——少了它，重跑一条回测不会刷新指标；
        #  * `create(metrics=...)`（新建行）——少了它，**第一次**落库的记录就没有
        #    `metrics`，前端 `metrics.profit_factor` 一直是空。
        #
        # 实测过：只按「有任意一处」断言时，删掉新建那一路仍然全绿——那正是本类
        # 一开始要修的那种假绿。代价是将来若把两条路径合并成 `update_or_create`，
        # 这条会转红；那是**有意的**提醒（合并时得确认新路径两条情形都覆盖）。
        assert _assigns_attribute(save_fn, "metrics"), (
            "`save_backtest_result` 的**更新路径**没有写 `metrics`（`obj.metrics = ...`）"
            "——重跑回测不会刷新指标"
        )
        assert _passes_keyword(save_fn, "metrics"), (
            "`save_backtest_result` 的**新建路径**没有把 `metrics=` 交给 ORM"
            "——首次落库的记录前端 `metrics.profit_factor` 一直是空"
        )
