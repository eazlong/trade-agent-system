"""升实盘前置体检（`apps/trading/preflight.py`）的口径与边界。

这一份钉四件事，每件都对应一类「体检自己出错」的形态：

1. **判据不复制**——「跑满观察期且判定通过」只有 `live_gate` 一处；体检报 pass/fail
   必须与 promote 那一刻的实际应答**同向**。所以这里有一条用例直接拿同一个夹具跑
   `_promote()`：闸门拒的，体检必须 `fail`；闸门放的，体检不得 `fail`。
2. **只读**——体检不许产生任何 session / candidate / 状态行的写入。这条用一个用例
   数「跑前跑后行数 + `status` 不变」来钉，而不是靠读代码。
3. **`skip` 不改方向**——`Redis` 不可达、`celery_app` 导不进、没给目标账户，三者都
   只落 `skip`。把它们算成 `no_go`，人就会去修一件体检没问到的事，而真正的 `fail`
   被埋在后面。
4. **`warn` 与 `fail` 的分界**——`halt` 命中与 `Shadow` 档位**不阻止升级**（升级建的
   是会话，不是订单），所以是 `warn`；而 `testnet=True` / 状态不对 / 没候选行是 promote
   当场 400，所以是 `fail`。混成一个档，读的人分不出「别点」与「点了会成，但你以为
   买到的东西不在」。

**刻意不重造 L3 的证据**：观察期 / 盈亏 / 风控命中的判定已由
`apps/strategy_factory/tests/test_paper_observation.py` 逐条钉过。这里只钉「体检把那
份结论**搬运**得对不对」，不钉「那份结论对不对」——后者已经在别处，再验一遍验的是
复制品。
"""

from __future__ import annotations

import datetime
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from apps.backtest.models import BacktestResult
from apps.exchange.models import ExchangeAccount
from apps.regime import halt
from apps.regime.models import HaltTrigger
from apps.regime.tests.test_halt import _declare
from apps.strategy_factory.live_gate import APPROVED_STATUS
from apps.strategy_factory.models import StrategyCandidate
from apps.strategy_factory.status import (
    StrategyCandidateOrigin,
    StrategyCandidateStatus,
)
from apps.trading import preflight
from apps.trading.models import (
    LiveSession,
    LiveSessionStatusChange,
    Order,
    Strategy,
)

User = get_user_model()


# --------------------------------------------------------------------------- #
# 夹具：与 `test_live_session_promotion.py` 同形。
#
# 刻意复用同一套形状而不是重写一套：两处的「一条可升级的会话长什么样」必须是同一个
# 答案，否则体检与 promote 的用例会在不同的世界线上各自全绿。
# --------------------------------------------------------------------------- #


def _user(username: str = "preflight"):
    return User.objects.create_user(
        username=username, email=f"{username}@example.com", password="pw12345"
    )


def _account(label: str, testnet: bool, *, is_active: bool = True):
    """账户凭据用**真的 Fernet 密文**。

    体检本身不解密（它不取余额），但同一条会话可能被 `_promote()` 那条对照用例端到端
    跑一遍，而那条路会解密。夹具造错时，失败会看起来像「余额接口坏了」。
    """
    from cryptography.fernet import Fernet
    from django.conf import settings

    f = Fernet(settings.FERNET_KEY.encode())
    return ExchangeAccount.objects.create(
        exchange="binance",
        label=label,
        api_key_enc=f.encrypt(b"test_key"),
        api_secret_enc=f.encrypt(b"test_secret"),
        testnet=testnet,
        is_active=is_active,
    )


def _strategy(name: str = "preflight_probe"):
    return Strategy.objects.create(name=name, code_path=f"strategies/{name}.py")


def _backtest(user, strategy, *, symbol="BTC/USDT", parameters=None):
    return BacktestResult.objects.create(
        strategy=strategy,
        user=user,
        symbol=symbol,
        timeframe="4h",
        start_date=datetime.date(2026, 1, 1),
        end_date=datetime.date(2026, 2, 1),
        initial_capital=Decimal("1000"),
        final_capital=Decimal("1100"),
        total_return_pct=10.0,
        review_status="approved",
        parameters=parameters or {},
    )


