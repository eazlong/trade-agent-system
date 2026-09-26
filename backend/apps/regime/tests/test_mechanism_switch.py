"""机制整体那一档：准入体检页与那一次 INSERT（出 Shadow 那个单元）。

本文件钉的是**回溯那一侧**：Shadow 期已经跑出了什么、够不够格出 Shadow、以及人点头之后
落下的那一行流水。`test_breaker_switch.py` 钉的是同形的一页，但那一页回答的是「打开之后
会拦住什么」——同一个形状，两套完全不同的数，所以是两个文件。

本文件里最要紧的四条性质，每一条坏了都不报警、只出错：

1. **三个触发源要取并集，不是求和。** 第 163 条要的是「一年里有几天真的会施加动作」，
   而同一天可以既被资讯抬到高波动、又产生了适用性停用。求和会把频率**算高**，于是本该
   通过的机制被拦下；只算适用性停用会把频率**算低**（「资讯抬升到高波动、全场天天停开
   新仓、却从不产生适用性停用」那种机制干净通过）。两个方向都错，所以三类天数分行报、
   并集才是与门槛比的数。
2. **到期有两条腿，兜底那条必须说出来。** 兜底是为了防「没人维护的事件库让 Shadow
   无限期挂着」——而挂着的形状与正常跑着完全一样，所以「事件窗口数不足 N/3」这句话必须
   自动进流水的 `reason`，不能只活在命令的那条回复里。
3. **「尚未开始」不是 0。** 一条含资讯结论的判定都没有时，页面上任何看起来正常的 0
   （0 天、0%、0 个窗口）都会被人读成「跑了、没事」。这一档必须能说出「还没开始」。
4. **页面不是守卫。** 未到期、频率超标都不阻止人切（设计里没有这一条，硬拒绝会教人去
   找绕过它的路）；页面的职责是把没达标的项摆在人脸前——所以要有一条用例钉住「不达标
   照样写得进去」，否则下一个人「顺手加个校验」就把这条设计改掉了。

夹具锚点：起算日相对**今天**算（`START`），页面注入的 `NOW` 就是真实时钟——这一档的数全
按自然日算，没有「窗口是否与未来相交」那类与注入时刻强相关的量，所以不需要像
`test_breaker_switch` 那样把时钟往后挪几分钟。门槛一律读 `config.SHADOW`（值本身由
`test_config.py` 钉住）。
"""

from __future__ import annotations

import dataclasses
from datetime import date, datetime, time, timedelta

from django.test import TestCase
from django.utils import timezone as django_timezone

from apps.common.time_utils import business_tz, to_business
from apps.regime import config, mechanism_switch, news_verdict
from apps.regime.models import (
    ActorKind,
    BaseRegime,
    EventImpact,
    EventScope,
    EventStatus,
    MajorEvent,
    MechanismKind,
    MechanismMode,
    RegimeJudgement,
    RegimeMechanismSwitch,
    ShadowDailyRecord,
    business_midnight,
)

NOW = django_timezone.now()
TODAY = to_business(NOW).date()
SYMBOL = config.CANDLES.symbol

MIN_DAYS = config.SHADOW.min_natural_days
MIN_WINDOWS = config.SHADOW.min_event_windows
CAP_DAYS = config.SHADOW.expiry_cap_days
MAX_RATE = config.SHADOW.max_trigger_rate

#: 起算日：**刚好满 `MIN_DAYS` 个自然日**（含两端）。各用例用一个只改起算日的 helper 挪它。
START = TODAY - timedelta(days=MIN_DAYS - 1)

#: 体检页正文里那几行必须一直在的句子。它们不是装饰：第 1 句防「按一下就以为上线了」，
#: 第 2 句防「算不出来被读成通过」，第 3 句防「拿一个三个月才评估得出的条款当准入门槛」。
NOT_A_SWITCH = "不是执行开关"
TRUTH_ABSENT = "真值未录入"
SELF_FUSE_NOT_A_GATE = "不构成出 Shadow 的准入门槛"


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #


