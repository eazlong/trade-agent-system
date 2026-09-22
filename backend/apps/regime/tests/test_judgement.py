"""判定记录、查询语义与两根轴合成（第①段单元 4）。

这一组测试真正要钉死的只有三条，其余都是在给它们加护栏：

1. **「待生效」是查询语义**——`current_judgement()` 比的是 `effective_at`，不是
   `attribute_date`。比错字段的表现极其隐蔽：每天 08:00 之后所有查询都报「没有生效
   中的阶段」，而记录本身条条正常。
2. **判不出来就不落记录**——数据不足是 `skipped`，故障是异常。两者混同会让「预热没
   走完」天天冒充故障告警，或者让判定静默死掉。
3. **资讯只能让系统更保守**——结构上钉住（对所有基础阶段取合成结果都不得更激进），
   不是靠单元 5 接资讯时自觉。

DB 用的都是 `TestCase`（真事务回滚），K 线用合成序列而不是固定 fixture：判定参数
（ATR 14 / EMA 20·60 / 分位窗口 250）决定了「能被判定」需要 250 天以上的序列，
写死 340 行 fixture 不如生成出来，改参数时也只改一处。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from django.db import IntegrityError
from django.test import TestCase

from apps.common.time_utils import business_tz, to_business
from apps.regime import config
from apps.regime.judgement import (
    _REASON_MIN_DWELL,
    apply_escalation,
    apply_min_dwell,
    build_evidence,
    current_judgement,
    in_force_since,
    load_candles,
    pending_judgement,
    run_daily_judgement,
)
from apps.regime.models import (
    Escalation,
    RegimeJudgement,
    business_midnight,
)
from apps.regime.quant import PRIORITY, BaseRegime

SYMBOL = config.CANDLES.symbol

#: 判定机制的名义运行日：北京 2026-09-22。与 test_timing.py 取同一个日子，
#: 但这里的时刻刻意不落在日界上。
RUN_DAY = date(2026, 9, 22)

#: 名义运行时刻：北京 2026-09-22 11:00（= UTC 03:00）。刻意不取日界那一刻——
#: 判定挂在 5 分钟心跳上，绝大多数 tick 都发生在日界之后的白天，那才是常态。
NOW = datetime(2026, 9, 22, 3, 0, tzinfo=timezone.utc)

#: 此刻应已收盘的那根日线（运行日 D → 签署日 D−1）。
SIGNED_DAY = RUN_DAY - timedelta(days=1)


def series(
    n: int = 340,
    *,
    calm_from: int = 300,
    step: float = 1.0,
    band: float = 0.003,
    wide: float = 0.03,
    end_from: int | None = None,
    end_step: float | None = None,
    end_band: float | None = None,
) -> list[dict]:
    """合成一条能在**默认参数**下判定的日线序列。

    形状：前段用 ±3% 的日内振幅把 ATR% 抬起来，末段用 ±0.3% 压下去，于是最后一天的
    ATR% 分位落在窗口最低端（远低于 `high_vol_quantile`），再由末段的 EMA 斜率与间距
    决定档位。三个变体（上行 / 下行 / 高波动）都由这几个开关摆出来：

    - 默认 → 上行趋势（末段单边上涨）
    - `end_step=-1.5` → 下行趋势
    - `end_band=0.06` → 高波动（末段恢复宽幅，分位跳到 1.0）
    - `n=100` → 判不出来（分位窗口只攒到 86 天）
    """
    rows = []
    for i in range(n):
        if i < calm_from:
            close, half = 100.0 + (i % 7) * 0.3, wide
        elif end_from is not None and i >= end_from:
            close = 100.0 + (end_from - calm_from) * step + (i - end_from) * (
                step if end_step is None else end_step
            )
            half = band if end_band is None else end_band
        else:
            close, half = 100.0 + (i - calm_from) * step, band
        rows.append({"close": close, "high": close * (1 + half), "low": close * (1 - half)})
    return rows


def persist(rows: list[dict], last_day: date, symbol: str = SYMBOL) -> None:
    """把合成序列**整体替换**成这串日线，最后一根是 `last_day`。

    先清空该标的的既有行：判定读的是全序列（EMA 带无限记忆），只覆盖一部分等于换了
    一条序列却没有说出口。测试里连续 persist 两个变体时，这一点决定了结果是不是
    后一个变体。
    """
    from apps.regime.models import DailyCandle

    n = len(rows)
    first_day = last_day - timedelta(days=n - 1)
    DailyCandle.objects.filter(symbol=symbol).delete()
    DailyCandle.objects.bulk_create(
        [
            DailyCandle(
                symbol=symbol,
                date=first_day + timedelta(days=i),
                open_time=datetime.combine(
                    first_day + timedelta(days=i), time(0, 0), tzinfo=timezone.utc
                ),
                open=Decimal(str(row["close"])),
                high=Decimal(str(row["high"])),
                low=Decimal(str(row["low"])),
                close=Decimal(str(row["close"])),
                volume=Decimal("1"),
            )
            for i, row in enumerate(rows)
        ]
    )


def make_judgement(
    effective_day: date,
    regime: BaseRegime | str,
    *,
    attribute_day: date | None = None,
    escalation: str = "",
    symbol: str = SYMBOL,
) -> RegimeJudgement:
    """直接造一条历史判定记录（`effective_day` 的北京 08:00 生效）。"""
    value = regime.value if isinstance(regime, BaseRegime) else regime
    return RegimeJudgement.objects.create(
        symbol=symbol,
        attribute_date=attribute_day or (effective_day - timedelta(days=2)),
        effective_at=business_midnight(effective_day),
        base_regime=value,
        escalation=escalation,
        effective_regime=value,
    )


# --------------------------------------------------------------------------- #
# 模型
# --------------------------------------------------------------------------- #


class TestRecordKeepsBothAxes(TestCase):
    """三值必须同时留存，否则归因不可回答。"""

    def test_the_three_values_round_trip_separately(self):
        record = make_judgement(
            RUN_DAY, BaseRegime.RANGE, escalation=Escalation.NEWS.value
        )
        record.effective_regime = BaseRegime.HIGH_VOL.value
        record.save()

        stored = RegimeJudgement.objects.get(pk=record.pk)
        self.assertEqual(stored.base_regime, "range")
        self.assertEqual(stored.escalation, "news")
        self.assertEqual(
            stored.effective_regime,
            "high_vol",
            "生效阶段被资讯抬升过，基础阶段必须仍是纯量化口径的原值——"
            "两个值挤进一列就再也答不了「今天这个阶段是量化判出来的还是资讯抬上来的」",
        )

    def test_no_escalation_is_an_empty_string_not_null(self):
        record = make_judgement(RUN_DAY, BaseRegime.RANGE)
        self.assertEqual(record.escalation, "")
        self.assertEqual(
            RegimeJudgement._meta.get_field("escalation").choices[0],
            ("", "无"),
            "「无抬升」在 choices 里是空串；改成 NULL 会多出「空串 vs NULL」两种空值",
        )

    def test_symbol_and_effective_at_are_unique(self):
        make_judgement(RUN_DAY, BaseRegime.RANGE)
        with self.assertRaises(IntegrityError):
            make_judgement(RUN_DAY, BaseRegime.UPTREND)

    def test_run_day_and_run_at_are_derived_from_attribute_date(self):
        record = make_judgement(RUN_DAY, BaseRegime.RANGE)
        self.assertEqual(record.attribute_date, RUN_DAY - timedelta(days=2))
        self.assertEqual(
            record.run_day,
            record.attribute_date + timedelta(days=1),
            "run_day 是派生属性，不是独立字段——存两份必然漂移",
        )
        self.assertEqual(record.run_at, business_midnight(record.run_day))
        self.assertEqual(
            record.run_at,
            record.effective_at - timedelta(days=1),
            "名义运行时刻恰好在生效时刻前一天：拿它跟现在比才得到「结论新鲜度」",
        )
        self.assertLess(record.run_at, record.effective_at)


class TestBusinessMidnightIsBuiltInTheBusinessTimezone(TestCase):
    def test_it_uses_the_business_timezone_not_a_hardcoded_offset(self):
        moment = business_midnight(RUN_DAY)
        self.assertEqual(moment.tzinfo, business_tz())
        self.assertEqual(to_business(moment).hour, 8)


# --------------------------------------------------------------------------- #
# 查询语义
# --------------------------------------------------------------------------- #


class TestCurrentAndPendingAreTwoQueries(TestCase):
    """`effective_at <= now` 的最新一条 vs `> now` 的最早一条。"""

    def setUp(self):
        # 三条连续记录：09-22 / 09-23 / 09-24 各自北京 08:00 生效。
        self.r22 = make_judgement(RUN_DAY, BaseRegime.RANGE)
        self.r23 = make_judgement(RUN_DAY + timedelta(days=1), BaseRegime.UPTREND)
        self.r24 = make_judgement(RUN_DAY + timedelta(days=2), BaseRegime.UPTREND)

    def test_before_the_first_effective_moment_there_is_nothing_in_force(self):
        now = business_midnight(RUN_DAY) - timedelta(hours=1)
        self.assertIsNone(
            current_judgement(now=now),
            "还没有任何结论生效，必须返回 None。给个默认值就等于伪造了一次判定",
        )
        self.assertEqual(pending_judgement(now=now), self.r22)

    def test_the_exact_effective_moment_is_already_in_force(self):
        """边界是闭的：`effective_at <= now`，到点即生效。"""
        self.assertEqual(current_judgement(now=business_midnight(RUN_DAY)), self.r22)
        self.assertEqual(pending_judgement(now=business_midnight(RUN_DAY)), self.r23)

    def test_mid_day_the_previous_record_is_still_the_one_in_force(self):
        now = business_midnight(RUN_DAY + timedelta(days=1)) + timedelta(hours=5)
        self.assertEqual(current_judgement(now=now), self.r23)
        self.assertEqual(pending_judgement(now=now), self.r24)

    def test_after_the_last_effective_moment_nothing_is_pending(self):
        now = business_midnight(RUN_DAY + timedelta(days=5))
        self.assertEqual(current_judgement(now=now), self.r24)
        self.assertIsNone(pending_judgement(now=now))


class TestTheQueryComparesTheMomentNotTheDate(TestCase):
    """独立成类：这条要造的记录与 `setUp` 那三条不共用 effective_at。"""

    def test_a_pending_record_is_never_reported_as_current(self):
        """比 `effective_at` 而不是 `attribute_date` 的那条分界线。

        这条记录签署于「今天」，但它要到后天早上才咬人。若查询条件写成
        `attribute_date <= now.date()`，它今天就会被当成生效态——于是机制会提前一天
        按新阶段停策略，而记录本身完全正常，看不出任何异常。
        """
        record = make_judgement(
            RUN_DAY + timedelta(days=2),
            BaseRegime.DOWNTREND,
            attribute_day=RUN_DAY,
        )
        now = business_midnight(RUN_DAY) + timedelta(hours=3)
        self.assertEqual(record.attribute_date, to_business(now).date())
        self.assertLess(record.attribute_date, to_business(record.effective_at).date())
        self.assertIsNone(current_judgement(now=now))
        self.assertEqual(pending_judgement(now=now), record)


class TestQueriesAreScopedToTheSymbol(TestCase):
    def test_one_symbols_records_do_not_answer_for_another(self):
        make_judgement(RUN_DAY, BaseRegime.RANGE, symbol="ETH/USDT")
        self.assertIsNone(current_judgement(symbol=SYMBOL, now=business_midnight(RUN_DAY)))
        self.assertIsNotNone(
            current_judgement(symbol="ETH/USDT", now=business_midnight(RUN_DAY))
        )


class TestInForceSinceWalksBackToTheLastChange(TestCase):
    def test_a_single_record_starts_at_its_own_effective_moment(self):
        record = make_judgement(RUN_DAY, BaseRegime.RANGE)
        self.assertEqual(in_force_since(record), record.effective_at)

    def test_repeated_records_do_not_restart_the_clock(self):
        """同阶段每天落一条记录，持续期不能被这些记录打断。

        判定的产物是「每天一条记录」，而「阶段」是慢变量。持续期要是按记录算，
        一个稳定运行半年的阶段会天天显示「才刚开始」，永远不许切换。
        """
        first = make_judgement(RUN_DAY, BaseRegime.RANGE)
        for offset in (1, 2, 3):
            make_judgement(RUN_DAY + timedelta(days=offset), BaseRegime.RANGE)
        latest = make_judgement(RUN_DAY + timedelta(days=4), BaseRegime.RANGE)
        self.assertEqual(in_force_since(latest), first.effective_at)

    def test_a_missing_day_does_not_break_the_run(self):
        """判定缺失的日子没有记录，但阶段并没有变过，持续期照旧。"""
        first = make_judgement(RUN_DAY, BaseRegime.RANGE)
        # 跳过 09-23，直接 09-24。
        later = make_judgement(RUN_DAY + timedelta(days=2), BaseRegime.RANGE)
        self.assertEqual(in_force_since(later), first.effective_at)

    def test_the_clock_restarts_when_the_effective_stage_changes(self):
        """中途被资讯抬升过，新阶段的持续期从被抬升那一刻重新起算。"""
        make_judgement(RUN_DAY, BaseRegime.UPTREND)
        make_judgement(RUN_DAY + timedelta(days=1), BaseRegime.UPTREND)
        escalated = make_judgement(
            RUN_DAY + timedelta(days=2),
            BaseRegime.HIGH_VOL,
            escalation=Escalation.NEWS.value,
        )
        self.assertEqual(in_force_since(escalated), escalated.effective_at)


# --------------------------------------------------------------------------- #
# 两根轴
# --------------------------------------------------------------------------- #


class TestEscalationOnlyEverGoesConservative(TestCase):
    def test_no_flag_leaves_the_base_untouched(self):
        for base in BaseRegime:
            with self.subTest(base=base):
                self.assertIs(apply_escalation(base, ""), base)

    def test_news_always_raises_to_high_vol(self):
        for base in BaseRegime:
            with self.subTest(base=base):
                self.assertIs(apply_escalation(base, Escalation.NEWS.value), BaseRegime.HIGH_VOL)

    def test_news_never_lands_on_a_more_aggressive_tier(self):
        """结构性不变量：合成结果在优先级里的位次不得低于基础阶段。

        这条将来只有在加了第二种抬升（比如事件熔断）之后才可能被违反，而那时最可能
        的写法是「按事件严重程度沿阶梯抬一档」——那正是会让系统**更激进**的写法。
        所以现在就用一个覆盖全档位的断言把它钉住，而不是等它发生。
        """
        for base in PRIORITY:
            with self.subTest(base=base):
                effective = apply_escalation(base, Escalation.NEWS.value)
                self.assertLessEqual(
                    PRIORITY.index(effective),
                    PRIORITY.index(base),
                    f"{base.value} 被抬升成 {effective.value}，比基础阶段更激进——"
                    "资讯（LLM 的产物）只能让系统更保守",
                )

    def test_a_no_op_escalation_still_gets_recorded(self):
        """基础阶段已经是高波动时，抬升不改变结果，但标志必须留下。

        资讯那根轴的输出不可重放：今天资讯说了什么，只有这条记录知道。因为它「恰好
        没改变结果」而丢掉，等于丢掉了「今天资讯判断过高波动」这条事实。
        """
        self.assertIs(apply_escalation(BaseRegime.HIGH_VOL, Escalation.NEWS.value), BaseRegime.HIGH_VOL)
        record = make_judgement(
            RUN_DAY,
            BaseRegime.HIGH_VOL,
            escalation=Escalation.NEWS.value,
        )
        self.assertEqual(record.escalation, "news")
        self.assertEqual(record.effective_regime, "high_vol")

    def test_an_unknown_flag_is_a_loud_error(self):
        with self.assertRaises(ValueError):
            apply_escalation(BaseRegime.RANGE, "halt")


class TestMinDwellRule(TestCase):
    """阶段是慢变量：切换要等当前阶段站稳 `min_dwell_days` 个自然日。"""

    MIN = config.JUDGEMENT_LIFECYCLE.min_dwell_days

    def setUp(self):
        self.candidate_effective_at = business_midnight(RUN_DAY + timedelta(days=1))

    def test_cold_start_adopts_the_candidate(self):
        base, reason, numbers = apply_min_dwell(
            BaseRegime.RANGE, self.candidate_effective_at, None, self.MIN
        )
        self.assertIs(base, BaseRegime.RANGE)
        self.assertEqual(reason, "")
        self.assertIsNone(
            numbers["in_force_days"],
            "没有当前阶段可站，持续期无从谈起——首条判定必须落下来，"
            "否则查询语义永远返回 None，整个机制卡在起点",
        )

    def test_an_unchanged_stage_is_adopted_immediately(self):
        current = make_judgement(RUN_DAY, BaseRegime.RANGE)
        base, reason, numbers = apply_min_dwell(
            BaseRegime.RANGE, self.candidate_effective_at, current, self.MIN
        )
        self.assertIs(base, BaseRegime.RANGE)
        self.assertEqual(reason, "", "持续期约束的是**切换**，不是维持")
        self.assertEqual(
            numbers["in_force_days"],
            1,
            "当前记录 D 08:00 生效、候选 D+1 08:00 生效，于是「候选生效那一刻已站了 1 天」——"
            "分母是候选生效时刻，不是今天",
        )

    def test_a_switch_before_the_dwell_elapses_is_carried(self):
        current = make_judgement(RUN_DAY, BaseRegime.UPTREND)
        base, reason, numbers = apply_min_dwell(
            BaseRegime.DOWNTREND, self.candidate_effective_at, current, self.MIN
        )
        self.assertIs(base, BaseRegime.UPTREND, "被拦下时沿用当前生效阶段")
        self.assertEqual(reason, _REASON_MIN_DWELL)
        self.assertEqual(numbers["carried_from_effective_at"], current.effective_at.isoformat())
        self.assertLess(numbers["in_force_days"], self.MIN)

    def test_a_switch_after_the_dwell_elapses_is_adopted(self):
        current = make_judgement(RUN_DAY - timedelta(days=self.MIN), BaseRegime.UPTREND)
        base, reason, numbers = apply_min_dwell(
            BaseRegime.DOWNTREND, self.candidate_effective_at, current, self.MIN
        )
        self.assertIs(base, BaseRegime.DOWNTREND)
        self.assertEqual(reason, "")
        self.assertGreaterEqual(numbers["in_force_days"], self.MIN)

    def test_the_dwell_boundary_is_inclusive(self):
        """恰好站满 `min_dwell_days` 就允许切换——`>=`，不是 `>`。"""
        current = make_judgement(RUN_DAY - timedelta(days=self.MIN - 1), BaseRegime.UPTREND)
        base, reason, _ = apply_min_dwell(
            BaseRegime.DOWNTREND, self.candidate_effective_at, current, self.MIN
        )
        self.assertIs(base, BaseRegime.DOWNTREND)
        self.assertEqual(reason, "")

    def test_the_comparison_uses_the_effective_stage_not_the_base_stage(self):
        """当前记录被资讯抬升过时，比较对象是「现在真正在起作用的阶段」。

        这条记录的基础阶段是上行趋势（纯量化口径），但生效阶段是高波动（资讯抬上来的）。
        今天的候选也是上行趋势：与**生效**阶段比，这是一次切换（要受持续期约束）；
        与基础阶段比，则会被读成「没变」而直接采纳。真正在拦人的是高波动。
        """
        current = make_judgement(
            RUN_DAY, BaseRegime.UPTREND, escalation=Escalation.NEWS.value
        )
        current.effective_regime = BaseRegime.HIGH_VOL.value
        current.save(update_fields=["effective_regime"])

        base, reason, _ = apply_min_dwell(
            BaseRegime.UPTREND, self.candidate_effective_at, current, self.MIN
        )
        self.assertIs(base, BaseRegime.HIGH_VOL)
        self.assertEqual(reason, _REASON_MIN_DWELL)

    def test_in_force_days_is_measured_at_the_candidate_moment(self):
        """`in_force_days` 与 `min_dwell_days` 同尺度：都是「候选生效那天已经站了几天」。

        拿「今天」去算会少一天，于是持续期在边界上偏紧，而偏紧的表现是切换比设计
        晚一天——没有任何报错，只是慢。
        """
        current = make_judgement(RUN_DAY - timedelta(days=self.MIN), BaseRegime.UPTREND)
        _, _, numbers = apply_min_dwell(
            BaseRegime.DOWNTREND, self.candidate_effective_at, current, self.MIN
        )
        self.assertEqual(
            numbers["in_force_days"],
            (self.candidate_effective_at - current.effective_at).days,
        )
        self.assertEqual(numbers["in_force_days"], self.MIN + 1)


class TestEvidenceSeparatesWhatQuantSawFromWhatWasDecided(TestCase):
    def test_the_two_groups_are_both_present(self):
        class Label:
            regime = BaseRegime.UPTREND
            atr = 1.5
            atr_pct = 0.01
            atr_pct_rank = 0.2
            quantile_sample = 250
            ema_fast = 110.0
            ema_slow = 100.0
            ema_slope = 1.0
            separation = 3.0

        evidence = build_evidence(Label(), {"action": "adopted", "reason": ""})
        self.assertEqual(
            set(evidence), {"quant", "decision"}, "两组回答的是两个不同的问题"
        )
        self.assertEqual(evidence["quant"]["regime"], "uptrend")
        self.assertEqual(evidence["quant"]["quantile_sample"], 250)

    def test_an_undecidable_label_keeps_the_numbers(self):
        class Label:
            regime = None
            atr = None
            atr_pct = None
            atr_pct_rank = None
            quantile_sample = 86
            ema_fast = 100.0
            ema_slow = None
            ema_slope = None
            separation = None

        evidence = build_evidence(Label(), {"action": "adopted", "reason": ""})
        self.assertIsNone(evidence["quant"]["regime"])
        self.assertEqual(
            evidence["quant"]["quantile_sample"],
            86,
            "未判定的日子仍要留下能算的数字，供事后解释「那天为什么判不出来」",
        )


# --------------------------------------------------------------------------- #
# 每日判定
# --------------------------------------------------------------------------- #


class TestRunDailyJudgementSkipsWhatItCannotJudge(TestCase):
    def test_no_candles_is_a_skip_not_a_failure(self):
        result = run_daily_judgement(now=NOW)
        self.assertEqual(result["skipped"], "no_candles")
        self.assertEqual(RegimeJudgement.objects.count(), 0)

    def test_stale_candles_are_a_skip(self):
        """最后一根日线不是「此刻应已收盘」的那一根时，宁可今天没有结论。

        用它判定等于「用前天的行情签署今天的结论」，而签署日正是切片时的 join 键。
        """
        persist(series(), last_day=RUN_DAY)  # 应为 SIGNED_DAY
        result = run_daily_judgement(now=NOW)
        self.assertEqual(result["skipped"], "stale_candles")
        self.assertEqual(result["expected_candle"], SIGNED_DAY.isoformat())
        self.assertEqual(RegimeJudgement.objects.count(), 0)

    def test_a_series_too_short_to_judge_is_a_skip_with_the_reason(self):
        """预热没走完是**已知的、有限的**状态，不是故障。

        它要是抛异常，二百多天里每天一次假告警，之后没人再看这个告警。
        """
        persist(series(n=100, calm_from=90), last_day=SIGNED_DAY)
        result = run_daily_judgement(now=NOW)
        self.assertEqual(result["skipped"], "undecidable")
        self.assertEqual(result["attribute_date"], SIGNED_DAY.isoformat())
        self.assertEqual(RegimeJudgement.objects.count(), 0)

    def test_a_skip_does_not_disturb_the_record_in_force(self):
        """判不出来时保持上一有效状态——由查询语义天然承担，不需要写一条「沿用」记录。"""
        previous = make_judgement(SIGNED_DAY, BaseRegime.UPTREND)
        persist(series(n=100, calm_from=90), last_day=SIGNED_DAY)
        run_daily_judgement(now=NOW)
        self.assertEqual(current_judgement(now=NOW), previous)
        self.assertEqual(RegimeJudgement.objects.count(), 1)


class TestRunDailyJudgementRecordsOneConclusionPerDay(TestCase):
    def setUp(self):
        persist(series(), last_day=SIGNED_DAY)

    def test_it_records_the_three_values_and_the_evidence(self):
        result = run_daily_judgement(now=NOW)

        self.assertEqual(result["base_regime"], BaseRegime.UPTREND.value)
        self.assertEqual(result["escalation"], "")
        self.assertEqual(result["effective_regime"], BaseRegime.UPTREND.value)
        self.assertEqual(result["decision"], "adopted")
        self.assertTrue(result["recorded"])

        record = RegimeJudgement.objects.get()
        self.assertEqual(record.symbol, SYMBOL)
        self.assertEqual(record.attribute_date, SIGNED_DAY)
        self.assertEqual(
            record.effective_at,
            business_midnight(RUN_DAY + timedelta(days=1)),
            "运行日 D 得出的结论从 D+1 北京 08:00 起生效——整整一天的可见期",
        )
        self.assertEqual(record.run_day, RUN_DAY)
        self.assertEqual(record.evidence["quant"]["regime"], BaseRegime.UPTREND.value)
        self.assertEqual(record.evidence["decision"]["action"], "adopted")
        self.assertEqual(record.evidence["quant"]["quantile_sample"], 250)

    def test_the_parameter_snapshot_is_embedded_in_the_record(self):
        run_daily_judgement(now=NOW)
        snapshot = RegimeJudgement.objects.get().config_snapshot
        self.assertIn("judgement", snapshot)
        self.assertIn("judgement_lifecycle", snapshot)
        self.assertEqual(
            snapshot["judgement"]["atr_period"], config.JUDGEMENT.atr_period
        )
        self.assertEqual(
            snapshot["judgement_lifecycle"]["min_dwell_days"],
            config.JUDGEMENT_LIFECYCLE.min_dwell_days,
            "留痕靠快照，不靠读回配置——半年后要解释某条结论用的是哪套参数，"
            "答案必须在记录自己身上",
        )

    def test_the_heartbeat_running_again_the_same_day_writes_nothing_new(self):
        first = run_daily_judgement(now=NOW)
        again = run_daily_judgement(now=NOW + timedelta(minutes=5))

        self.assertTrue(first["recorded"])
        self.assertFalse(again["recorded"])
        self.assertEqual(RegimeJudgement.objects.count(), 1)

    def test_an_existing_record_is_never_rewritten(self):
        """记录是事件，不是缓存：同一天已有结论时不更新它。

        这条同时也是资讯通道（单元 5）的**排序约束**：资讯必须在判定之前跑完，
        否则当天的记录已经落下，抬升标志就丢了——丢了的表现是「今天资讯判断过应当
        保守，而系统照常开新仓」。真需要纠正只能走数据订正。
        """
        run_daily_judgement(now=NOW)
        again = run_daily_judgement(
            now=NOW + timedelta(minutes=5), escalation=Escalation.NEWS.value
        )

        self.assertFalse(again["recorded"])
        record = RegimeJudgement.objects.get()
        self.assertEqual(record.escalation, "")
        self.assertEqual(record.effective_regime, BaseRegime.UPTREND.value)

    def test_the_decision_carries_the_current_stage_when_the_dwell_is_not_elapsed(self):
        """端到端走一遍被持续期拦下的路径：结论落在库里，内容是「维持不变」。"""
        current = make_judgement(RUN_DAY, BaseRegime.UPTREND)
        persist(series(end_from=320, end_step=-1.5), last_day=SIGNED_DAY)

        result = run_daily_judgement(now=NOW)

        self.assertEqual(result["decision"], "carried")
        self.assertEqual(result["reason"], _REASON_MIN_DWELL)
        record = RegimeJudgement.objects.get(effective_at=business_midnight(RUN_DAY + timedelta(days=1)))
        self.assertEqual(record.base_regime, BaseRegime.UPTREND.value)
        self.assertEqual(
            record.evidence["quant"]["regime"],
            BaseRegime.DOWNTREND.value,
            "量化看到的是下行趋势，最后采用的不是它——两组数字分开正是为了这个",
        )
        self.assertEqual(
            datetime.fromisoformat(
                record.evidence["decision"]["carried_from_effective_at"]
            ),
            current.effective_at,
            "存的是时刻而不是本地化字符串：这里比的是绝对时刻，"
            "「+08:00 的 08:00」与「+00:00 的 00:00」是同一刻",
        )

    def test_the_decision_switches_once_the_current_stage_has_held_long_enough(self):
        make_judgement(
            RUN_DAY - timedelta(days=config.JUDGEMENT_LIFECYCLE.min_dwell_days),
            BaseRegime.UPTREND,
        )
        persist(series(end_from=320, end_step=-1.5), last_day=SIGNED_DAY)

        result = run_daily_judgement(now=NOW)

        self.assertEqual(result["decision"], "adopted")
        self.assertEqual(result["base_regime"], BaseRegime.DOWNTREND.value)

    def test_an_escalation_is_applied_on_top_of_the_quant_axis(self):
        result = run_daily_judgement(now=NOW, escalation=Escalation.NEWS.value)
        record = RegimeJudgement.objects.get()
        self.assertEqual(result["base_regime"], BaseRegime.UPTREND.value)
        self.assertEqual(record.escalation, Escalation.NEWS.value)
        self.assertEqual(record.effective_regime, BaseRegime.HIGH_VOL.value)

    def test_the_news_reference_is_persisted_verbatim(self):
        news = {"entries": [{"title": "t", "url": "u"}], "conclusion": "high_vol"}
        run_daily_judgement(now=NOW, escalation=Escalation.NEWS.value, news_ref=news)
        self.assertEqual(RegimeJudgement.objects.get().news_ref, news)


class TestLoadCandlesReadsEverythingInOrder(TestCase):
    """全量而不是最后 250 根：EMA 是带无限记忆的递推量。"""

    def test_all_candles_come_back_ascending(self):
        persist(series(n=300, calm_from=280), last_day=SIGNED_DAY)
        rows = load_candles()
        self.assertEqual(len(rows), 300)
        self.assertEqual(rows[0]["date"], SIGNED_DAY - timedelta(days=299))
        self.assertEqual(rows[-1]["date"], SIGNED_DAY)
        self.assertEqual(set(rows[-1]), {"date", "high", "low", "close"})
