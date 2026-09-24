"""启动会话那一刻的熔断回显（第②段单元 ②f 补的 CONTEXT.md 第 172 条）。

`test_halt.py` 钉的是「一组声明怎么求值成挡不挡得住」；这个文件钉的是**那次求值被搬到
用户面前的样子**：启动一个会话之后，屏幕上会不会多出一段「你启动了它，但它开不了新仓」。

它要防的是「回显看起来没问题、实际上没人会看到」这一类失效——每一条坏掉的表现都是
**屏幕上少了一段字，或者多了一段与己无关的字**，两者都不会红任何别的东西：

1. **不命中就一个字都不说。** 只跑 BTC 的会话不该在 SOL 的解锁窗口里收到一段熔断通告：
   每次都附一段无关的话，人很快就不读了，而真正该被读到的那次也一起被跳过。
2. **命中就必须说全。** 触发源、作用域、生效期、依据四件事缺一不可——只说「被拦住了」
   等于把用户丢回工单里。层标题取自 `HaltLayer.text`，所以这里断的是**同源**关系，
   不是一串抄下来的字。
3. **启动本身没被阻止。** 这段话只在响应里加一段，会话照常 `running`；「减仓照常」
   必须说出来，否则「停止」会被读成「什么都动不了」。
4. **开关仍然管着事件层。** 声明行在、开关关着 ⇒ 不出声（保命档没有开关，照常出声）。

时钟一律显式注入 `NOW`：`opened_at` 是闭区间、`expires_at` 是开区间，边界上的取等号
方向错了的表现是「早一刻出声」——只有钉住时钟才看得见。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_timezone

from django.test import TestCase

from apps.regime import halt, start_notice
from apps.regime.models import (
    ActorKind,
    HaltDeclaration,
    HaltTrigger,
    MechanismKind,
    MechanismMode,
    RegimeMechanismSwitch,
)

NOW = datetime(2026, 9, 23, 0, 0, tzinfo=dt_timezone.utc)
BTC = "BTC/USDT"
DOGE = "DOGE/USDT"
STRATEGY = "3f2a1c00-0000-0000-0000-000000000001"


def _declare(
    *,
    trigger: HaltTrigger = HaltTrigger.EVENT,
    scope: str = halt.global_scope(),
    label: str = "FOMC 议息",
    opened_at: datetime | None = None,
    expires_at: datetime | None = None,
    closed_at: datetime | None = None,
    reason: str = "高影响事件窗口 [halt_at, resume_at)",
) -> HaltDeclaration:
    return HaltDeclaration.objects.create(
        trigger=trigger.value,
        scope=scope,
        label=label,
        opened_at=opened_at or (NOW - timedelta(hours=2)),
        expires_at=expires_at,
        closed_at=closed_at,
        reason=reason,
        actor_kind=ActorKind.TASK.value,
        actor_name="regime.sync_halt_windows",
    )


def _switch(kind: MechanismKind, mode: MechanismMode) -> RegimeMechanismSwitch:
    return RegimeMechanismSwitch.objects.create(
        kind=kind.value,
        from_mode=MechanismMode.SHADOW.value,
        to_mode=mode.value,
        at=NOW - timedelta(days=1),
        actor_kind=ActorKind.CLI.value,
        actor_name="ops",
        reason="测试",
    )


def _notice(symbol: str = BTC, strategy_id: str | None = None) -> str:
    return start_notice.start_notice(symbol, strategy_id, now=NOW)


class _NoticeTestBase(TestCase):
    """默认把**事件熔断那个开关打开**：本文件多数用例问的是「命中的层被说出来了吗」，
    不是开关。

    不打开的话每条 `EVENT` 声明都会被静默过滤，用例测的其实成了「开关关着的时候什么都
    不说」——那样的断言在实现把过滤整个删掉时也照样通过。开关自己的行为集中在
    `TestTheSwitchStillGatesTheEventLayer`，那一类**不**调这个方法。
    """

    def setUp(self):
        super().setUp()
        _switch(MechanismKind.EVENT_BREAKER, MechanismMode.EXECUTING)


# --------------------------------------------------------------------------- #
# 裁剪：只报命中这个会话的层
# --------------------------------------------------------------------------- #


class TestOnlyWhatHitsThisSessionIsSpoken(_NoticeTestBase):
    def test_nothing_declared_says_nothing(self):
        """一个字都不说——不是「没有层在拦」这种话，是**空串**。

        空串是调用方能直接用来决定「加不加这段」的东西；换成一句「当前无熔断」，客户端
        就得去识别那句话才能裁剪，而那份识别很快就会与这里的措辞分家。
        """
        self.assertEqual(_notice(), "")

    def test_a_global_layer_is_reported(self):
        _declare(scope=halt.global_scope())
        self.assertIn("FOMC 议息", _notice())

    def test_a_symbol_layer_hits_only_its_own_symbol(self):
        """SOL 的解锁窗口不该在只跑 BTC 的会话上出声（第 172 条点名的那个例子）。"""
        _declare(scope=halt.symbol_scope(DOGE), label="DOGE 解锁")
        self.assertIn("DOGE 解锁", _notice(DOGE))
        self.assertEqual(_notice(BTC), "")

    def test_a_symbol_layer_is_never_mentioned_to_another_symbol(self):
        """裁剪不只是「不说话」——**别的品种的名字也不该出现**。

        写成「有 X 层在拦，但都不命中你这个品种」也是错的：那句话每次启动都会附上。
        """
        _declare(scope=halt.symbol_scope(DOGE), label="DOGE 解锁")
        self.assertNotIn("DOGE", _notice(BTC))

    def test_a_strategy_layer_needs_the_id(self):
        """策略档的三档判据（Q3 定死的那一条，见 `halt._matches`）。

        调用方没给 id 时表里的策略档行**不认它生效**；给了别的 id 也不认。
        """
        _declare(scope=halt.strategy_scope(STRATEGY), label="策略 A 停用")
        self.assertIn("策略 A 停用", _notice(BTC, STRATEGY))
        self.assertEqual(_notice(BTC, None), "")
        self.assertEqual(_notice(BTC, "另一个-strategy-id"), "")

    def test_a_released_layer_says_nothing(self):
        _declare(closed_at=NOW - timedelta(minutes=1))
        self.assertEqual(_notice(), "")

    def test_a_layer_that_has_not_started_says_nothing(self):
        """还没生效的行提前出声，会让人以为「现在已经被拦了」——而它连边界都没到。"""
        _declare(opened_at=NOW + timedelta(minutes=1))
        self.assertEqual(_notice(), "")

    def test_a_layer_that_expired_says_nothing(self):
        _declare(expires_at=NOW)
        self.assertEqual(_notice(), "")


# --------------------------------------------------------------------------- #
# 说了什么
# --------------------------------------------------------------------------- #


class TestWhatItSaysAboutTheLayer(_NoticeTestBase):
    def test_the_title_is_the_shared_layer_title(self):
        """层标题取自 `HaltLayer.text`——与 `query_halt`、与订单被拒的理由逐字同源。

        断言拿 `halt.layer_of(row).text` 去比而不是抄一遍标题串：被测的是**同源关系**，
        抄一遍的话，那句话改了实现、测试也跟着改，两边一起漂。
        """
        row = _declare(scope=halt.symbol_scope(BTC))
        self.assertIn(halt.layer_of(row).text, _notice())

    def test_the_trigger_source_is_spelled_out(self):
        """第 172 条要的是「回显是哪个触发源在拦」，所以触发源的人话必须在。"""
        _declare(trigger=HaltTrigger.EVENT)
        self.assertIn(HaltTrigger.EVENT.display, _notice())

    def test_two_layers_are_both_listed(self):
        """几层可以同时生效（CONTEXT.md:177）；只报一层会让人按那层去排查。"""
        _declare(scope=halt.global_scope(), label="全市场窗口")
        _declare(
            trigger=HaltTrigger.BLANKET,
            scope=halt.symbol_scope(BTC),
            label="高波动",
        )
        text = _notice()
        self.assertIn("全市场窗口", text)
        self.assertIn("高波动", text)
        self.assertIn("2 层在拦", text)

    def test_the_deadline_uses_both_timezones(self):
        """误读时区不是「显示错了」，是**把熔断放在了错误的一天**（同日历口径）。"""
        _declare(expires_at=NOW + timedelta(hours=3))
        self.assertIn("北京时间", _notice())
        self.assertIn("UTC", _notice())

    def test_an_open_ended_deadline_says_undecided(self):
        """`expires_at` 为空 = **不定**（保命档随阶段起落），不是「永久」。"""
        _declare(expires_at=None)
        self.assertIn("未定", _notice())

    def test_the_reason_the_writer_recorded_is_shown(self):
        _declare(reason="FOMC 议息前 2 小时，窗口 [halt_at, resume_at)")
        self.assertIn("FOMC 议息前 2 小时", _notice())


class TestTheTwoThingsThatMustAlwaysBeSaid(_NoticeTestBase):
    def test_it_does_not_read_like_a_refusal(self):
        """这段话跟着一次 200 的响应回来，所以它必须自己说清「启动没被阻止」。

        写成一句裸的「开仓被拦住」，人第一反应是「那我刚才那一下白按了」——而存量仓位的
        止损保护恰恰是靠这一步上线的（第 172 条：不阻止启动）。
        """
        _declare()
        text = _notice()
        self.assertIn("会话已启动", text)
        self.assertIn("开仓", text)

    def test_it_says_reduce_only_still_passes(self):
        """「停止」最容易被读成「什么都动不了」，而减仓是熔断时唯一想让它动的事。"""
        _declare()
        self.assertIn("只减不增", _notice())


# --------------------------------------------------------------------------- #
# 开关仍然管着事件层（保命档没有开关——「高波动算生效」）
# --------------------------------------------------------------------------- #


class TestTheSwitchStillGatesTheEventLayer(TestCase):
    """**刻意不打开开关**：这一组问的就是开关关着的时候出不出声。"""

    def test_the_event_layer_is_silent_while_its_switch_is_off(self):
        """声明行在、开关关着 ⇒ 不出声。

        「声明说了想拦」与「这个源被启用了」是两件事（`HaltVerdict` 的 docstring）；
        回显报的是**真的在拦**的层，而那正是下单通路会拦的层——把留着的行也报出来，
        用户看到的会比实际发生的更严，且他会去排查一个不存在的拦截。
        """
        _declare()
        self.assertEqual(_notice(), "")

    def test_the_event_layer_speaks_once_the_switch_is_on(self):
        row = _declare()
        self.assertEqual(_notice(), "")
        _switch(MechanismKind.EVENT_BREAKER, MechanismMode.EXECUTING)
        self.assertIn(halt.layer_of(row).text, _notice())

    def test_the_blanket_layer_has_no_switch(self):
        """保命档映射到 `None`（`HALT_TRIGGER_SWITCH`），所以开关全关时它照常出声。

        这条是那处刻意不对称在**用户侧**的守卫：保命档被某个「默认关」的开关压住的表现
        就是这里变成空串——而系统别处看起来一切正常。
        """
        _declare(trigger=HaltTrigger.BLANKET, label="高波动")
        self.assertIn("高波动", _notice())


# --------------------------------------------------------------------------- #
# 与下单通路同源
# --------------------------------------------------------------------------- #


class TestItAgreesWithTheOrderPath(_NoticeTestBase):
    def test_the_notice_is_empty_exactly_when_the_order_path_would_allow(self):
        """**端到端的那一条**：回显与 `pre_trade_check` 的判据必须是同一个。

        回显说「被拦住了」而下单通路照放行（或反过来）都不会红任何别的东西——用户只会
        在某天发现「它说会拦，但单还是出去了」。所以这里拿 `block_reason`（订单拒绝理由
        的同一个入口）逐组比对，而不是再写一遍预期。
        """
        _declare(scope=halt.global_scope())
        _declare(scope=halt.symbol_scope(BTC), label="BTC 窗口")
        _declare(scope=halt.strategy_scope(STRATEGY), label="策略 A 停用")
        _declare(scope=halt.symbol_scope(DOGE), label="DOGE 解锁")

        cases = [(BTC, None), (BTC, STRATEGY), (DOGE, None), ("SOL/USDT", STRATEGY)]
        for symbol, strategy_id in cases:
            with self.subTest(symbol=symbol, strategy_id=strategy_id):
                blocked = bool(halt.block_reason(symbol, strategy_id, now=NOW))
                self.assertEqual(blocked, _notice(symbol, strategy_id) != "")


# --------------------------------------------------------------------------- #
# 它是只读的
# --------------------------------------------------------------------------- #


class TestItIsReadOnly(_NoticeTestBase):
    def test_it_writes_nothing(self):
        """回显是**读**：它不该顺手补一条声明、也不该改开关。

        顺手写点什么的表现是「每次启动都往流水里多一行」，而那张表只增不改。
        """
        _declare()
        before_declarations = HaltDeclaration.objects.count()
        before_switches = RegimeMechanismSwitch.objects.count()

        _notice()

        self.assertEqual(HaltDeclaration.objects.count(), before_declarations)
        self.assertEqual(RegimeMechanismSwitch.objects.count(), before_switches)