def _session(
    user,
    strategy,
    account,
    *,
    mode="paper",
    status="running",
    symbol="BTC/USDT",
    started_at=None,
    config=None,
    backtest=None,
):
    return LiveSession.objects.create(
        user=user,
        backtest_result=backtest,
        strategy=strategy,
        symbol=symbol,
        mode=mode,
        status=status,
        exchange_account=account,
        initial_capital=Decimal("1000"),
        current_equity=Decimal("1050"),
        started_at=started_at,
        config=config or {},
    )


def _candidate(session, *, status=APPROVED_STATUS, name="cand", user=None):
    return StrategyCandidate.objects.create(
        name=name,
        origin=StrategyCandidateOrigin.MANUAL.value,
        strategy=session.strategy,
        status=status,
        paper_session=session,
        proposed_by=user or session.user,
    )


def _timeline(session, *segments, start):
    """按 `(status, days)` 写时间线，起点 `start`。最后一段延续到 `start+总和`。"""
    cursor = start
    rows = []
    for status, days in segments:
        rows.append(
            LiveSessionStatusChange(
                live_session=session,
                from_status="",
                to_status=status,
                changed_at=cursor,
            )
        )
        cursor += timedelta(days=days)
    LiveSessionStatusChange.objects.bulk_create(rows)
    return cursor


def _fill_trades(session, count):
    for _ in range(count):
        Order.objects.create(
            user=session.user,
            exchange_account=session.exchange_account,
            symbol=session.symbol,
            side="sell",
            order_type="market",
            quantity=Decimal("1"),
            status="filled",
            live_session=session,
            realized_pnl=Decimal("1"),
            created_at=timezone.now() - timedelta(days=1),
        )


def _promote(user, session, *, body=None):
    client = Client()
    token = AccessToken.for_user(user)
    return client.post(
        f"/api/trading/sessions/{session.id}/promote/",
        data=body if body is not None else {},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )


def _report(**kwargs):
    """体检一次，并断言它只读（这条断言给每个用例都带上，而不是只给一个用例）。"""
    return preflight.preflight(**kwargs)


# --------------------------------------------------------------------------- #
# 1. 只读
# --------------------------------------------------------------------------- #


class TestItIsReadOnly:
    def test_preflight_writes_nothing(self, db):
        """体检前后：行数与 `status` 一字不差。**这是本模块的存在理由。**

        把它写成「数行数」而不是「读代码确认没 save」：后者会在某次重构成
        `session.save(update_fields=[...])` 时静默失效，而行数/状态断言会红。
        """
        user = _user("ro")
        strategy = _strategy("ro")
        paper_acct = _account("ro-paper", testnet=True)
        live_acct = _account("ro-live", testnet=False)
        backtest = _backtest(user, strategy)
        session = _session(
            user, strategy, paper_acct, backtest=backtest, started_at=timezone.now()
        )
        _candidate(session)

        before = {
            "sessions": LiveSession.objects.count(),
            "candidates": StrategyCandidate.objects.count(),
            "changes": LiveSessionStatusChange.objects.count(),
            "orders": Order.objects.count(),
            "status": LiveSession.objects.get(pk=session.pk).status,
        }

        preflight.preflight(paper_session=session, live_account=live_acct)

        after = {
            "sessions": LiveSession.objects.count(),
            "candidates": StrategyCandidate.objects.count(),
            "changes": LiveSessionStatusChange.objects.count(),
            "orders": Order.objects.count(),
            "status": LiveSession.objects.get(pk=session.pk).status,
        }
        assert after == before


# --------------------------------------------------------------------------- #
# 2. 判据不复制：体检的 fail ⇔ promote 会被拒
# --------------------------------------------------------------------------- #