def _judgement(
    run_day: date,
    *,
    status: str = news_verdict.STATUS_OK,
    symbol: str = SYMBOL,
) -> RegimeJudgement:
    """落一条判定，`news_ref.status` 由 `status` 决定。

    三值的具体取值与本文件无关（这一档不读阶段），但字段必填，所以给一个固定值。
    `attribute_date = run_day - 1`、`effective_at = 业务日界(run_day + 1)`：这就是
    `test_shadow` 里那套映射（模型层不校验它，映射由判定链路统一实现）。
    """
    return RegimeJudgement.objects.create(
        symbol=symbol,
        attribute_date=run_day - timedelta(days=1),
        effective_at=business_midnight(run_day + timedelta(days=1)),
        base_regime=BaseRegime.RANGE.value,
        escalation="",
        effective_regime=BaseRegime.RANGE.value,
        news_ref={"status": status},
    )


def _shadow(
    day: date,
    *,
    regime: BaseRegime = BaseRegime.RANGE,
    suggested: int = 0,
    symbol: str = SYMBOL,
) -> ShadowDailyRecord:
    """落一条 Shadow 记录。`suggested_count` 与清单一起造（它是清单长度的副本）。"""
    return ShadowDailyRecord.objects.create(
        symbol=symbol,
        run_day=day,
        base_regime=regime.value,
        escalation="",
        effective_regime=regime.value,
        suggestions=[{"strategy_id": f"s{i}"} for i in range(suggested)],
        suggested_count=suggested,
    )


def _event(
    name: str,
    halt_at: datetime,
    resume_at: datetime | None = None,
    *,
    impact: str = EventImpact.HIGH.value,
    status: str = EventStatus.SCHEDULED.value,
) -> MajorEvent:
    """落一条真事件。窗口默认两小时；`triggers_halt` 的判据在模型上，这里不重写它。"""
    return MajorEvent.objects.create(
        name=name,
        scope_kind=EventScope.MARKET.value,
        symbols=[],
        event_time=halt_at,
        impact=impact,
        halt_at=halt_at,
        resume_at=resume_at if resume_at is not None else halt_at + timedelta(hours=2),
        status=status,
        created_by="tester",
    )


def _at(day: date, hour: int = 10) -> datetime:
    """业务时区某个自然日的某个钟点。事件窗口用得到（`halt_at` 是绝对时刻）。"""
    return datetime.combine(day, time(hour, 0), tzinfo=business_tz())


class _Fixture(TestCase):
    def start(self, *, days_ago: int) -> None:
        """把起算日挪到「今天往前 `days_ago` 天」——即 Shadow 已跑 `days_ago + 1` 天。"""
        _judgement(TODAY - timedelta(days=days_ago))

    def sweep(self) -> mechanism_switch.Confirmation:
        return mechanism_switch.confirmation(now=NOW)

    def body(self) -> str:
        return mechanism_switch.page(now=NOW).body

    def summary(self) -> str:
        return mechanism_switch.page(now=NOW).summary

    def back(self, *, actor_kind: ActorKind = ActorKind.CHAT, name: str = "alice"):
        """往流水里落一条「退回 Shadow」（测读侧时用，绕开写方）。"""
        return RegimeMechanismSwitch.objects.create(
            kind=MechanismKind.MECHANISM.value,
            from_mode=MechanismMode.EXECUTING.value,
            to_mode=MechanismMode.SHADOW.value,
            at=NOW - timedelta(days=1),
            actor_kind=actor_kind.value,
            actor_name=name,
            reason="测试",
        )


# --------------------------------------------------------------------------- #
# 性质 3：起算日 = 第一条**成功**的资讯判定，且「尚未开始」必须说出来
# --------------------------------------------------------------------------- #


