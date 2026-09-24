"""策略档的停止声明在**下单通路**上真的生效（第③段的身份接线）。

`test_guard_halt.py` 钉的是第 0 步的**形状**（排在判空之前、减仓单跳过、查不出来
fail-closed、拒绝必须走通知），它把 `_halt_block_reason` 整个桩掉——而那道缝正是本文件
要穿过去的。停止判定的策略档钉在 `Strategy.id` 上（`halt.strategy_scope`），一张单却只
答得出会话 id（`OrderRequest.live_session_id`），「会话 → 策略」这一跳落在
`RiskGuard._strategy_of_session` 里。这一跳断掉的表现是：

    日报上写着某策略「已停用」，它的单照常出去；而启动会话那一段回显（`start_notice`，
    它直接拿得到 `LiveSession.strategy_id`）还在说「它开不了新仓」。

两处说法相反，且**不会红任何别的东西**。所以这条链必须从真实的库、经真实的
`halt.halt_layers` 走一遍：桩掉任何一段，本文件就退化成了它要防的那件事。

四条：

1. **命中自己**：会话所属策略的声明挡住这张单。
2. **不命中别人**：同一条声明对另一个策略的会话放行（策略档就该只拦那一个策略）。
3. **认不出会话时不按全市场算**：``live_session_id`` 为空或查不到时退化成「停用没生效」，
   而不是「全场莫名停摆」（`_strategy_of_session` 的取向）。
4. **gate 关着时这一档不生效**：开关与声明是两件事，都必须为真（`halt.switch_open`）。

`transaction=True` 不是省事：`pre_trade_check` 的取数经 `db_async` 走到**另一个线程**上，
非事务性的 `django_db` 把数据关在未提交的事务里，那个线程看不见，于是四条全会「通过」
（什么声明都不生效 ⇒ 一致放行）。**那正是本文件要防的假绿灯**，所以宁可付一次 flush 的
代价。注意它也因此不能用 `django.test.TestCase`——后者与 `transaction=True` 互斥。

时钟用墙上时间而不是固定 `NOW`：判定入口（`halt_layers`）不注入时钟，窗口必须相对真实
的「现在」落在过去。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.regime import halt
from apps.regime.models import (
    ActorKind,
    HaltDeclaration,
    HaltTrigger,
    MechanismKind,
    MechanismMode,
    RegimeMechanismSwitch,
)
from apps.riskguard.guard import RiskGuard
from apps.trading.adapters.base import OrderRequest

if TYPE_CHECKING:
    from apps.trading.models import LiveSession, Strategy

pytestmark = pytest.mark.django_db(transaction=True)

SYMBOL = "BTC/USDT"
LABEL = "策略停用决策：趋势跟踪（下行趋势表现不佳）"


def _strategy(name: str) -> "Strategy":
    from apps.trading.models import Strategy

    return Strategy.objects.create(name=name, code_path=f"strategies/{name}.py")


def _session(strategy: "Strategy") -> "LiveSession":
    from apps.trading.models import LiveSession

    name = f"u-{uuid.uuid4().hex[:8]}"
    return LiveSession.objects.create(
        user=get_user_model().objects.create_user(
            username=name, email=f"{name}@example.com", password="pw12345"
        ),
        strategy=strategy,
        symbol=SYMBOL,
        mode="paper",
        status="running",
        initial_capital=Decimal("1000"),
    )


def _declare(strategy: "Strategy", *, label: str = LABEL) -> HaltDeclaration:
    """该策略的停用声明。``expires_at=None`` = 不定（策略停用没有预先知道的截止时刻）。"""
    return HaltDeclaration.objects.create(
        trigger=HaltTrigger.DEACTIVATION.value,
        scope=halt.strategy_scope(strategy.id),
        label=label,
        opened_at=timezone.now() - timedelta(hours=2),
        expires_at=None,
        reason="行情阶段 gate：当前阶段不适配（测试）",
        actor_kind=ActorKind.TASK.value,
        actor_name="regime.sync_gate",
    )


def _gate_on() -> RegimeMechanismSwitch:
    """打开行情阶段 gate 的开关。``HALT_TRIGGER_SWITCH[DEACTIVATION]`` 指向它。"""
    return RegimeMechanismSwitch.objects.create(
        kind=MechanismKind.REGIME_GATE.value,
        from_mode=MechanismMode.SHADOW.value,
        to_mode=MechanismMode.EXECUTING.value,
        at=timezone.now() - timedelta(days=1),
        actor_kind=ActorKind.CLI.value,
        actor_name="ops",
        reason="测试",
    )


def _request(*, live_session_id: str | None) -> OrderRequest:
    return OrderRequest(
        exchange="binance",
        symbol=SYMBOL,
        order_type="market",
        side="buy",
        quantity=Decimal("0.001"),
        price=None,
        live_session_id=live_session_id,
    )


def _check(request: OrderRequest) -> tuple[bool, str]:
    """走**真实**的 `pre_trade_check`。

    ``user_id=None`` 是刻意的：第 0 步之后那五步问的都是「这个人还能不能下单」，本文件
    只关心第 0 步，而判空会在第 0 步之后立刻返回——于是不必去桩第 2~4 步，也不必让
    ``_reject`` 去发通知。
    """
    return asyncio.run(RiskGuard(mode="trading").pre_trade_check(request, None))


class TestAStrategyScopedDeclarationBlocksItsOwnOrder:
    def test_the_session_strategy_is_the_one_the_declaration_names(self):
        _gate_on()
        strategy = _strategy("donchian_atr_trend")
        session = _session(strategy)
        _declare(strategy)

        approved, reason = _check(_request(live_session_id=str(session.id)))

        assert approved is False
        assert LABEL in reason
        # 作用域必须写进理由：「拦全场」与「只拦某个策略」在排查时是完全不同的两件事。
        assert f"策略 {strategy.id}" in reason


class TestItDoesNotLeakToOtherScopes:
    def test_another_strategys_session_is_not_blocked(self):
        """同一条声明对别人的单必须放行——策略档拦错了人是「全场莫名停摆」的一种。"""
        _gate_on()
        halted = _strategy("donchian_atr_trend")
        other = _strategy("mean_reversion")
        _declare(halted)

        approved, reason = _check(_request(live_session_id=str(_session(other).id)))

        assert (approved, reason) == (True, "OK")

    def test_an_unresolvable_session_is_not_read_as_global(self):
        """认不出会话 ⇒ 退化成「停用没生效」，**不按全市场处理**。

        ``None`` 与「查不到的 id」走的是 `_strategy_of_session` 里两条不同的返回路径，
        而它们的正确取向是同一个（会话行不见了，该管的是那条会话，不是把这一单拦下来），
        所以钉在同一个用例里。
        """
        _gate_on()
        strategy = _strategy("donchian_atr_trend")
        _declare(strategy)

        for live_session_id in (None, str(uuid.uuid4())):
            approved, reason = _check(_request(live_session_id=live_session_id))
            assert (approved, reason) == (True, "OK"), live_session_id


class TestTheGateSwitchStillGatesThisTier:
    def test_with_the_switch_off_the_declaration_is_inert(self):
        """声明在、开关关着 ⇒ 不拦。**第③段之前 gate 恒为关**，所以这条是「机制没出
        Shadow 时它一个字都不许动」的那条线。

        行**留着**（关掉开关不抹记录，`HaltDeclaration` 的 docstring），只是不作数。
        """
        strategy = _strategy("donchian_atr_trend")
        session = _session(strategy)
        _declare(strategy)

        assert RegimeMechanismSwitch.current(MechanismKind.REGIME_GATE) is (
            MechanismMode.SHADOW
        )
        approved, reason = _check(_request(live_session_id=str(session.id)))

        assert (approved, reason) == (True, "OK")
        assert HaltDeclaration.objects.filter(closed_at__isnull=True).count() == 1
