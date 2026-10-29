"""推进任务的用例（子票 #22；#52 补了「持有候选的失败结论」这一组）。

钉住四条：

1. **一条候选失败不拖垮整批**：失败的候选进 `failed` 列表，其余照常推进、结果可读。
2. **总开关关掉时连库都不扫**。
3. **状态集合就是「正在自动推进中」的那几个**：终态与各种等待态不在里面（它们要人来动）。
4. **停 `regime_hold` 的候选，来处那一层判了不行时要真的被推进**，而不是被收成一条
   没人看的 warning 后永久停住（#52）。

任务自己不重测漏斗逻辑（那是 `test_advance.py` 的事），只测「怎么把 `advance` 组织起来」。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from apps.strategy_factory import tasks
from apps.strategy_factory.config import CONFIG
from apps.strategy_factory.evaluate import OosEvidence
from apps.strategy_factory.models import StrategyCandidate, StrategyCandidateTransition
from apps.strategy_factory.orchestrator import advance
from apps.strategy_factory.status import (
    StrategyCandidateOrigin,
    StrategyCandidateStatus,
)
from apps.strategy_factory.tasks import (
    ADVANCEABLE_STATUSES,
    advance_candidate_task,
    advance_candidates_task,
    advanceable_ids,
)
from apps.trading.models import Strategy

pytestmark = pytest.mark.django_db


def _strategy(name: str) -> Strategy:
    return Strategy.objects.create(name=name, code_path=f"strategies/{name}.py")


def _candidate(
    strategy: Strategy,
    status: StrategyCandidateStatus = StrategyCandidateStatus.REGISTERED,
) -> StrategyCandidate:
    return StrategyCandidate.objects.create(
        name=strategy.name,
        hypothesis="探针",
        code_path=f"~/.tradelogx/strategies/{strategy.name}.py",
        origin=StrategyCandidateOrigin.AGENT.value,
        source_ref={},
        strategy=strategy,
        status=status.value,
    )


#: 与 `test_advance.py` 同一个固定时刻（转移记录与判定都要一个可钉的钟）。
FIXED_NOW = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)


def _open_regime_gate() -> None:
    """把 `REGIME_GATE` 切到执行态（票 #50 / #52）。

    不切的话档位是出厂默认 `SHADOW`，闸门**不施加动作**（候选照常升档），于是
    「停 `regime_hold`」这个前提根本不成立，用例验的会是另一条路。
    """
    from apps.regime.models import (
        ActorKind,
        MechanismKind,
        MechanismMode,
        RegimeMechanismSwitch,
    )

    RegimeMechanismSwitch.objects.create(
        kind=MechanismKind.REGIME_GATE.value,
        from_mode=MechanismMode.SHADOW.value,
        to_mode=MechanismMode.EXECUTING.value,
        at=FIXED_NOW - timedelta(days=1),
        actor_kind=ActorKind.CLI.value,
        actor_name="ops",
        reason="测试：把行情阶段 gate 切到执行态",
    )


def _held_failure_deps(*, oos=None, paper=None):
    """「来处那一层判了不行」的注入式接线（行情格给 `fit`）。

    行情给 `fit` 是刻意的：「这一层判了不行」不该被行情判定影响；给 `unfit` 会让
    「是闸门拦的」与「是那一层判的」在结论里混成一体，读不出本票要验的那一支。
    """
    from decimal import Decimal

    from apps.strategy_factory.evaluate import RegimeEvidence, RegimePoolCellEvidence
    from apps.strategy_factory.orchestrator import AdvanceDeps, RunContext

    def _ctx() -> RunContext:
        return RunContext(
            symbol="BTC/USDT:USDT",
            timeframe="1h",
            evaluation_start=date(2026, 1, 1),
            evaluation_end=date(2026, 4, 11),
            initial_capital=Decimal("1000"),
            parameters={"exit_at_mid": False, "position_pct": 0.2},
        )

    fitted = RegimeEvidence(regime="range", cell=RegimePoolCellEvidence(state="fit"))
    return AdvanceDeps(
        resolve_run_context=lambda candidate: _ctx(),
        start_in_sample=lambda candidate, ctx, plan: {},
        read_in_sample=lambda candidate: None,
        start_oos=lambda candidate, ctx, plan, rid: {},
        read_oos=lambda candidate: oos,
        create_paper_session=lambda candidate: None,
        read_paper=lambda candidate: paper,
        read_regime=lambda candidate: fitted,
        notify=lambda candidate, message: True,
        now=lambda: FIXED_NOW,
    )


class TestAdvanceCandidateTask:
    def test_missing_candidate_is_reported_not_raised(self):
        result = advance_candidate_task("00000000-0000-0000-0000-000000000000")
        assert result["ok"] is False
        assert "不存在" in result["error"]

    def test_registered_candidate_moves_one_step(self):
        strategy = _strategy("task_one_step")
        candidate = _candidate(strategy)

        result = advance_candidate_task(str(candidate.pk))

        assert result["ok"] is True
        assert result["from_status"] == "registered"
        assert result["to_status"] == "in_sample_running"
        assert result["reason_code"] == "in_sample_run_started"
        assert result["changed"] is True
        assert result["recorded"] is True
        candidate.refresh_from_db()
        assert candidate.status == "in_sample_running"
        assert (
            StrategyCandidateTransition.objects.filter(candidate=candidate).count() == 1
        )

    def test_wiring_error_propagates(self):
        """缺跑批判据是接线错误：任务照实失败（可见），不落成一次判定。"""
        from apps.strategy_factory.orchestrator import MissingRunContextError

        strategy = _strategy("task_no_context")
        candidate = _candidate(strategy, StrategyCandidateStatus.IN_SAMPLE_RUNNING)

        with pytest.raises(MissingRunContextError):
            advance_candidate_task(str(candidate.pk))

        candidate.refresh_from_db()
        assert candidate.status == "in_sample_running"


class TestAdvanceCandidatesTask:
    def test_disabled_switch_skips_scanning(self, monkeypatch):
        monkeypatch.setattr(
            tasks, "CONFIG", replace(CONFIG, auto_advance_enabled=False)
        )
        strategy = _strategy("task_switch_off")
        candidate = _candidate(strategy)

        result = advance_candidates_task()

        assert result["skipped"] == "auto_advance_disabled"
        candidate.refresh_from_db()
        assert candidate.status == "registered", "总开关关掉时状态不得变化"

    def test_advances_every_ready_candidate(self):
        first = _candidate(_strategy("task_batch_a"))
        second = _candidate(_strategy("task_batch_b"))

        result = advance_candidates_task()

        assert result["candidates"] == 2
        assert result["failed"] == []
        assert {row["candidate_id"] for row in result["advanced"]} == {
            str(first.pk),
            str(second.pk),
        }

    def test_one_failure_does_not_stop_the_batch(self, monkeypatch):
        """一条候选缺跑批判据 → 记进 `failed`，其余照常推进。"""
        broken = _candidate(
            _strategy("task_broken"), StrategyCandidateStatus.IN_SAMPLE_RUNNING
        )
        healthy = _candidate(_strategy("task_healthy"))

        result = advance_candidates_task()

        assert [row["candidate_id"] for row in result["failed"]] == [str(broken.pk)]
        assert "MissingRunContextError" in result["failed"][0]["error"]
        assert [row["candidate_id"] for row in result["advanced"]] == [str(healthy.pk)]
        broken.refresh_from_db()
        healthy.refresh_from_db()
        assert broken.status == "in_sample_running", "失败的那条不动"
        assert healthy.status == "in_sample_running", "其余的照常前进"

    def test_limit_caps_the_scan(self):
        for index in range(3):
            _candidate(_strategy(f"task_limit_{index}"))

        result = advance_candidates_task(limit=2)

        assert result["candidates"] == 2

    def test_waiting_and_terminal_states_are_not_scanned(self):
        """要人来动 / 已结束的候选不进扫描集合（白读一遍库没有意义）。"""
        skip = [
            StrategyCandidateStatus.REGISTRATION_FAILED,
            StrategyCandidateStatus.IN_SAMPLE_FAILED,
            StrategyCandidateStatus.OOS_FAILED,
            StrategyCandidateStatus.PAPER_FAILED,
            StrategyCandidateStatus.AWAITING_LIVE_APPROVAL,
            StrategyCandidateStatus.LIVE,
            StrategyCandidateStatus.REJECTED,
            StrategyCandidateStatus.CANCELLED,
        ]
        for index, status in enumerate(skip):
            _candidate(_strategy(f"task_skip_{index}"), status)
        ready = _candidate(_strategy("task_ready"))

        assert advanceable_ids() == [str(ready.pk)]
        assert set(ADVANCEABLE_STATUSES) == {
            "registered",
            "in_sample_running",
            "oos_running",
            "paper_pending",
            "paper_running",
            # 被行情闸门拦下的候选（#37）。它**必须**在扫描集合里：批扫描是
            # 「行情翻回来了吗」唯一的自动驱动，漏掉它的后果是候选安静地停在
            # `regime_hold`——转移表给了回边、判定层也会放行，但没有东西去推那一下。
            "regime_hold",
        }

    def test_paper_running_candidate_is_scanned_but_holds(self):
        """`paper_running` 的判定归 #24；现在扫到它只会停在观察中（不落噪音记录）。"""
        strategy = _strategy("task_observing")
        candidate = _candidate(strategy, StrategyCandidateStatus.PAPER_RUNNING)

        result = advance_candidates_task()

        assert result["failed"] == []
        assert result["advanced"][0]["reason_code"] == "paper_observing"
        assert result["advanced"][0]["changed"] is False
        candidate.refresh_from_db()
        assert candidate.status == "paper_running"


