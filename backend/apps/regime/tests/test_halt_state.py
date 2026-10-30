"""机制读面的第四只读端点：`GET api/regime/halt/`——此刻真的在拦的停止层。

与 `test_views.py` 的分工：那个文件钉的是「池化表怎么被读出去」，本文件钉的是「停止判定
怎么被读出去」。求值本身在 `test_halt.py` 里已经钉过，这里一条都不重述——本文件只问
一件事：**转述忠不忠实**。

四条性质，每一条坏了都不报警、只出错：

1. **匿名 401，登录用户都能读**——与 `overview` 同一条权限口径（读对所有登录用户开放）。
2. **开关关闭的触发源不出现在响应里**。这是本端点与 `live_declarations` 的唯一区别，也是
   它存在的理由：横幅的语义是「此刻挡不挡得住」，不是「库里有什么行」。读成后者的话，
   一条触发源已经关掉的声明会被报成「在拦」——而那正是本仓反复在防的「同一件事两个说法」。
3. **保命档与手动熔断永远出现**（`HALT_TRIGGER_SWITCH` 里它们是 `None`）。这条与性质 2
   是一对：两个方向都钉住，才挡得住将来「顺手给保命档也加个开关」。
4. **层是一个集合**：多层同时生效时**全部**列出。只报第一层会让人按报出来那层去排查，
   而解除它之后单还是下不出去（`HaltVerdict.reason` 的 docstring 写过这件事）。

外加一条形状纪律：`halt_layer_payload` **不发** `trigger` / `scope` / `label` 的零件——
`text` 已经是「行 → 层」的唯一换算口，发出零件等于给前端留一条自己再拼一遍的路。所以
断言的是**键集**（多一个键就是多一条分叉的路）以及 `text` 与 `halt.HaltLayer.text` 同源。

**时钟**：视图不安时钟（它调的是不注入 `now` 的默认路径），所以本文件的时刻一律**相对
真实现在**造——把夹具钉在某个固定日期上的话，「还没生效」那一类用例会随着真实时间越过
那个日期而静默变成「已生效」，用例还是绿的。
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils.timezone import now as tz_now
from rest_framework.test import APIClient

from apps.regime import halt
from apps.regime.models import HaltTrigger, MechanismKind, MechanismMode
from apps.regime.tests.test_halt import _declare, _switch

HALT = "/api/regime/halt/"

#: 一层在响应里的**全部**键。多一个就是多一条「前端自己拼」的路（模块 docstring）。
LAYER_KEYS = {"text", "reason", "opened_at", "expires_at"}

#: 相对真实现在的基准时刻。见模块 docstring 的「时钟」一节。
AT = tz_now()


class _Fixture(TestCase):
    def get(self):
        self.client.force_authenticate(user=self.user)
        return self.client.get(HALT)

    def setUp(self):
        # `AUTH_USER_MODEL = "authentication.User"`：email 是必填的第一个位置参数
        # （与 `test_gate_run._Fixture` 同一写法）。
        self.user = get_user_model().objects.create_user(
            email="halt_state@test.local",
            username="halt_state",
            password="pw12345",
        )
        self.client = APIClient()

    def layers(self) -> list[dict]:
        response = self.get()
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["halt_layers"]

    def body(self) -> dict:
        response = self.get()
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def omitted(self) -> int:
        return self.body()["omitted_strategy_layers"]


# --------------------------------------------------------------------------- #
# 性质 1：认证
# --------------------------------------------------------------------------- #


class TestAuth(_Fixture):
    def test_anonymous_is_rejected(self):
        response = APIClient().get(HALT)

        self.assertEqual(response.status_code, 401, response.content)

    def test_the_read_surface_is_not_cacheable(self):
        """「此刻是什么状态」的读面都 `no-store`（`views._no_store`）：缓存一次就是缓存
        一个可能已经解除的停止判定。"""
        self.assertEqual(self.get()["Cache-Control"], "no-store")


# --------------------------------------------------------------------------- #
# 性质 2 / 3：开关与声明是两件事
# --------------------------------------------------------------------------- #


class TestSwitchDecides(_Fixture):
    def test_a_disabled_trigger_stays_out_of_the_read_surface(self):
        """事件熔断的声明在，但开关没打开（出厂就是 Shadow）→ 它不作数，不该出现。"""
        _declare(trigger=HaltTrigger.EVENT, opened_at=AT - timedelta(hours=2))

        self.assertEqual(self.layers(), [])

    def test_the_same_declaration_appears_once_the_switch_is_open(self):
        _declare(trigger=HaltTrigger.EVENT, opened_at=AT - timedelta(hours=2))
        _switch(MechanismKind.EVENT_BREAKER, MechanismMode.EXECUTING)

        self.assertEqual(len(self.layers()), 1)

    def test_the_blanket_layer_has_no_switch_and_always_counts(self):
        """保命档：`HALT_TRIGGER_SWITCH[BLANKET] = None`——「高波动算生效，它不由任何人
        确认」。所有开关都留在 Shadow，它照样出现。"""
        _declare(
            trigger=HaltTrigger.BLANKET,
            label="高波动",
            opened_at=AT - timedelta(hours=2),
        )

        layers = self.layers()

        self.assertEqual(len(layers), 1)
        self.assertIn("高波动", layers[0]["text"])

    def test_the_manual_layer_has_no_switch_either(self):
        _declare(
            trigger=HaltTrigger.MANUAL,
            label="手动熔断",
            opened_at=AT - timedelta(hours=2),
        )

        self.assertEqual(len(self.layers()), 1)


# --------------------------------------------------------------------------- #
# 生效期与解除：行留在表里，作不作数是求值的结果
# --------------------------------------------------------------------------- #


class TestLiveness(_Fixture):
    def test_a_closed_declaration_does_not_appear(self):
        _declare(
            trigger=HaltTrigger.BLANKET,
            opened_at=AT - timedelta(hours=3),
            closed_at=AT - timedelta(hours=1),
        )

        self.assertEqual(self.layers(), [])

    def test_a_declaration_that_has_not_opened_yet_does_not_appear(self):
        _declare(
            trigger=HaltTrigger.BLANKET,
            opened_at=AT + timedelta(hours=1),
        )

        self.assertEqual(self.layers(), [])

    def test_an_expired_declaration_does_not_appear(self):
        _declare(
            trigger=HaltTrigger.BLANKET,
            opened_at=AT - timedelta(hours=3),
            expires_at=AT - timedelta(minutes=1),
        )

        self.assertEqual(self.layers(), [])

    def test_a_window_with_no_end_stays(self):
        """`expires_at=None` 是「不定何时解除」，不是「缺数据」——它必须出现，且响应里
        那个键保持 `null`（不是空串）。"""
        _declare(
            trigger=HaltTrigger.BLANKET,
            opened_at=AT - timedelta(hours=2),
            expires_at=None,
        )

        layers = self.layers()

        self.assertEqual(len(layers), 1)
        self.assertIsNone(layers[0]["expires_at"])


# --------------------------------------------------------------------------- #
# 性质 4 与形状纪律
# --------------------------------------------------------------------------- #


class TestLayerShape(_Fixture):
    def test_all_layers_are_listed_not_just_the_first(self):
        """层是集合：两层同时生效时两层都要在（`HaltVerdict.reason` 的 docstring）。"""
        _declare(
            trigger=HaltTrigger.BLANKET,
            label="保命档那层",
            opened_at=AT - timedelta(hours=3),
        )
        _declare(
            trigger=HaltTrigger.MANUAL,
            label="手动那层",
            scope=halt.symbol_scope("DOGE/USDT"),
            opened_at=AT - timedelta(hours=2),
        )

        layers = self.layers()

        self.assertEqual(len(layers), 2)
        texts = "｜".join(layer["text"] for layer in layers)
        self.assertIn("保命档那层", texts)
        self.assertIn("手动那层", texts)

    def test_a_layer_carries_only_the_transcribed_keys(self):
        """不发 `trigger` / `scope` / `label` 的零件：`text` 已是唯一换算口，发出零件就是
        给前端留一条自己再拼一遍的路（`serializers.halt_layer_payload`）。"""
        _declare(trigger=HaltTrigger.BLANKET, opened_at=AT - timedelta(hours=2))

        layer = self.layers()[0]

        self.assertEqual(set(layer), LAYER_KEYS)

    def test_the_text_is_the_backend_sentence_not_a_recomposition(self):
        """`text` 与 `halt.HaltLayer.text` **逐字**相同，且句子里带作用域——只说触发源的话
        「拦全场」与「只拦某个品种」在页面上长得一样（`HaltLayer.text` 的 docstring）。"""
        row = _declare(
            trigger=HaltTrigger.MANUAL,
            label="手动熔断",
            scope=halt.symbol_scope("DOGE/USDT"),
            opened_at=AT - timedelta(hours=2),
        )

        layer = self.layers()[0]

        self.assertEqual(layer["text"], halt.layer_of(row).text)
        self.assertIn(halt.scope_display(row.scope), layer["text"])

    def test_opened_at_is_iso_serialized(self):
        _declare(trigger=HaltTrigger.BLANKET, opened_at=AT - timedelta(hours=2))

        layer = self.layers()[0]

        # `iso()` 的输出（不是模型列的 `str()`）：能被解析回一个带时区的时刻。
        self.assertTrue(layer["opened_at"].startswith("20"), layer["opened_at"])
        self.assertEqual(
            layer["opened_at"],
            halt.layer_of(halt.blocking_declarations()[0]).opened_at.isoformat(),
        )


# --------------------------------------------------------------------------- #
# 性质 5：惰性策略档不在横幅上占位（2026-10-30）
# --------------------------------------------------------------------------- #


class TestInertStrategyLayers(_Fixture):
    """「策略没走到模拟实盘/实盘，就不在顶部判定状态里展示」（用户原话）。

    **这一条不是放宽判定**，是去掉一个恒为假的提示。机制按**全部被管策略**产出策略档
    声明（池化与暂停两路都是），于是从没部署过的策略也在表里占一条；而那一层对任何一张
    单都是惰性的——`halt._matches` 的策略档判据要求调用方给的 `strategy_id` 等于这一行，
    而它只能由一条会话换出来（`RiskGuard._strategy_of_session`）。订单通路仍走
    `halt_layers`，一个字都没变；收窄只发生在这个读面。

    五件事各钉一条，每一件坏了都会让横幅说一句它没资格说的话：

    1. **没会话的策略档不占位**，但**条数要报**（`omitted_strategy_layers`）——
       静默过滤会让「我明明停过它、界面怎么不说」在界面上查无此事。
    2. **模拟盘会话也算「在跑」**：`active_session_strategy_ids` 不传 `modes` 时两种模式
       都算。这里若退化成只认 `live`，模拟盘上跑着的策略会在横幅上被判成「没在跑」，
       方向恰好错向危险的那一侧。
    3. **手动熔断永不滤**：它是人当场按下的停止，而横幅是它唯一的可见出口。
    4. **global / symbol 档永不滤**：它们拦的是全场的答案与账户上真实存在的单，与有没有
       会话无关。
    5. **`REGIME_SUSPENSION` 与 `DEACTIVATION` 同等待遇**：两路都按被管策略产出，
       只滤一路的话另一路会把同一条策略又摆回来。
    """

    def setUp(self):
        super().setUp()
        from apps.trading.models import Strategy

        # 两个开关都要开：`DEACTIVATION` 与 `REGIME_SUSPENSION` 共用 `REGIME_GATE`。
        _switch(MechanismKind.REGIME_GATE, MechanismMode.EXECUTING)
        self.strategy = Strategy.objects.create(
            name="BoxBreakout", code_path="/t/box.py", git_commit_hash="abc1234"
        )

    def session(self, *, mode: str = "paper", status: str = "running"):
        from apps.trading.models import LiveSession

        return LiveSession.objects.create(
            user=self.user,
            strategy=self.strategy,
            symbol="BTC/USDT",
            mode=mode,
            status=status,
            initial_capital="10000.00",
        )

    def declare_strategy_layer(
        self, *, trigger: HaltTrigger = HaltTrigger.DEACTIVATION
    ) -> None:
        _declare(
            trigger=trigger,
            scope=halt.strategy_scope(self.strategy.id),
            label="策略停用 BoxBreakout",
            opened_at=AT - timedelta(hours=2),
        )

    # ── 1. 没在跑 ⇒ 不占位，但条数照样报 ────────────────────────────────── #

    def test_a_strategy_layer_with_no_session_is_not_displayed(self):
        self.declare_strategy_layer()

        self.assertEqual(self.layers(), [])
        self.assertEqual(self.omitted(), 1)

    def test_the_omitted_count_is_zero_when_nothing_is_hidden(self):
        """`omitted_strategy_layers` 恒在场（0 也是答案）：缺键会让前端把「没有隐藏」
        与「这个后端还没升级」混起来，而那两件事看起来一模一样。"""
        _declare(trigger=HaltTrigger.BLANKET, opened_at=AT - timedelta(hours=2))

        body = self.body()

        self.assertIn("omitted_strategy_layers", body)
        self.assertEqual(body["omitted_strategy_layers"], 0)
        self.assertEqual(len(body["halt_layers"]), 1)

    # ── 2. 模拟盘与实盘都算「在跑」 ──────────────────────────────────────── #

    def test_a_paper_session_puts_the_layer_back_on_the_banner(self):
        """用户原话里的「模拟实盘」= `LiveSession.mode == "paper"`。它必须算在跑。"""
        self.session(mode="paper", status="running")
        self.declare_strategy_layer()

        self.assertEqual(len(self.layers()), 1)
        self.assertEqual(self.omitted(), 0)

    def test_a_live_session_puts_the_layer_back_on_the_banner(self):
        self.session(mode="live", status="running")
        self.declare_strategy_layer()

        self.assertEqual(len(self.layers()), 1)
        self.assertEqual(self.omitted(), 0)

    def test_a_pending_session_still_counts(self):
        """`pending` 是「待启动但会启动」：它一起来就要吃这条声明，横幅提前一天说出来
        比启动那一刻才冒出来好（`LiveSession.ACTIVE_STATUSES` 的口径）。"""
        self.session(mode="paper", status="pending")
        self.declare_strategy_layer()

        self.assertEqual(len(self.layers()), 1)
        self.assertEqual(self.omitted(), 0)

    def test_a_stopped_session_does_not_count(self):
        """`stopped` 的会话不会再下单，把它算进去等于保留了用户要消掉的那类噪声。"""
        self.session(mode="paper", status="stopped")
        self.declare_strategy_layer()

        self.assertEqual(self.layers(), [])
        self.assertEqual(self.omitted(), 1)

    # ── 3. 手动熔断永不滤 ───────────────────────────────────────────────── #

    def test_a_manual_strategy_layer_survives_without_any_session(self):
        """`manage.py manual_halt --strategy` 写的也是 `strategy:` 作用域，但那是人当场
        按下的停止。滤掉它等于让一次人工停止在界面上静默消失。"""
        self.declare_strategy_layer(trigger=HaltTrigger.MANUAL)

        self.assertEqual(len(self.layers()), 1)
        self.assertEqual(self.omitted(), 0)

    # ── 4. global / symbol 档永不滤 ──────────────────────────────────────── #

    def test_global_and_symbol_layers_survive_without_any_session(self):
        _declare(
            trigger=HaltTrigger.BLANKET,
            label="高波动",
            opened_at=AT - timedelta(hours=3),
        )
        _declare(
            trigger=HaltTrigger.MANUAL,
            label="手动熔断",
            scope=halt.symbol_scope("BTC/USDT"),
            opened_at=AT - timedelta(hours=2),
        )

        self.assertEqual(len(self.layers()), 2)
        self.assertEqual(self.omitted(), 0)

    # ── 5. 两路策略档同等待遇 ───────────────────────────────────────────── #

    def test_the_suspension_trigger_is_filtered_the_same_way(self):
        self.declare_strategy_layer(trigger=HaltTrigger.REGIME_SUSPENSION)

        self.assertEqual(self.layers(), [])
        self.assertEqual(self.omitted(), 1)

    def test_only_the_inert_one_is_hidden_when_both_kinds_are_present(self):
        """保命档 + 一条惰性策略档：横幅上只留保命档，条数如实报 1——这正是
        「不许只报一层就完事」与「惰性层不占位」两条纪律的交点。"""
        _declare(trigger=HaltTrigger.BLANKET, opened_at=AT - timedelta(hours=3))
        self.declare_strategy_layer()

        body = self.body()

        self.assertEqual(len(body["halt_layers"]), 1)
        self.assertIn("高波动", body["halt_layers"][0]["text"])
        self.assertEqual(body["omitted_strategy_layers"], 1)