class TestAgreesWithThePromoteGate:
    """同一份夹具，体检与 `_promote()` 必须同向。

    这一组不是「再测一遍闸门」，而是测**体检有没有把闸门的结论搬对**。搬错的形态是
    「体检报 go，点下去 400」——而那种错在单看体检输出时完全正常。
    """

    def _approved_setup(self, username: str):
        """一条「已跑满观察期且判定通过」的会话 + 它的候选。

        时长与成交笔数都从 `CONFIG` 现算：观察期与 `min_trades` 都是环境变量可覆盖的
        （txy 上是 1 天 / 8 笔），写死 15 天 / 30 笔会让这组用例在换环境的机器上红，
        而红的原因看起来像「体检判错了」——实际是用例自己没跟着配置走。
        """
        from apps.strategy_factory.config import CONFIG

        days = CONFIG.paper_observation_days + 1
        trades = CONFIG.min_trades

        user = _user(username)
        strategy = _strategy(username)
        paper_acct = _account(f"{username}-paper", testnet=True)
        live_acct = _account(f"{username}-live", testnet=False)
        backtest = _backtest(user, strategy)
        now = timezone.now()
        started = now - timedelta(days=days)
        session = _session(
            user,
            strategy,
            paper_acct,
            backtest=backtest,
            started_at=started,
            config={"position_pct": 0.1},
        )
        _timeline(session, ("running", days), start=started)
        _fill_trades(session, trades)
        candidate = _candidate(session)
        return user, session, candidate, live_acct

    def test_approved_session_has_no_blocking_check(self, db):
        user, session, _, live_acct = self._approved_setup("pf-go")
        report = _report(paper_session=session, live_account=live_acct)
        assert report.blocking == (), [(c.key, c.detail) for c in report.blocking]
        assert report.verdict in ("go", "go_with_warnings")
        assert report.get("candidate.status").status == preflight.PASS

    def test_approved_session_actually_promotes(self, db):
        """对照：体检说没有 fail 的这条，`confirmed=true` 真的能升。

        这是「判据同向」的另一半——只钉「体检说不 fail」而不同时钉「promote 真能过」，
        两个判据一起漂的时候用例全绿，而线上按 promote 会 400。
        """
        user, session, candidate, live_acct = self._approved_setup("pf-real")
        response = _promote(
            user,
            session,
            body={"exchange_account_id": str(live_acct.id), "confirmed": True},
        )
        assert response.status_code == 201, response.content
        live = LiveSession.objects.get(pk=response.json()["id"])
        assert live.mode == "live"
        assert live.exchange_account_id == live_acct.id
        assert StrategyCandidate.objects.get(pk=candidate.pk).status == "live"

    @pytest.mark.parametrize(
        "candidate_status",
        [
            StrategyCandidateStatus.PAPER_RUNNING.value,
            StrategyCandidateStatus.PAPER_FAILED.value,
            StrategyCandidateStatus.OOS_RUNNING.value,
            StrategyCandidateStatus.REGIME_HOLD.value,
        ],
    )
    def test_every_status_before_approval_is_a_blocking_check(
        self, db, candidate_status
    ):
        """非 `awaiting_live_approval` 的每个状态，体检与 promote 都拒。"""
        user = _user(f"pf-blk-{candidate_status}")
        strategy = _strategy(f"pf-blk-{candidate_status}")
        paper_acct = _account(f"pf-blk-{candidate_status}-paper", testnet=True)
        live_acct = _account(f"pf-blk-{candidate_status}-live", testnet=False)
        session = _session(user, strategy, paper_acct, started_at=timezone.now())
        _candidate(session, status=candidate_status)

        report = _report(paper_session=session, live_account=live_acct)
        check = report.get("candidate.status")
        assert check.status == preflight.FAIL
        assert report.verdict == "no_go"

        response = _promote(
            user,
            session,
            body={"exchange_account_id": str(live_acct.id), "confirmed": True},
        )
        assert response.status_code == 400, response.content

    def test_missing_candidate_row_is_blocking(self, db):
        """没有候选主行 = 历史 paper 会话，宿命就是永远 400。体检必须说清这句。"""
        user = _user("pf-nocand")
        strategy = _strategy("pf-nocand")
        paper_acct = _account("pf-nocand-paper", testnet=True)
        live_acct = _account("pf-nocand-live", testnet=False)
        session = _session(user, strategy, paper_acct, started_at=timezone.now())

        report = _report(paper_session=session, live_account=live_acct)
        assert report.get("candidate.row").status == preflight.FAIL
        assert "候选流水线" in report.get("candidate.row").detail
        assert report.verdict == "no_go"

        response = _promote(
            user,
            session,
            body={"exchange_account_id": str(live_acct.id), "confirmed": True},
        )
        assert response.status_code == 400