def test_every_scanned_status_has_a_verdict():
    """扫描集合里的每个状态，判定层都能给出一个结论（不会在任务里抛未知状态）。"""
    from apps.strategy_factory.evaluate import CandidateEvidence, evaluate_candidate

    for value in ADVANCEABLE_STATUSES:
        verdict = evaluate_candidate(
            CandidateEvidence(status=StrategyCandidateStatus(value))
        )
        assert verdict.reason_code.value


class TestAHeldCandidateWhoseLayerFailsIsAdvancedNotWarned:
    """停 `regime_hold` 的候选，来处那一层判了「不行」时，批任务**必须推进它**（#52）。

    修之前：`advance()` 抛 `InvalidTransitionError`，批循环把它收成 `failed` 一条 +
    一条 `logger.warning`，候选状态原地不动；下一轮扫描再来一遍——**每 5 分钟一次，
    永久**，而它在页面上与「行情还没翻」一模一样（只有翻日志才看得出）。

    这一组验的是**批任务那一侧**（`test_advance.py` 验的是 `advance()` 本身）：
    这类候选要落进 `advanced`、`failed` 必须为空，且连跑两轮不再产生 warning。
    """

    def _held(self, *, tag, from_status, layer):
        """停 `regime_hold` 的候选 + 真实的「从 X 进来」转移记录。

        `evidence.layers.in_sample` 预置一个主行 id：样本外的判定要拿它当衰减分母，
        缺了它会在起跑前抛 `AdvanceWiringError`——那是另一条接线错误，不是本票的现场。
        """
        strategy = _strategy(f"task_held_{tag}")
        candidate = _candidate(strategy, StrategyCandidateStatus.REGIME_HOLD)
        StrategyCandidate.objects.filter(pk=candidate.pk).update(
            evidence={
                "layers": {
                    "in_sample": {"result_id": "00000000-0000-0000-0000-000000000001"}
                }
            }
        )
        StrategyCandidateTransition.objects.create(
            candidate=candidate,
            layer=layer,
            from_status=from_status.value,
            to_status=StrategyCandidateStatus.REGIME_HOLD.value,
            reason_code="regime_unfit",
            detail={},
            at=FIXED_NOW,
        )
        return candidate

    def test_the_batch_advances_it_and_records_no_warning(self, monkeypatch, caplog):
        """`failed` 为空、`advanced` 有一条、候选状态已变、**没有**那条 warning。"""
        import logging

        _open_regime_gate()
        candidate = self._held(
            tag="oos_error",
            from_status=StrategyCandidateStatus.OOS_RUNNING,
            layer="oos",
        )
        deps = _held_failure_deps(
            oos=OosEvidence(error="样本内主行不存在（#52 批任务）")
        )
        monkeypatch.setattr(
            tasks, "advance", lambda candidate: advance(candidate, deps=deps)
        )

        with caplog.at_level(logging.WARNING, logger="apps.strategy_factory.tasks"):
            result = advance_candidates_task()

        assert result["failed"] == [], result["failed"]
        assert [row["candidate_id"] for row in result["advanced"]] == [
            str(candidate.pk)
        ]
        assert result["advanced"][0]["reason_code"] == "oos_backtest_failed"
        candidate.refresh_from_db()
        assert candidate.status == "oos_running", "这一轮退回它进来的那一层"
        assert "[StrategyFactory] advance 候选" not in caplog.text, (
            "不该再有那条 warning"
        )

    def test_the_second_scan_lands_it_in_failed_without_warning(
        self, monkeypatch, caplog
    ):
        """第二轮回扫：由那一层自己判去 `oos_failed`，仍然没有 warning。

        「第一轮不炸」还不够——如果修法只是「换个地方停住」，第二轮会原地不动，
        而任务结果里看不出这件事（`advanced` 照样有一条）。所以这里断言**状态真的变了**。
        """
        import logging

        _open_regime_gate()
        candidate = self._held(
            tag="oos_two_rounds",
            from_status=StrategyCandidateStatus.OOS_RUNNING,
            layer="oos",
        )
        deps = _held_failure_deps(
            oos=OosEvidence(error="样本内主行不存在（#52 批任务）")
        )
        monkeypatch.setattr(
            tasks, "advance", lambda candidate: advance(candidate, deps=deps)
        )

        with caplog.at_level(logging.WARNING, logger="apps.strategy_factory.tasks"):
            first = advance_candidates_task()
            second = advance_candidates_task()

        assert first["failed"] == []
        assert second["failed"] == [], second["failed"]
        assert second["advanced"][0]["reason_code"] == "oos_backtest_failed"
        candidate.refresh_from_db()
        assert candidate.status == "oos_failed"
        assert "[StrategyFactory] advance 候选" not in caplog.text