class TestTheStartingLine(_Fixture):
    def test_the_start_is_the_first_successful_judgement(self):
        """起算日取的是**第一条成功**的判定。它之前那条失败的不能把起算日拉回去。"""
        _judgement(TODAY - timedelta(days=9), status=news_verdict.STATUS_FAILED)
        _judgement(TODAY - timedelta(days=4))
        self.assertEqual(self.sweep().achieved.started_on, TODAY - timedelta(days=4))

    def test_a_quiet_round_still_starts_the_clock(self):
        """`quiet`（源全成功、0 条）是一次可信的结论——与采集窗口起点共用同一批判据。"""
        _judgement(TODAY - timedelta(days=2), status=news_verdict.STATUS_QUIET)
        self.assertEqual(self.sweep().achieved.started_on, TODAY - timedelta(days=2))

    def test_a_failed_judgement_alone_does_not_start_it(self):
        _judgement(TODAY - timedelta(days=6), status=news_verdict.STATUS_FAILED)
        self.assertIsNone(self.sweep().achieved.started_on)

    def test_shadow_records_alone_do_not_start_it(self):
        """**这一条是第 159 条的全部要点。** 判定链路先跑、资讯通道后接上时，那几天的记录
        交出的是一份纯量化的成绩单；拿它们起算，等于让准入体检去评估另一个函数。"""
        for days_ago in (9, 8, 7):
            _shadow(TODAY - timedelta(days=days_ago))
        self.assertIsNone(self.sweep().achieved.started_on)

    def test_not_started_is_not_a_zero(self):
        """「尚未开始」的四条出口都要说这句话，一条都不能渲染成看起来正常的 0。"""
        data = self.sweep()
        self.assertIsNone(data.rate)
        self.assertFalse(data.rate_met, "算不出来的频率不该被读成通过")
        self.assertFalse(data.days_met)
        self.assertFalse(data.expired)
        self.assertIn("Shadow 尚未开始", self.body())
        self.assertIn("Shadow 尚未开始", self.summary())
        self.assertIn("尚未开始", mechanism_switch.unmet_note(data) or "")

    def test_not_started_is_not_expired_even_after_the_cap(self):
        """没有任何资讯判定时，兜底的日子再多也不到期。

        否则一个从没接过资讯的部署会在第 60 天收到「可以出 Shadow 了」——而它一天都还没
        被校验过。到期说的是「条件可被校验」，没有起算日就没有可校验的东西。
        """
        for days_ago in range(CAP_DAYS + 5):
            _shadow(TODAY - timedelta(days=days_ago))
        self.assertFalse(self.sweep().expired)

    def test_the_page_names_how_many_days_have_a_conclusion(self):
        """自然日与「出了结论的天数」不是一回事：判定没跑的那几天照样占着分母（频率因此
        被稀释），而只有前者被印出来时，读的人会把稀释过的频率当成真实频率。"""
        self.start(days_ago=MIN_DAYS - 1)
        for days_ago in range(5):
            _shadow(TODAY - timedelta(days=days_ago))
        body = self.body()
        self.assertIn(f"已跑 {MIN_DAYS} 个自然日", body)
        self.assertIn("其中出了结论的 5 天", body)

    def test_no_parenthesis_when_every_day_has_a_conclusion(self):
        """差额为零时不多印一句：正常一轮每天都有结论，那半句会变成背景噪声。"""
        self.start(days_ago=MIN_DAYS - 1)
        for days_ago in range(MIN_DAYS):
            _shadow(TODAY - timedelta(days=days_ago))
        self.assertNotIn("其中出了结论的", self.body())


# --------------------------------------------------------------------------- #
# 性质 2：到期两条腿 + 兜底那句必须说出来
# --------------------------------------------------------------------------- #