# --------------------------------------------------------------------------- #
# 3. 目标账户那四道
# --------------------------------------------------------------------------- #


class TestLiveAccountChecks:
    def _base(self, username: str, **session_kwargs):
        user = _user(username)
        strategy = _strategy(username)
        paper_acct = _account(f"{username}-paper", testnet=True)
        session = _session(
            user,
            strategy,
            paper_acct,
            started_at=timezone.now(),
            **session_kwargs,
        )
        _candidate(session)
        return user, session

    def test_testnet_target_is_blocking(self, db):
        """testnet 账户当实盘用，是 promote 的第一句拒绝原因。"""
        user, session = self._base("pf-testnet")
        target = _account("pf-testnet-target", testnet=True)
        report = _report(paper_session=session, live_account=target)
        assert report.get("live_account.testnet").status == preflight.FAIL

    def test_inactive_target_is_blocking(self, db):
        """`is_active=False` 的账户在视图里走的也是 400 那一支。"""
        user, session = self._base("pf-inactive")
        target = _account("pf-inactive-target", testnet=False, is_active=False)
        report = _report(paper_session=session, live_account=target)
        assert report.get("live_account.active").status == preflight.FAIL

    def test_symbol_conflict_is_blocking(self, db):
        """同账户同品种已有活跃会话：runner 共享账户级持仓，会互相平仓。"""
        user, session = self._base("pf-conflict", symbol="DOGE/USDT")
        target = _account("pf-conflict-target", testnet=False)
        other = _session(
            user,
            session.strategy,
            target,
            mode="live",
            status="running",
            symbol="DOGE/USDT",
        )
        assert other.pk != session.pk

        report = _report(paper_session=session, live_account=target)
        check = report.get("live_account.conflict")
        assert check.status == preflight.FAIL
        # 拒绝原因里必须带上那条会话的身份，否则读的人不知道去停哪一条。
        assert str(other.id)[:8] in check.detail

    def test_no_target_given_skips_instead_of_failing(self, db):
        """没给目标账户 → `skip`，不是 `fail`。

        这是本模块的立场：`skip` 只说明「这一条没问到」。算成 fail，人就会以为
        「少了个参数」等于「这条会话不能升」。
        """
        user, session = self._base("pf-notarget")
        report = _report(paper_session=session)
        check = report.get("live_account.specified")
        assert check.status == preflight.SKIP
        assert "--live-account" in check.detail
        assert not any(
            c.key.startswith("live_account.") and c.blocking for c in report.checks
        )


# --------------------------------------------------------------------------- #
# 4. skip 与 warn 不改方向
# --------------------------------------------------------------------------- #


