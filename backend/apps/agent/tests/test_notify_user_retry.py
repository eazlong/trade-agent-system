"""P1 回归：notify_user 瞬时 DB 故障重试（celery 连接被 PG 杀死）。

事故（2026-09-18，定时任务 6e2d7793）：
- 06:23:40 agent 任务开始，06:24:10 恰好 30s 后 _persist_notification 失败：
  "server closed the connection unexpectedly"（PostgreSQL
  idle_in_transaction_session_timeout=30000ms 杀掉了 celery worker 的
  空闲事务连接，LLM 生成期间连接闲置）；
- notify_user 返回失败 → LLM 回退"直接输出报告" → 指标全部虚构 +
  最终报告未投递给用户。

修复（本文件覆盖）：
瞬时故障时丢弃疑似坏连接（connections.close_all()）并重试一次；
重试成功 → 返回成功（标记 retry）；重试仍失败 → 返回错误。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.agent.tools.notify_user import NotifyUserTool


def _tool():
    return NotifyUserTool()


@pytest.mark.asyncio
async def test_retry_recovers_from_dead_connection():
    """核心回归：首次持久化被 PG 杀连接 → 重试成功 → 通知送达。"""
    tool = _tool()
    tool._persist_notification = AsyncMock(
        side_effect=[
            RuntimeError("server closed the connection unexpectedly"),
            None,
        ]
    )
    tool._send_via_redis = MagicMock()

    with patch("django.db.connections.close_all") as close_all:
        result = await tool.execute(message="回测报告", user_id="user1")

    assert result.success is True
    assert "重试" in result.data["message"]
    assert tool._persist_notification.await_count == 2
    assert tool._send_via_redis.call_count == 1
    close_all.assert_called_once()  # 两次尝试之间丢弃坏连接


@pytest.mark.asyncio
async def test_first_attempt_success_no_retry():
    """首次成功不得触发重试 / close_all（防误伤）。"""
    tool = _tool()
    tool._persist_notification = AsyncMock(return_value=None)
    tool._send_via_redis = MagicMock()

    with patch("django.db.connections.close_all") as close_all:
        result = await tool.execute(message="hello", user_id="user1")

    assert result.success is True
    assert result.data["message"] == "通知已发送"
    assert tool._persist_notification.await_count == 1
    close_all.assert_not_called()


@pytest.mark.asyncio
async def test_persistent_failure_returns_error():
    """重试仍失败 → 如实返回错误（不得谎报成功）。"""
    tool = _tool()
    tool._persist_notification = AsyncMock(
        side_effect=[RuntimeError("conn dead"), RuntimeError("still dead")]
    )
    tool._send_via_redis = MagicMock()

    with patch("django.db.connections.close_all") as close_all:
        result = await tool.execute(message="hello", user_id="user1")

    assert result.success is False
    assert "发送通知失败" in result.error
    assert "still dead" in result.error
    assert tool._persist_notification.await_count == 2
    close_all.assert_called_once()  # 仅两次尝试之间调用一次
    tool._send_via_redis.assert_not_called()


# ── 第二次修正（2026-09-18 生产验证发现）：重试必须在同步线程内换连接 ──
# 生产实测：事件循环线程里的 connections.close_all() 打不到 sync_to_async
# 线程池的连接，第 2 次仍打旧连接 → "connection already closed"。
# 正确做法：在同步线程内捕获 OperationalError/InterfaceError → connection.close() → 重放。


@pytest.mark.asyncio
async def test_persist_notification_self_heals_in_sync_thread(db):
    """ORM 首次因连接失效抛错 → 同步线程内 close + 重放 → 成功且只重放一次。"""
    from django.contrib.auth import get_user_model
    from django.db import OperationalError

    from apps.notify.models import Notification

    user = get_user_model().objects.create_user(
        email="retry@test.local", username="retry_user", password="pw12345"
    )

    calls = {"n": 0}
    real_create = Notification.objects.create

    def flaky_create(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OperationalError("server closed the connection unexpectedly")
        return real_create(**kwargs)

    with patch.object(Notification.objects, "create", side_effect=flaky_create):
        await NotifyUserTool._persist_notification(str(user.id), "自愈测试")

    assert calls["n"] == 2  # 失败 1 次 + 重放 1 次 → 说明捕获了瞬时异常并重放
    assert Notification.objects.filter(user=user, message="自愈测试").count() == 1
    # 注：重放前的 connection.close() 无法在此断言——Django 测试库是**内存
    # sqlite**，sqlite3 backend 对内存库的 close() 是 no-op（防止销毁库）。
    # 该步骤（同线程 close → 新连接）由生产日志验证：
    # "[NotifyUserTool] stale DB connection in sync thread; closing and retrying once"。


@pytest.mark.asyncio
async def test_persist_notification_propagates_persistent_failure(db):
    """两次都失败 → 异常透传（由外层 execute 如实报错，不谎报成功）。"""
    from django.contrib.auth import get_user_model
    from django.db import OperationalError

    from apps.notify.models import Notification

    user = get_user_model().objects.create_user(
        email="dead@test.local", username="dead_user", password="pw12345"
    )

    with patch.object(
        Notification.objects,
        "create",
        side_effect=OperationalError("connection already closed"),
    ):
        with pytest.raises(OperationalError):
            await NotifyUserTool._persist_notification(str(user.id), "x")