class TestTheDeadline(_Fixture):
    def test_one_day_short_is_not_expired(self):
        self.start(days_ago=MIN_DAYS - 2)
        for i in range(MIN_WINDOWS):
            _event(f"窗口{i}", _at(TODAY - timedelta(days=3)))
        data = self.sweep()
        self.assertEqual(data.achieved.ran_days, MIN_DAYS - 1)
        self.assertFalse(data.days_met)
        self.assertFalse(data.expired)

    def test_both_legs_met_is_expired(self):
        self.start(days_ago=MIN_DAYS - 1)
        for i in range(MIN_WINDOWS):
            _event(f"窗口{i}", _at(TODAY - timedelta(days=3)))
        data = self.sweep()
        self.assertTrue(data.days_met)
        self.assertTrue(data.windows_met)
        self.assertTrue(data.expired)
        self.assertFalse(data.windows_short)
        # 摘要里**不加**「已到期」那半句：两个数（20/20、3/3）自己就把它说完了，多一句
        # 判断等于给同一件事造第二个说法。只有兜底那一种情形需要一句判断，因为它是唯一
        # 一个从数字里看不出来的事实。
        summary = self.summary()
        self.assertIn(f"已跑 {MIN_DAYS} 个自然日（门槛 {MIN_DAYS}）", summary)
        self.assertIn(f"期内高影响事件窗口 {MIN_WINDOWS}/{MIN_WINDOWS}", summary)
        self.assertNotIn("事件窗口数不足", summary)

    def test_the_cap_expires_without_enough_windows(self):
        """**兜底那一条。** 日子满 `expiry_cap_days`、窗口一个都没有，仍然到期——但这一句
        必须带着「事件窗口数不足」一起说，否则「已到期」会被读成「达标了」。"""
        self.start(days_ago=CAP_DAYS - 1)
        data = self.sweep()
        self.assertTrue(data.expired)
        self.assertFalse(data.windows_met)
        self.assertTrue(data.windows_short)
        self.assertIn("兜底", self.summary())
        self.assertIn("事件窗口数不足", self.summary())

    def test_the_day_before_the_cap_is_not_expired(self):
        """兜底那一侧的边界：`>= cap` 才到期。含与不含在页面上长得一样，只差一天。"""
        self.start(days_ago=CAP_DAYS - 2)
        self.assertEqual(self.sweep().achieved.ran_days, CAP_DAYS - 1)
        self.assertFalse(self.sweep().expired)

    def test_windows_short_is_false_before_the_cap(self):
        """窗口不够但没到兜底的日子：只是「还没到期」，不是「兜底到期」。"""
        self.start(days_ago=MIN_DAYS - 1)
        data = self.sweep()
        self.assertFalse(data.expired)
        self.assertFalse(data.windows_short)


# --------------------------------------------------------------------------- #
# 性质 1：三个触发源取并集，三类天数分行报
# --------------------------------------------------------------------------- #


