"""mode 与 ExchangeAccount.testnet 的自洽不变式（第①段强制前置）。

不变式：`paper ⇒ testnet=True`、`live ⇒ testnet=False`，不一致则**拒绝启动**。

它要修的是「字段名承诺隔离、实现不隔离」这一类**静默**失效：用户以为在演练、
实际在动钱。注意这不是新增能力，只是把静默错配变成一次可见的失败——所以
`promote` 出来的会话（mode=live + 从 paper 会话继承来的 testnet 账户）在这里
会被如实拒绝，那正是这条不变式存在的意义。
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from rest_framework_simplejwt.tokens import AccessToken

from apps.agent.frame_manager import FrameManager
from apps.backtest.models import BacktestResult
from apps.exchange.models import ExchangeAccount
from apps.trading.models import LiveSession, Strategy

User = get_user_model()


def _user(username: str):
    return User.objects.create_user(
        username=username, email=f"{username}@example.com", password="pw12345"
    )


def _account(label: str, testnet: bool):
    return ExchangeAccount.objects.create(
        exchange="binance",
        label=label,
        api_key_enc=b"enc",
        api_secret_enc=b"enc",
        testnet=testnet,
    )


def _session(user, mode: str, testnet: bool, status: str = "pending"):
    strategy = Strategy.objects.create(
        name=f"s-{mode}-{testnet}-{status}", code_path="strategies/s.py"
    )
    backtest = BacktestResult.objects.create(
        strategy=strategy,
        user=user,
        symbol="BTC/USDT",
        timeframe="4h",
        start_date=datetime.date(2026, 1, 1),
        end_date=datetime.date(2026, 2, 1),
        initial_capital=Decimal("1000"),
        final_capital=Decimal("1100"),
        total_return_pct=10.0,
        review_status="approved",
    )
    return LiveSession.objects.create(
        user=user,
        backtest_result=backtest,
        strategy=strategy,
        symbol="BTC/USDT",
        mode=mode,
        status=status,
        exchange_account=_account(f"{mode}-{testnet}", testnet),
        initial_capital=Decimal("1000"),
        current_equity=Decimal("1000"),
        config={},
    )


def _idle_frame_manager():
    """框架全部打桩：本文件只验「准入」这一步，不验框架起没起来。"""
    fm = MagicMock()
    fm.start_trading_frame = AsyncMock()
    fm.start_strategy_runner = AsyncMock()
    fm.stop_strategy_runner = AsyncMock()
    fm.start_live_session = AsyncMock()
    return fm


def _post(user, session, action: str):
    client = Client()
    token = AccessToken.for_user(user)
    with patch.object(
        FrameManager, "get_instance", classmethod(lambda cls: _idle_frame_manager())
    ):
        return client.post(
            f"/api/trading/sessions/{session.id}/{action}/",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )


@pytest.mark.django_db
class TestModeAccountInvariant:
    def test_start_rejects_paper_session_on_live_account(self):
        """paper 会话绑了实盘账户 → 拒绝启动（否则「演练」在动真钱）"""
        session = _session(_user("mismatch-paper"), "paper", testnet=False)

        resp = _post(session.user, session, "start")

        assert resp.status_code == 400, resp.content
        assert "testnet" in resp.json()["error"]
        session.refresh_from_db()
        assert session.status == "pending", "拒绝启动时不得改动会话状态"

    def test_start_rejects_live_session_on_testnet_account(self):
        """live 会话绑了测试网账户 → 拒绝启动（promote 的产物正是这一形状）"""
        session = _session(_user("mismatch-live"), "live", testnet=True)

        resp = _post(session.user, session, "start")

        assert resp.status_code == 400, resp.content
        assert "testnet" in resp.json()["error"]

    def test_start_rejects_unknown_mode(self):
        """mode 是自由字符串，未知值不得被当成 live（那是更激进的一侧）"""
        session = _session(_user("mismatch-unknown"), "paper", testnet=True)
        LiveSession.objects.filter(pk=session.pk).update(mode="foo")
        session.refresh_from_db()

        resp = _post(session.user, session, "start")

        assert resp.status_code == 400, resp.content
        assert "foo" in resp.json()["error"]

    def test_start_allows_consistent_paper_session(self):
        """自洽时不受影响：paper + testnet 账户照常启动"""
        session = _session(_user("consistent-paper"), "paper", testnet=True)

        resp = _post(session.user, session, "start")

        assert resp.status_code == 200, resp.content
        session.refresh_from_db()
        assert session.status == "running"

    def test_resume_rejects_mismatch_and_keeps_db_status(self):
        """恢复也是一次启动：不能从 resume 口绕开同一不变式"""
        session = _session(_user("mismatch-resume"), "paper", testnet=False, status="paused")

        resp = _post(session.user, session, "resume")

        assert resp.status_code == 400, resp.content
        session.refresh_from_db()
        assert session.status == "paused"

    def test_resume_allows_consistent_session(self):
        session = _session(_user("consistent-resume"), "paper", testnet=True, status="paused")

        resp = _post(session.user, session, "resume")

        assert resp.status_code == 200, resp.content

    def test_session_without_account_is_rejected(self):
        """没有账户就无从谈一致性；start 早就有这条前置，resume 也该有"""
        session = _session(_user("no-account"), "paper", testnet=True)
        LiveSession.objects.filter(pk=session.pk).update(exchange_account=None)
        session.refresh_from_db()

        assert session.mode_account_mismatch() is not None


def _promote(user, session):
    client = Client()
    token = AccessToken.for_user(user)
    return client.post(
        f"/api/trading/sessions/{session.id}/promote/",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )


@pytest.mark.django_db
class TestPromoteProducesConsistentSession:
    """promote 原样继承源账户，源会话是 paper ⇒ 账户必然 testnet=True ⇒ 产物是
    一支 mode=live + testnet=True 的会话，启动时必被上面的校验拒绝。不能一边返回
    201「Promoted to live trading session」一边交出一支永远起不来的会话。"""

    def test_promote_rejects_testnet_account_and_leaves_source_untouched(self):
        user = _user("promote-testnet")
        session = _session(user, "paper", testnet=True, status="running")

        resp = _promote(user, session)

        assert resp.status_code == 400, resp.content
        assert "testnet" in resp.json()["error"]
        session.refresh_from_db()
        assert session.status == "running", "拒绝时必须先于停止源会话"
        assert not LiveSession.objects.filter(mode="live").exists()

    def test_promote_rejects_session_without_account(self):
        user = _user("promote-no-account")
        session = _session(user, "paper", testnet=True, status="stopped")
        LiveSession.objects.filter(pk=session.pk).update(exchange_account=None)

        resp = _promote(user, session)

        assert resp.status_code == 400, resp.content
        assert not LiveSession.objects.filter(mode="live").exists()

    def test_promote_checks_the_account_not_the_mode(self):
        """只对 testnet 拒绝，不是无差别封路：绑了实盘账户时 promote 照常产出。

        这个 paper + testnet=False 的源会话是直接写库造出来的（绕过启动校验），
        用来钉住「拒绝的唯一依据是账户属性」。
        """
        from apps.exchange.models import ExchangeAccount

        user = _user("promote-live-account")
        session = _session(user, "paper", testnet=True, status="stopped")
        live_account = ExchangeAccount.objects.create(
            exchange="binance",
            label="promote-live",
            api_key_enc=b"enc",
            api_secret_enc=b"enc",
            testnet=False,
        )
        LiveSession.objects.filter(pk=session.pk).update(exchange_account=live_account)

        resp = _promote(user, session)

        assert resp.status_code == 201, resp.content
        promoted = LiveSession.objects.get(mode="live")
        assert promoted.mode_account_mismatch() is None