class TestSkipsAndWarnings:
    def _approved(self, username: str, symbol="BTC/USDT", config=None):
        """同 `_approved_setup`：时长与笔数跟着 `CONFIG` 走（见那里的理由）。"""
        from apps.strategy_factory.config import CONFIG

        days = CONFIG.paper_observation_days + 1
        user = _user(username)
        strategy = _strategy(username)
        paper_acct = _account(f"{username}-paper", testnet=True)
        live_acct = _account(f"{username}-live", testnet=False)
        now = timezone.now()
        started = now - timedelta(days=days)
        session = _session(
            user,
            strategy,
            paper_acct,
            symbol=symbol,
            started_at=started,
            config=config if config is not None else {"position_pct": 0.1},
        )
        _timeline(session, ("running", days), start=started)
        _fill_trades(session, CONFIG.min_trades)
        _candidate(session)
        return user, session, live_acct

    def test_skip_does_not_flip_the_verdict_to_no_go(self, db):
        """一个只有 skip 的报告是 `go_with_warnings`，**不是** `no_go`。"""
        report = preflight.Report(
            paper_session_id="x",
            candidate_id=None,
            checks=(
                preflight.Check("a", "a", preflight.PASS),
                preflight.Check("b", "b", preflight.SKIP, "没问到"),
            ),
        )
        assert report.blocking == ()
        assert report.verdict == "go_with_warnings"

    def test_halt_hit_is_a_warning_not_a_blocker(self, db):
        """有层在拦：升级照样成（建的是会话），但那条会话开不了新仓。"""
        user, session, live_acct = self._approved("pf-halt")
        # 生效期显式盖住「现在」：`_declare` 的默认 `opened_at` 取的是它自己那个固定
        # 时刻，而体检用的是真实时钟——不显式给，这条用例会随日期推移变成空转的绿灯。
        _declare(
            trigger=HaltTrigger.BLANKET,
            scope=halt.global_scope(),
            opened_at=timezone.now() - timedelta(hours=1),
        )

        report = _report(paper_session=session, live_account=live_acct)
        check = report.get("halt.layers")
        assert check.status == preflight.WARN
        assert not check.blocking
        assert "不阻止升级" in check.detail

    def test_regime_gate_shadow_is_a_warning_not_a_blocker(self, db):
        """出厂档位就是 Shadow（一条切换流水都没有）。"""
        user, session, live_acct = self._approved("pf-shadow")
        report = _report(paper_session=session, live_account=live_acct)
        check = report.get("regime.mode")
        assert check.status == preflight.WARN
        assert not check.blocking
        assert "/regime gate on" in check.detail

    def test_missing_timeline_is_a_blocking_check(self, db):
        """**没有时间线 → fail**，不是 warn。

        这条与「观察期没满」必须分开判：`running_duration` 在无时间线时返回 `None`，
        判定据此落 `insufficient_evidence` 并**一直等**——看起来与「还没跑够」一模一样，
        而处置相反（前者要回填时间线，后者只要继续跑）。这是迁移 0008 之前建的会话。

        判据与「墙钟过了多久」无关（`running_duration` 压根不看 `started_at`），所以
        这里的 30 天只是让这条会话「看起来跑够了」——把变量的作用说清，省得后来的人
        以为这条用例在验时长。
        """
        user = _user("pf-notimeline")
        strategy = _strategy("pf-notimeline")
        paper_acct = _account("pf-notimeline-paper", testnet=True)
        live_acct = _account("pf-notimeline-live", testnet=False)
        session = _session(
            user, strategy, paper_acct, started_at=timezone.now() - timedelta(days=30)
        )
        _candidate(session)

        report = _report(paper_session=session, live_account=live_acct)
        check = report.get("paper.timeline")
        assert check.status == preflight.FAIL
        assert "回填" in check.detail
        assert report.verdict == "no_go"

    def test_unfinished_observation_is_a_warning_not_a_failure(self, db):
        """观察期没满只是「还没到时候」——它不该让体检说 no_go。

        那条会话确实还不能升，但那句话由 `candidate.status` 那条闸门原话负责；
        观察期那一行只负责回答「差多少天」。

        **时长按 `CONFIG` 现算，不写死 14 天**：观察期是环境变量可覆盖的
        （`STRATEGY_FACTORY_PAPER_OBSERVATION_DAYS`），txy 上就是 1 天。写死的话这条
        用例在本地绿、在 txy 红——而且红的原因看起来像「体检的观察期判错了」。
        """
        from apps.strategy_factory.config import CONFIG

        required = CONFIG.paper_observation_days
        # 用比门槛少一半的时长，两端都不贴边界（要求 ≥1 天时取不到更短的正数，
        # 那时就取 0：started_at 与时间线起点都在「现在」，running 为 0 天）。
        elapsed = max(required // 2, 0)

        user = _user("pf-short")
        strategy = _strategy("pf-short")
        paper_acct = _account("pf-short-paper", testnet=True)
        live_acct = _account("pf-short-live", testnet=False)
        now = timezone.now()
        started = now - timedelta(days=elapsed)
        session = _session(user, strategy, paper_acct, started_at=started)
        _timeline(session, ("running", elapsed), start=started)
        _candidate(session, status=StrategyCandidateStatus.PAPER_RUNNING.value)

        report = _report(paper_session=session, live_account=live_acct)
        assert report.get("paper.observation").status == preflight.WARN
        assert report.get("paper.timeline").status == preflight.PASS
        assert report.get("candidate.status").status == preflight.FAIL

    def test_observation_detail_names_the_configured_threshold(self, db):
        """detail 里的门槛数必须来自 `CONFIG`，不能是写死的 14。

        这条与上一条合起来才封住那个漂移：上一条让用例跟着配置走，这一条让**输出**跟着
        配置走。只有前者时，报告仍可能印着「要求 ≥ 14 天」而实际按 1 天判。
        """
        from apps.strategy_factory.config import CONFIG

        user = _user("pf-threshold")
        strategy = _strategy("pf-threshold")
        paper_acct = _account("pf-threshold-paper", testnet=True)
        now = timezone.now()
        session = _session(
            user, strategy, paper_acct, started_at=now - timedelta(days=1)
        )
        _timeline(session, ("running", 1), start=now - timedelta(days=1))
        _candidate(session, status=StrategyCandidateStatus.PAPER_RUNNING.value)

        detail = _report(paper_session=session).get("paper.observation").detail
        assert f"≥ {CONFIG.paper_observation_days} 天" in detail


# --------------------------------------------------------------------------- #
# 5. 单仓占比那条近似判据（纯函数，单独钉）
# --------------------------------------------------------------------------- #


class TestRiskCapCheck:
    """`risk_cap_check` 是体检里唯一自己算数的判据，所以它必须能被单独钉住。

    另外那些都靠「问某一处唯一出处」，验的是搬运；这一条验的是判据本身。
    """

    @pytest.mark.parametrize(
        "pct,perp,expected",
        [
            (0.10, False, preflight.PASS),  # 现货上限 0.20，离得开
            # **恰等上限是 warn，不是 pass**：风控只在 `ratio > cap` 时拒，所以恰等
            # 理论上能过；但这正是近似判据最不准的地方——策略按「总资金 × pct」算量，
            # 而风控除的是**账户余额**。余额只要比总资金少一点，这一单就会被拒。
            (0.20, False, preflight.WARN),
            (0.2001, False, preflight.FAIL),  # 过线一点就超
            (0.05, True, preflight.PASS),  # 合约上限 0.10
            (0.10, True, preflight.WARN),  # 合约上限恰等，同一条理由
            (0.11, True, preflight.FAIL),  # 合约上限比现货紧
            (0.18, False, preflight.WARN),  # 0.18 > 0.20×0.8 → 贴线
        ],
    )
    def test_cap_boundaries(self, db, pct, perp, expected):
        check = preflight.risk_cap_check("BTC/USDT", pct, perp=perp)
        assert check.status == expected

    def test_unknown_position_pct_is_a_skip(self, db):
        check = preflight.risk_cap_check("BTC/USDT", None)
        assert check.status == preflight.SKIP

    def test_the_detail_says_it_is_an_approximation(self, db):
        """近似判据必须自称近似。

        风控比的是 `price×qty / 账户余额`，而策略按 `总资金 × position_pct` 算量；
        两边只在「总资金 == 余额」时严格相等。不写这句，读的人会把一个近似值当闸门。
        """
        check = preflight.risk_cap_check("BTC/USDT", 0.1)
        assert "近似" in check.detail


# --------------------------------------------------------------------------- #
# 6. 与视图的那份副本保持一致
# --------------------------------------------------------------------------- #


def test_promotable_statuses_match_the_view(db):
    """体检允许的状态集合必须与 `live_session_promote` 那一行一致。

    `preflight` 不 import 视图（那会把 DRF 拖进来），所以 `PROMOTABLE_SESSION_STATUSES`
    是一份副本。副本必须有一条用例钉着——否则视图改了那一行、体检还在放行一个必然 400
    的状态，而两边各自看都对。

    **用行为验，不用源码子串验。** 视图被 `@api_view` 包过一层，
    `inspect.getsource` 拿到的是装饰器生成的 `view()`（DRF 的 dispatch），不是被装饰的
    函数体——源码子串判据在那里恒为假，会变成一条永远红的用例（或换个写法变成永远绿的
    装饰）。发一次真请求则是不可伪造的：允许的状态必须走得到干跑，其余必须 400。
    """
    all_statuses = [value for value, _label in LiveSession.STATUS_CHOICES]
    assert set(preflight.PROMOTABLE_SESSION_STATUSES) <= set(all_statuses), (
        "PROMOTABLE_SESSION_STATUSES 里有 LiveSession 根本不存在的状态"
    )

    for status in all_statuses:
        user = _user(f"pf-drift-{status}")
        strategy = _strategy(f"pf-drift-{status}")
        paper_acct = _account(f"pf-drift-{status}-paper", testnet=True)
        live_acct = _account(f"pf-drift-{status}-live", testnet=False)
        session = _session(user, strategy, paper_acct, status=status)
        _candidate(session)

        # 不带 `confirmed` → 干跑：什么都不改，只回摘要或拒绝原因。
        response = _promote(
            user, session, body={"exchange_account_id": str(live_acct.id)}
        )
        allowed = status in preflight.PROMOTABLE_SESSION_STATUSES
        if allowed:
            assert response.status_code == 200, (
                f"视图放行 {status!r} 而体检没有 → 体检会报出一条不存在的 fail"
            )
            assert response.json().get("dry_run") is True
        else:
            assert response.status_code == 400, (
                f"视图拒绝 {status!r} 而体检把它算进允许集合 → 体检会放行必然 400 的会话"
            )
            assert "cannot promote" in response.json()["error"]


def test_scan_covers_candidates_without_a_paper_session(db):
    """`--scan` 必须把「还没走到 L3」的候选也列出来，且**不读成可以升**。

    这一条是 txy 上真实跑出来的缺陷：`Report` 当初只有三档 verdict，而这类候选一条
    fail 都没有（它没有可判的对象），于是它落 `go_with_warnings`。`--scan` 的输出因此
    写着「可以升（但有需要你知道的条目）」，指着两条 `in_sample_failed` 的候选——它们
    连 paper 会话都还没有。

    所以 `applicable=False` 必须是**独立的一档**，且 `--strict` 不拿它当不通过：
    「还没到 L3」不是「体检不合格」，它的下一步是改参数重跑，不是修体检。
    """
    user = _user("pf-scan")
    strategy = _strategy("pf-scan")
    candidate = StrategyCandidate.objects.create(
        name="pf-scan",
        origin=StrategyCandidateOrigin.MANUAL.value,
        strategy=strategy,
        status=StrategyCandidateStatus.IN_SAMPLE_FAILED.value,
        proposed_by=user,
    )

    reports = preflight.scan()
    row = next(r for r in reports if r.candidate_id == str(candidate.pk))
    assert row.paper_session_id is None
    assert row.applicable is False
    assert row.get("paper.evidence").status == preflight.SKIP
    assert row.blocking == ()
    assert row.verdict == "not_applicable"
    assert row.as_payload()["verdict"] == "not_applicable"
    # 状态显示名进 detail（`in_sample_failed` 是落库值，不该直接甩给人）。
    assert "样本内未过" in row.get("candidate.row").detail


def test_scan_skips_terminal_candidates(db):
    """终态不进来：它们没有下一步可体检，列出来只会让清单越来越长。"""
    user = _user("pf-scan-terminal")
    strategy = _strategy("pf-scan-terminal")
    for index, status in enumerate(
        [
            StrategyCandidateStatus.LIVE.value,
            StrategyCandidateStatus.REJECTED.value,
            StrategyCandidateStatus.CANCELLED.value,
        ]
    ):
        StrategyCandidate.objects.create(
            name=f"pf-term-{index}",
            origin=StrategyCandidateOrigin.MANUAL.value,
            strategy=strategy,
            status=status,
            proposed_by=user,
        )

    reports = preflight.scan()
    assert reports == []


def test_applicable_is_independent_of_the_three_verdicts(db):
    """`applicable=False` 压过另外三档——它不是「更轻的 no_go」，是另一件事。"""
    report = preflight.Report(
        paper_session_id=None,
        candidate_id="x",
        applicable=False,
        checks=(preflight.Check("a", "a", preflight.PASS),),
    )
    assert report.verdict == "not_applicable"
    assert report.blocking == ()