class TestTheThreeSources(_Fixture):
    def test_deactivation_days(self):
        self.start(days_ago=MIN_DAYS - 1)
        _shadow(TODAY - timedelta(days=2), suggested=2)
        _shadow(TODAY - timedelta(days=1), suggested=1)
        data = self.sweep()
        self.assertEqual(
            (data.trigger_days, data.deactivation_days, data.high_vol_days, data.event_days),
            (2, 2, 0, 0),
        )

    def test_high_vol_days(self):
        """高波动档算触发，哪怕那天一条建议都没有：全场天天停开新仓本身就是「本来会施加
        动作」，而它不必经过适用性停用。"""
        self.start(days_ago=MIN_DAYS - 1)
        _shadow(TODAY - timedelta(days=2), regime=BaseRegime.HIGH_VOL)
        data = self.sweep()
        self.assertEqual(
            (data.trigger_days, data.deactivation_days, data.high_vol_days, data.event_days),
            (1, 0, 1, 0),
        )

    def test_event_window_days(self):
        self.start(days_ago=MIN_DAYS - 1)
        day = TODAY - timedelta(days=3)
        _event("FOMC", _at(day))
        data = self.sweep()
        self.assertEqual(
            (data.trigger_days, data.deactivation_days, data.high_vol_days, data.event_days),
            (1, 0, 0, 1),
        )

    def test_the_union_is_not_the_sum(self):
        """**这条是本文件的中心。** 同一天既被抬到高波动、又产生了适用性停用、又压在窗口
        里——那是一天，不是三天。求和会让频率虚高 3 倍。"""
        self.start(days_ago=MIN_DAYS - 1)
        day = TODAY - timedelta(days=3)
        _shadow(day, regime=BaseRegime.HIGH_VOL, suggested=1)
        _event("FOMC", _at(day))
        data = self.sweep()
        self.assertEqual(
            (data.trigger_days, data.deactivation_days, data.high_vol_days, data.event_days),
            (1, 1, 1, 1),
        )
        self.assertEqual(data.trigger_days / data.achieved.ran_days, 1 / MIN_DAYS)

    def test_days_outside_the_period_do_not_count(self):
        self.start(days_ago=MIN_DAYS - 1)
        _shadow(START - timedelta(days=1), regime=BaseRegime.HIGH_VOL, suggested=3)
        _shadow(TODAY + timedelta(days=1), regime=BaseRegime.HIGH_VOL, suggested=3)
        data = self.sweep()
        self.assertEqual(data.trigger_days, 0)
        self.assertEqual(data.achieved.ran_days, MIN_DAYS)

    def test_another_symbols_records_do_not_count(self):
        self.start(days_ago=MIN_DAYS - 1)
        _shadow(TODAY - timedelta(days=1), suggested=4, symbol="ETH/USDT")
        self.assertEqual(self.sweep().trigger_days, 0)

    def test_a_window_that_opened_before_the_start_still_covers_days_inside(self):
        """窗口起点落在期外、但窗口盖到期内的日子——**两个数因此会不一样，这是刻意的**。

        「期内开启的窗口数」（到期那条腿）按第 159 条只数起点落在期内的；而频率的分子问的
        是「这天本来会不会施加动作」，所以盖到的那一天要算。把两个数合成一个，两种问法就
        只剩一个答案——而它们在真实数据上确实不同（跨 08:00 的开窗就是这样）。
        """
        self.start(days_ago=MIN_DAYS - 1)
        _event(
            "跨过起算日",
            _at(START - timedelta(days=1), hour=23),
            _at(START, hour=12),
        )
        data = self.sweep()
        self.assertEqual(data.achieved.windows, 0, "起点在期外，不算「期内开启」")
        self.assertEqual(data.event_days, 1, "但它盖到了起算日这一天")

    def test_a_window_ending_at_midnight_does_not_claim_the_next_day(self):
        """窗口是**半开区间** `[halt_at, resume_at)`：压在 00:00 上的结束时刻不把第二天
        算进来。闭区间的话那个多出来的日子看起来完全正常，只会让频率高一点点。"""
        self.start(days_ago=MIN_DAYS - 1)
        day = TODAY - timedelta(days=4)
        _event(
            "收在零点",
            _at(day, hour=22),
            datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=business_tz()),
        )
        self.assertEqual(self.sweep().event_days, 1)

    def test_a_low_impact_or_cancelled_event_opens_no_window(self):
        """档位与状态都走 `MajorEvent.triggers_halt`（唯一实现），本模块不重写这个判据。"""
        self.start(days_ago=MIN_DAYS - 1)
        day = TODAY - timedelta(days=2)
        _event("中档", _at(day), impact=EventImpact.MEDIUM.value)
        _event("已取消", _at(day), status=EventStatus.CANCELLED.value)
        self.assertEqual(self.sweep().event_days, 0)


class TestTheRate(_Fixture):
    def test_the_threshold_is_strict(self):
        """`< max_trigger_rate` 是**严格**小于：正压着门槛那一天不算通过。

        门槛那一点是最容易写反的地方（`<=` 与 `<` 在 20 天里有 2 天还是 3 天的差别），
        而两种写法在页面上的样子一模一样。
        """
        self.start(days_ago=MIN_DAYS - 1)
        at_threshold = int(MIN_DAYS * MAX_RATE)
        for i in range(at_threshold):
            _shadow(TODAY - timedelta(days=i + 1), suggested=1)
        data = self.sweep()
        self.assertEqual(data.trigger_days, at_threshold)
        self.assertEqual(data.rate, MAX_RATE)
        self.assertFalse(data.rate_met)

    def test_one_day_under_the_threshold_passes(self):
        self.start(days_ago=MIN_DAYS - 1)
        _shadow(TODAY - timedelta(days=1), suggested=1)
        self.assertTrue(self.sweep().rate_met)

    def test_the_page_reports_the_three_sources_separately(self):
        """超标之后人第一个要问的是「是哪一类把它顶上去的」——所以三类必须分行。"""
        self.start(days_ago=MIN_DAYS - 1)
        day = TODAY - timedelta(days=2)
        _shadow(day, suggested=1)
        _shadow(TODAY - timedelta(days=1), regime=BaseRegime.HIGH_VOL)
        _event("FOMC", _at(TODAY))
        body = self.body()
        self.assertIn("适用性停用 1 天", body)
        self.assertIn("高波动档 1 天", body)
        self.assertIn("事件窗口 1 天", body)
        self.assertIn("去重后合计 3 天", body)


