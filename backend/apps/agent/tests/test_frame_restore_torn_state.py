"""Regression: trading frame must survive restarts with torn persisted state.

Bug: 实盘启动后进程重启，Redis 残留撕裂状态
(`frame:trading:state=stopped` 但 `frame:order_executor=1`)。
旧逻辑 `need_restart_trading = (_trading_state == RUNNING)` 在撕裂态下恒为
False → 交易框架永不自动重启 → kline 无人订阅、信号无人分发、订单永远不会出现。

修复：`order_exec==1`（executor 曾初始化）时无条件重建交易框架。
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.agent.frame_manager import FrameManager, FrameState


def _make_fm(**attrs) -> FrameManager:
    """构造 FM 实例，跳过 __init__（避免真实 Redis/DB 访问）。"""
    fm = FrameManager.__new__(FrameManager)
    fm._trading_state = FrameState.STOPPED
    fm._assist_state = FrameState.STOPPED
    fm._risk_guard_refs = 0
    fm._data_feed_refs = 0
    fm._order_executor = None
    fm._order_consumer_task = None
    fm._strategy_runners = {}
    fm._need_restart = False
    fm._riskguard = None
    fm._kline_check_sem = asyncio.Semaphore(2)
    for k, v in attrs.items():
        setattr(fm, k, v)
    return fm


@pytest.mark.asyncio
async def test_torn_state_force_restarts_trading_frame():
    """撕裂态（order_exec=1 + trading=stopped）必须自动重启交易框架。"""
    fm = _make_fm(
        _need_restart=True,  # 由 _restore_frame_states 在 order_exec=="1" 时设置
        _trading_state=FrameState.STOPPED,  # 撕裂：executor 曾初始化但 state 被回滚
        _risk_guard_refs=0,
        _data_feed_refs=0,
    )
    fm.start_trading_frame = AsyncMock()
    fm.start_assist_frame = AsyncMock()
    fm._restore_live_sessions = AsyncMock()

    await fm.restore_and_restart_frames()

    fm.start_trading_frame.assert_awaited_once()
    call_kwargs = fm.start_trading_frame.call_args.kwargs
    assert call_kwargs.get("force") is True, (
        "撕裂态恢复必须 force=True 重建底层组件（OrderExecutor 无法跨进程复用）"
    )
    fm._restore_live_sessions.assert_awaited_once()


@pytest.mark.asyncio
async def test_clean_stopped_state_does_not_restart():
    """干净停止（executor=0 + trading=stopped）不应触发重启。"""
    fm = _make_fm()
    fm.start_trading_frame = AsyncMock()
    fm.start_assist_frame = AsyncMock()
    fm._restore_live_sessions = AsyncMock()

    await fm.restore_and_restart_frames()

    fm.start_trading_frame.assert_not_awaited()
    fm.start_assist_frame.assert_not_awaited()


@pytest.mark.asyncio
async def test_running_state_restarts_with_force_when_need_restart():
    """正常 running 状态 + _need_restart（进程重启）也应强制重建。"""
    fm = _make_fm(
        _need_restart=True,
        _trading_state=FrameState.RUNNING,
        _risk_guard_refs=0,
        _data_feed_refs=0,
    )
    fm.start_trading_frame = AsyncMock()
    fm._restore_live_sessions = AsyncMock()

    await fm.restore_and_restart_frames()

    fm.start_trading_frame.assert_awaited_once()
    assert fm.start_trading_frame.call_args.kwargs.get("force") is True


@pytest.mark.asyncio
async def test_start_trading_frame_heals_stale_running_state():
    """状态标记 RUNNING 但 OrderExecutor 未初始化时必须强制重建，
    而不是 early-return 导致框架永远无法启动。"""
    fm = _make_fm(_trading_state=FrameState.RUNNING, _order_executor=None)
    fm._start_data_feed = AsyncMock()
    fm._start_risk_guard = AsyncMock()
    fm._start_order_executor = AsyncMock()
    fm._start_order_consumer = AsyncMock()
    with patch.object(fm, "_persist_frame_state"):
        await fm.start_trading_frame(mode="paper")

    fm._start_order_executor.assert_awaited_once()
    fm._start_order_consumer.assert_awaited_once()
    assert fm._trading_state == FrameState.RUNNING


class _FakeSession:
    """最小 LiveSession 替身：只暴露 _restore_live_sessions 用到的字段。"""

    def __init__(
        self,
        strategy_name,
        symbol,
        status="running",
        config=None,
        initial_capital=Decimal("10000"),
        exchange_account_id="acct-1",
        user_id="user-1",
        timeframe="4h",
    ):
        self.id = uuid.uuid4()
        self.strategy = SimpleNamespace(name=strategy_name)
        self.symbol = symbol
        self.status = status
        self.config = config or {}
        self.initial_capital = initial_capital
        self.exchange_account = SimpleNamespace(id=exchange_account_id)
        self.user = SimpleNamespace(id=user_id)
        self.backtest_result = SimpleNamespace(timeframe=timeframe)


@pytest.mark.asyncio
async def test_restore_live_sessions_runs_db_running_session():
    """DB 中 status=running 的会话（即使 Redis 键已过期）必须被恢复。"""
    session = _FakeSession(
        strategy_name="test_strategy_probe",
        symbol="BTC/USDT",
        config={"param": 1},
        timeframe="4h",
    )
    sid = str(session.id)

    fm = _make_fm()
    fm.start_trading_frame = AsyncMock()  # 本测试只验证恢复路径，不真启动框架
    fm.start_strategy_runner = AsyncMock()
    fake_redis = MagicMock()
    fake_redis.keys.return_value = []
    fm._get_redis = MagicMock(return_value=fake_redis)

    from apps.trading.models import LiveSession

    fake_qs = MagicMock()
    fake_qs.select_related.return_value = [session]
    with (
        patch.object(LiveSession.objects, "filter", return_value=fake_qs),
        patch("apps.agent.frame_manager.close_old_connections"),
    ):
        await fm._restore_live_sessions()

    fm.start_strategy_runner.assert_awaited_once()
    kw = fm.start_strategy_runner.call_args.kwargs
    assert kw["live_session_id"] == sid
    assert kw["strategy_name"] == "test_strategy_probe"
    assert kw["symbol"] == "BTC/USDT"
    assert kw["parameters"] == {"param": 1}


@pytest.mark.asyncio
async def test_restore_live_sessions_starts_trading_frame_first():
    """有待恢复会话但交易框架未运行时，必须先启动框架，
    否则 OrderConsumer 不消费 stream → 信号永远不会变成订单。"""
    session = _FakeSession(
        strategy_name="test_strategy_probe",
        symbol="BTC/USDT",
    )

    fm = _make_fm()  # trading_state=stopped, executor=None
    fm.start_trading_frame = AsyncMock()
    fm.start_strategy_runner = AsyncMock()
    fake_redis = MagicMock()
    fake_redis.keys.return_value = []
    fm._get_redis = MagicMock(return_value=fake_redis)

    from apps.trading.models import LiveSession

    fake_qs = MagicMock()
    fake_qs.select_related.return_value = [session]
    with (
        patch.object(LiveSession.objects, "filter", return_value=fake_qs),
        patch("apps.agent.frame_manager.close_old_connections"),
    ):
        await fm._restore_live_sessions()

    fm.start_trading_frame.assert_awaited_once()
    assert fm.start_trading_frame.call_args.kwargs.get("force") is True
    fm.start_strategy_runner.assert_awaited_once()


@pytest.mark.asyncio
async def test_restore_live_sessions_marks_unresolvable_strategy_error():
    """策略无法解析（不存在）时标记会话 status=error，避免每次重启重试。"""
    session = _FakeSession(
        strategy_name="missing_strategy_probe",
        symbol="ETH/USDT",
    )

    fm = _make_fm()
    fm.start_trading_frame = AsyncMock()

    async def _fail(**kwargs):
        raise ValueError("Strategy not found: missing_strategy_probe")

    fm.start_strategy_runner = _fail
    fake_redis = MagicMock()
    fake_redis.keys.return_value = []
    fm._get_redis = MagicMock(return_value=fake_redis)

    from apps.trading.models import LiveSession

    fake_qs = MagicMock()
    fake_qs.select_related.return_value = [session]
    # 断言 DB 里该会话被标记为 error（filter(id=...).update(status="error")）
    marked = {}

    def _fake_update(**kwargs):
        marked.update(kwargs)
        return 1

    fake_qs.update.side_effect = _fake_update
    with (
        patch.object(LiveSession.objects, "filter", return_value=fake_qs),
        patch("apps.agent.frame_manager.close_old_connections"),
    ):
        await fm._restore_live_sessions()

    assert marked.get("status") == "error"
