"""L1 起跑**经过 `advance()`** 之后仍然落得下结果（2026-10-26，#19 漏斗的生产缺陷回归）。

## 这条用例补的是哪个缺口

`test_effects.py::TestStartInSampleIntegration` 已经用真实引擎跑过
`effects.start_in_sample`，**但它绕过了 `advance()`**——而隔离事务住在 `advance()` 里。
于是真实接线里那个致命组合从来没有被端到端跑过（txy 生产上第一条候选
`0f5fb2f7` 每 5 分钟重跑一次 7014 根 K 线、永远推不动的就是它）：

* 占位行在 `advance()` 的事务里 INSERT，**未提交** → 引擎在另一个线程/连接上读不到它
  （`BacktestResult matching query does not exist`）→ `result not persisted`；
* 引擎的 `close_old_connections()` 与调用方**同线程** → 把调用方那个开着的事务连接关掉
  （`closed_in_transaction=True`）→ 此后该事务内任何查询都
  `InterfaceError: connection already closed`。

## 为什么这两个用例必须是 `transaction=True`

默认的 `django_db` 会给用例**包一层事务**——那样「事务外」这个前提在用例里根本不存在，
测出来的形状与生产不同形（上面那个缺口正是这么躲过整套测试的）。所以两个用例都显式
`pytest.mark.django_db(transaction=True)`，让 `advance()` 自己的事务是**真的**事务。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from apps.backtest.models import BacktestResult
from apps.datasource.models import KlineRecord
from apps.strategy_engine.base import BaseStrategy
from apps.strategy_engine.registry import StrategyRegistry
from apps.strategy_factory import effects
from apps.strategy_factory.evaluate import ReasonCode
from apps.strategy_factory.models import StrategyCandidate
from apps.strategy_factory.orchestrator import RunContext, advance
from apps.strategy_factory.status import (
    StrategyCandidateOrigin,
    StrategyCandidateStatus,
)
from apps.trading.models import Strategy

SYMBOL = "BTC/USDT:USDT"
TF = "1h"
START = date(2026, 1, 1)
END = date(2026, 4, 11)
PROBE_NAME = "advance_probe"
PARAMS = {"exit_at_mid": False, "position_pct": 0.25}


class _ProbeStrategy(BaseStrategy):
    """最小探针策略：每 4 根 bar 交替买卖，产出真实的权益曲线与成交数。"""

    name = PROBE_NAME
    description = (
        "策略类型：趋势跟踪\n"
        "核心指标：无\n"
        "适用场景：advance 端到端接线测试探针\n"
        "入场逻辑：每 4 根 bar 交替买卖"
    )

    def on_bar(self, kline, history):
        if len(history) % 4 == 0:
            return self.ctx.buy(Decimal("1"))
        if len(history) % 4 == 2 and self.ctx.position > 0:
            return self.ctx.sell(Decimal("1"))
        return None


def _register_probe() -> None:
    if PROBE_NAME not in StrategyRegistry.list_registered():
        StrategyRegistry.register(_ProbeStrategy, name=PROBE_NAME)


def _ctx() -> RunContext:
    return RunContext(
        symbol=SYMBOL,
        timeframe=TF,
        evaluation_start=START,
        evaluation_end=END,
        initial_capital=Decimal("10000"),
        parameters=dict(PARAMS),
    )


def _candidate(status: StrategyCandidateStatus) -> StrategyCandidate:
    """候选的跑批判据走 `source_ref["evaluation"]`（`resolve_run_context` 的两条路之一）。"""
    strategy = Strategy.objects.create(
        name=PROBE_NAME, code_path=f"strategies/{PROBE_NAME}.py"
    )
    return StrategyCandidate.objects.create(
        name=PROBE_NAME,
        hypothesis="每 4 根 bar 交替买卖",
        code_path=f"~/.tradelogx/strategies/{PROBE_NAME}.py",
        origin=StrategyCandidateOrigin.AGENT.value,
        strategy=strategy,
        status=status.value,
        source_ref={"evaluation": _ctx().as_payload()},
    )


def _seed_days(start: date, days: int) -> None:
    """铺满 `[start, start+days)` 的 1h K 线（真实取数路径消费，零交易所调用）。"""
    base = datetime(
        start.year, start.month, start.day, tzinfo=__import__("datetime").timezone.utc
    )
    rows = []
    for day in range(days):
        for hour in range(24):
            ts = base + timedelta(days=day, hours=hour)
            close = 100.0 + (day * 24 + hour) * 0.05
            rows.append(
                KlineRecord(
                    exchange="binance",
                    symbol=SYMBOL,
                    timeframe=TF,
                    timestamp=ts,
                    open=close,
                    high=close + 1.0,
                    low=close - 1.0,
                    close=close,
                    volume=1.0,
                )
            )
    KlineRecord.objects.bulk_create(rows)


class TestEngineRunsOutsideAnyTransaction:
    """修法本身：起跑那一刻**不能**有任何事务开着（否则两条实证都会命中）。"""

    pytestmark = pytest.mark.django_db(transaction=True)

    def test_start_in_sample_is_called_with_no_transaction_open(self):
        _register_probe()
        candidate = _candidate(StrategyCandidateStatus.IN_SAMPLE_RUNNING)
        seen: dict[str, object] = {}

        def fake_start_in_sample(candidate, ctx, plan):
            from django.db import connection

            seen["in_atomic_block"] = connection.in_atomic_block
            return {"result_id": None}

        deps = replace(effects.default_deps(), start_in_sample=fake_start_in_sample)
        advance(candidate, deps=deps)

        assert seen["in_atomic_block"] is False, (
            "起跑时仍有事务开着：引擎的另一个连接读不到刚 INSERT 的占位行，"
            "它自己的 close_old_connections() 又会把这次推进的事务连接关掉"
        )


class TestOosRunAlsoHappensOutsideTheTransaction:
    """L2 是同一根因的第二种表现，必须一起搬出去。

    `walk_forward_runner.py` 里有五处 `close_old_connections()`，全都在调用方事务里时
    必然毒化连接（`InterfaceError`），而分折的层记录一个字都落不下来——症状与 L1 逐字
    相同。今天它不可达（候选卡在 L1），所以这是**潜伏**缺陷；L1 修好它就立刻可达。
    """

    pytestmark = pytest.mark.django_db(transaction=True)

    def test_start_oos_is_called_with_no_transaction_open(self):
        candidate = _candidate(StrategyCandidateStatus.OOS_RUNNING)
        # `read_oos` 需要一条主行 id（这里给一个不存在的：判定会落 oos_failed，本用例不看它）。
        candidate.evidence = {"layers": {"in_sample": {"result_id": str(uuid4())}}}
        candidate.save(update_fields=["evidence"])
        seen: dict[str, object] = {}

        def fake_start_oos(candidate, ctx, plan, in_sample_result_id):
            from django.db import connection

            seen["in_atomic_block"] = connection.in_atomic_block
            return {}

        deps = replace(effects.default_deps(), start_oos=fake_start_oos)
        advance(candidate, deps=deps)

        assert seen["in_atomic_block"] is False, (
            "分折时仍有事务开着：walk_forward_runner 的 close_old_connections() "
            "会把这次推进的事务连接关掉"
        )


class TestAdvanceThroughRealEngine:
    """真实取数 + 真实引擎，但**经过 `advance()`**：占位行先提交，引擎才落得下结果。"""

    pytestmark = pytest.mark.django_db(transaction=True)

    def test_real_engine_output_is_persisted_and_drives_the_verdict(self):
        _register_probe()
        _seed_days(START, 100)
        candidate = _candidate(StrategyCandidateStatus.IN_SAMPLE_RUNNING)

        outcome = advance(candidate)

        assert outcome.started_run is True
        rows = list(BacktestResult.objects.filter(candidate_id=candidate.pk))
        assert len(rows) == 1, "起跑必须留下正好一条挂在本候选上的回测行"
        row = rows[0]
        assert row.equity_curve, (
            "行里没有引擎产物（result not persisted）——占位行对引擎的连接不可见时就是这一条"
        )
        assert row.total_trades > 0, "真实引擎跑出来的成交数不该是 0"

        # 判定必须读到**上面那一行**、并给出一个基于真数字的结论。修好之前这里会是
        # `in_sample_backtest_failed`（error=「没有产出结果行」）——那才是最像 bug 的假失败。
        assert outcome.reason_code is not ReasonCode.IN_SAMPLE_BACKTEST_FAILED, (
            f"判定没读到刚跑出来的那一行：{outcome.reason_code.value}"
        )
        assert outcome.reason_code in {
            ReasonCode.IN_SAMPLE_SHARPE_BELOW_MIN,
            ReasonCode.INSUFFICIENT_EVIDENCE,
            ReasonCode.IN_SAMPLE_PASSED,
        }
        fresh = StrategyCandidate.objects.get(pk=candidate.pk)
        assert fresh.status in {
            StrategyCandidateStatus.OOS_RUNNING.value,
            StrategyCandidateStatus.IN_SAMPLE_FAILED.value,
        }, (
            "判定必须落到「进 L2」或「L1 未过」，不能停在 in_sample_running 等一个不会来的结果"
        )

    def test_in_sample_passed_keeps_the_record_and_approves_the_row(self):
        """L1 通过那一步：层记录留着（D18 幂等键）+ 主行按 D12 从 pending 推成 approved。

        起跑用假实现（它只负责「造一条有引擎产物的主行」，形状照真实引擎的落库结果），
        因为真实探针跑不出 ≥1.5 的夏普——但那正是本用例要覆盖的**通过分支**。
        """
        _register_probe()
        candidate = _candidate(StrategyCandidateStatus.IN_SAMPLE_RUNNING)

        def fake_start_in_sample(candidate, ctx, plan):
            row = BacktestResult.objects.create(
                strategy=candidate.strategy,
                symbol=SYMBOL,
                timeframe=TF,
                start_date=START,
                end_date=date(2026, 3, 22),
                initial_capital=Decimal("10000"),
                final_capital=Decimal("12000"),
                total_return_pct=20.0,
                sharpe_ratio=2.5,
                total_trades=120,
                equity_curve=[{"timestamp": "2026-01-01T00:00:00Z", "equity": "10000"}],
                parameters=dict(PARAMS),
            )
            return {"result_id": str(row.id)}

        deps = replace(effects.default_deps(), start_in_sample=fake_start_in_sample)
        outcome = advance(candidate, deps=deps)

        assert outcome.reason_code is ReasonCode.IN_SAMPLE_PASSED
        fresh = StrategyCandidate.objects.get(pk=candidate.pk)
        assert fresh.status == StrategyCandidateStatus.OOS_RUNNING.value
        record = ((fresh.evidence or {}).get("layers") or {}).get("in_sample")
        assert record and record.get("result_id"), (
            "通过时层记录必须留着——它就是「这一层不再重跑」的幂等键"
        )
        row = BacktestResult.objects.get(id=record["result_id"])
        assert row.review_status == "approved", "漏斗通过后由判定侧写审核字段（D12）"
        assert row.reviewed_at is not None