# --------------------------------------------------------------------------- #
# 上一次退回 Shadow：是人工还是自熔断（Q7）
# --------------------------------------------------------------------------- #


class TestTheBackRecord(_Fixture):
    def test_no_back_record(self):
        self.start(days_ago=MIN_DAYS - 1)
        self.assertIsNone(self.sweep().last_back)
        self.assertIn("没有", self.body())

    def test_only_the_mechanism_kind_counts(self):
        """事件熔断被关掉写的也是 `to_mode=shadow` 的行（日报第④段那条注释记着的坑）。

        少这一条限定，「关一下事件熔断」会在体检页上显示成「机制被退回过 Shadow」。
        """
        RegimeMechanismSwitch.objects.create(
            kind=MechanismKind.EVENT_BREAKER.value,
            from_mode=MechanismMode.EXECUTING.value,
            to_mode=MechanismMode.SHADOW.value,
            at=NOW - timedelta(hours=1),
            actor_kind=ActorKind.CHAT.value,
            actor_name="alice",
            reason="关掉事件熔断",
        )
        self.assertIsNone(self.sweep().last_back)

    def test_a_human_back_is_named_as_a_human(self):
        self.start(days_ago=MIN_DAYS - 1)
        self.back(actor_kind=ActorKind.CHAT, name="alice")
        body = self.body()
        self.assertIn("人工", body)
        self.assertIn("alice", body, "退回 Shadow 由谁做的必须写出来")

    def test_a_self_fused_back_asks_for_the_root_cause(self):
        """第 161 条：自熔断之后再回执行态要显式记录「这是自熔断后的恢复」并把根因摆出来。

        这条记录今天写不出来（自熔断的触发器按裁定不实现），但**读侧**必须已经在等它——
        等触发器落地时再补渲染，就会出现「第一次自熔断退回时页面什么都不说」。
        """
        self.start(days_ago=MIN_DAYS - 1)
        self.back(actor_kind=ActorKind.TASK, name="regime_self_fuse")
        body = self.body()
        self.assertIn("自熔断", body)
        self.assertIn("根因", body)
        self.assertNotIn("上一次退回 Shadow：人工", body)


# --------------------------------------------------------------------------- #
# 那一页必须一直说出口的三句话（本单元存在的理由）
# --------------------------------------------------------------------------- #


class TestTheHonestLines(_Fixture):
    def test_the_page_says_this_is_not_an_execution_switch(self):
        """`MechanismKind.MECHANISM` 今天**没有任何执行路径在读它**（`HALT_TRIGGER_SWITCH`
        只把另外两档映射到两个下游开关）。不说这句话，人敲完 `exit` 就以为机制上线了。"""
        self.start(days_ago=MIN_DAYS - 1)
        self.assertIn(NOT_A_SWITCH, self.body())

    def test_the_page_points_at_the_two_real_switches(self):
        self.start(days_ago=MIN_DAYS - 1)
        body = self.body()
        self.assertIn("/regime on", body)
        self.assertIn("/regime gate on", body)

    def test_the_agreement_rate_is_reported_as_undecidable(self):
        """成功标准①今天算不出来（真值没有存放它的地方）。**留白会被读成「这条大概没
        问题」**，所以必须写成一句明确的「无法判定」并按未达标计。"""
        self.start(days_ago=MIN_DAYS - 1)
        body = self.body()
        self.assertIn(TRUTH_ABSENT, body)
        self.assertIn("无法判定", body)
        self.assertIn("一致率无法判定", mechanism_switch.unmet_note(self.sweep()) or "")

    def test_the_self_fuse_clause_is_not_a_gate(self):
        """自熔断的频率条款要连续 3 个月才评估得出，而 Shadow 期的量级是几十个自然日——
        切换那一刻它必然无结论。这一页明写它不构成准入门槛，而不是算成一个空栏。"""
        self.start(days_ago=MIN_DAYS - 1)
        self.assertIn(SELF_FUSE_NOT_A_GATE, self.body())


# --------------------------------------------------------------------------- #
# 同一份快照：页面上的数与人点头之后进流水的数
# --------------------------------------------------------------------------- #


