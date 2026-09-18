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