class TestTheSnapshot(_Fixture):
    def test_the_unmet_note_renders_the_snapshot_it_is_given(self):
        """`unmet_note` 收的是**已经算好的快照**，不重新取数。

        重新取数的话，两次查询之间只要判定跑了一轮，页面上写着「19 天」而流水里记着
        「20 天」——两个数都出自本模块，却互相矛盾。这里给它一个改过的快照，看数字跟不跟。
        """
        self.start(days_ago=MIN_DAYS - 1)
        data = self.sweep()
        forged = dataclasses.replace(
            data, achieved=dataclasses.replace(data.achieved, ran_days=3)
        )
        note = mechanism_switch.unmet_note(forged) or ""
        self.assertIn(f"自然日 3/{MIN_DAYS}", note)

    def test_each_gap_appears_only_when_it_is_a_gap(self):
        """每条缺口各说各的：到期那两条腿满足了就不再出现，而频率超标照样出现。

        这一条顺带钉住设计上的一个算术：三个窗口落在**三天**上就是 3/20 = 15%——
        `min_event_windows=3` 塞进 `min_natural_days=20` 里，**纯靠事件窗口触发的机制在
        20 天这个门槛上过不了 ②**。两个门槛是各自独立的数，这是设计给的，不是本模块的
        bug；页面把两个数分开印，所以这条算得出来、也看得见。
        """
        self.start(days_ago=MIN_DAYS - 1)
        for i in range(MIN_WINDOWS):
            # **一天一个窗口**：三个窗口压在同一天只算一个触发日（并集按天算，见
            # `test_the_union_is_not_the_sum`）。
            _event(f"窗口{i}", _at(TODAY - timedelta(days=3 + i)))
        data = self.sweep()
        self.assertTrue(data.expired)
        self.assertEqual(data.trigger_days, MIN_WINDOWS)
        self.assertFalse(data.rate_met, f"{MIN_WINDOWS} 个窗口 / {MIN_DAYS} 天压过门槛")

        note = mechanism_switch.unmet_note(data) or ""
        self.assertIn("触发频率", note)
        self.assertNotIn("自然日", note, "天数够了，不该再报它")
        self.assertNotIn("事件窗口", note, "窗口够了，不该再报它")
        self.assertIn("一致率无法判定", note)


# --------------------------------------------------------------------------- #
# 写方
# --------------------------------------------------------------------------- #


class _FlipFixture(_Fixture):
    def flip(
        self,
        mode: MechanismMode = MechanismMode.EXECUTING,
        *,
        actor_kind: ActorKind = ActorKind.CLI,
        actor_name: str = "ops",
        reason: str = "测试",
    ) -> RegimeMechanismSwitch | None:
        return mechanism_switch.flip_mechanism(
            mode,
            actor_kind=actor_kind,
            actor_name=actor_name,
            reason=reason,
            now=NOW,
        )


class TestTheFlip(_FlipFixture):
    def test_it_writes_the_kind_this_module_owns(self):
        """`kind` 写死成机制整体那一档：做成参数就是邀请下一个调用点顺手翻一个还没接线的
        开关（与 `breaker_switch` 同一条）。"""
        row = self.flip()
        self.assertEqual(row.kind, MechanismKind.MECHANISM.value)

    def test_it_records_the_real_from_mode(self):
        """`from_mode` 真读出来，不写死 Shadow：「从执行态关掉再打开」若记成「从 Shadow
        打开」，流水就再也回答不了「当时它到底开没开」。"""
        first = self.flip()
        self.assertEqual(first.from_mode, MechanismMode.SHADOW.value)
        second = self.flip(MechanismMode.SHADOW)
        self.assertEqual(second.from_mode, MechanismMode.EXECUTING.value)
        third = self.flip()
        self.assertEqual(third.from_mode, MechanismMode.SHADOW.value)

    def test_pressing_the_same_direction_twice_writes_nothing(self):
        """已经是那一档就返回 `None`：只增不改的表里多一条 `executing → executing` 会
        永久留在那里，而它读起来像一次真的切换。"""
        self.flip()
        self.assertIsNone(self.flip())
        self.assertEqual(RegimeMechanismSwitch.objects.count(), 1)

    def test_it_refuses_an_empty_reason(self):
        with self.assertRaises(ValueError):
            self.flip(reason="   ")

    def test_it_refuses_an_empty_actor(self):
        with self.assertRaises(ValueError):
            self.flip(actor_name="  ")

    def test_it_does_not_check_the_thresholds(self):
        """**页面不是守卫。** 未到期、频率超标、一致率算不出来——照样写得进去。

        设计里没有任何一条说「不达标就不许上线」，而硬拒绝会教人去找绕过它的路。这一页的
        职责是把没达标的项摆在人脸前（`unmet_note`），不是替人按着。
        """
        self.start(days_ago=MIN_DAYS - 1)
        for i in range(MIN_DAYS):
            _shadow(TODAY - timedelta(days=i), suggested=1)  # 频率 100%
        data = self.sweep()
        self.assertFalse(data.expired)
        self.assertFalse(data.rate_met)
        row = self.flip(reason=self.summary())
        self.assertIsNotNone(row)
        self.assertIn("触发频率", row.reason, "摘要里要带着那个超标了的数")

    def test_the_cap_sentence_lands_in_the_reason(self):
        """第 159 条要求「兜底到期时必须显式记录事件窗口数不足」，而这句话**由同一份快照
        渲染**——所以它不靠人记得写。这条用例走的就是命令那条路：摘要进 `reason`。"""
        self.start(days_ago=CAP_DAYS - 1)
        row = self.flip(reason=self.summary())
        self.assertIn("事件窗口数不足", row.reason)

    def test_the_reason_of_a_back_does_not_claim_it_undoes_anything(self):
        """退回 Shadow 一个停用也不解除——那句摘要是事后回答「当时退回去意味着什么」的
        唯一材料，写反了会让一次撤防看起来像一次全场停止。"""
        self.flip()  # 先出 Shadow，否则「退回」会因为已经是那一档而什么都不写
        briefing = mechanism_switch.page(now=NOW)
        row = self.flip(
            MechanismMode.SHADOW, reason=mechanism_switch.closing_summary(briefing.data)
        )
        self.assertIn("不解除任何", row.reason)
        self.assertIn("退回前是执行态", row.reason)


# --------------------------------------------------------------------------- #
# 日报第④段那一句（渲染在 report.py，判据在这里）
# --------------------------------------------------------------------------- #


class TestTheExpiryNotice(_Fixture):
    def test_nothing_before_the_deadline(self):
        """到期前天天报一句「还没到期」是二十天的噪声，会训练人跳过第④段。"""
        self.start(days_ago=MIN_DAYS - 1)
        self.assertIsNone(mechanism_switch.expiry_notice(now=NOW))

    def test_nothing_without_a_start(self):
        """一条判定都没有，或只有失败的判定 —— 都还没起算。（本仓今天就是这个形状。）"""
        self.assertIsNone(mechanism_switch.expiry_notice(now=NOW))
        _judgement(TODAY - timedelta(days=CAP_DAYS + 3), status=news_verdict.STATUS_FAILED)
        self.assertIsNone(mechanism_switch.expiry_notice(now=NOW))

    def test_it_speaks_after_the_cap(self):
        """兜底到期之后**每天**一直报到出 Shadow 为止：它要防的是「一个没人管的库让
        Shadow 无限期挂着、挂着的样子与正常跑着完全一样」，而它只有一个出口——人看见。"""
        self.start(days_ago=CAP_DAYS - 1)
        notice = mechanism_switch.expiry_notice(now=NOW) or ""
        self.assertIn("兜底", notice)
        self.assertIn("事件窗口数不足", notice)
        self.assertIn("/regime mech", notice, "要指出下一步该敲哪条命令")

    def test_it_speaks_after_both_legs(self):
        self.start(days_ago=MIN_DAYS - 1)
        for i in range(MIN_WINDOWS):
            _event(f"窗口{i}", _at(TODAY - timedelta(days=3)))
        notice = mechanism_switch.expiry_notice(now=NOW) or ""
        self.assertIn("已到期", notice)
        self.assertNotIn("事件窗口数不足", notice)
