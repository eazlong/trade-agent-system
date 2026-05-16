import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestSessionWatchdogScanTasks:
    """Tests for _scan_tasks() — detecting timed-out running tasks."""

    @pytest.mark.asyncio
    async def test_scan_tasks_detects_overtime_task(self):
        """Task whose last_alive is older than expected_duration * 1.5 → flagged."""
        from apps.agent.session_watchdog import SessionWatchdog

        wd = SessionWatchdog()
        stale_time = (datetime.now(timezone.utc) - timedelta(seconds=400)).isoformat()

        mock_redis_data = {
            "task:progress:task1": {
                "status": "running",
                "task_id": "task1",
                "user_id": "user1",
                "expected_duration": "240",
                "last_alive": stale_time,
                "updated_at": stale_time,
                "retry_count": "0",
                "original_task": json.dumps({"task_id": "task1", "user_id": "user1", "payload": {"text": "hello"}}),
            },
        }
        mock_r = AsyncMock()
        mock_r.hgetall = AsyncMock(side_effect=lambda k: mock_redis_data.get(k, {}))
        mock_r.keys = AsyncMock(return_value=["task:progress:task1"])

        with patch("apps.agent.session_watchdog._get_watchdog_redis", return_value=mock_r), \
             patch("apps.agent.session_watchdog._send_notification_redis") as mock_notify, \
             patch("apps.agent.session_watchdog.bus") as mock_bus:
            mock_bus.publish = AsyncMock()
            count = await wd._scan_tasks()

        assert count == 1
        mock_bus.publish.assert_called()

    @pytest.mark.asyncio
    async def test_scan_tasks_ignores_normal_task(self):
        """Task with recent last_alive should not be flagged."""
        from apps.agent.session_watchdog import SessionWatchdog

        wd = SessionWatchdog()
        recent_time = datetime.now(timezone.utc).isoformat()

        mock_redis_data = {
            "task:progress:task2": {
                "status": "running",
                "task_id": "task2",
                "user_id": "user2",
                "expected_duration": "240",
                "last_alive": recent_time,
                "updated_at": recent_time,
                "retry_count": "0",
                "original_task": json.dumps({"task_id": "task2"}),
            },
        }
        mock_r = AsyncMock()
        mock_r.hgetall = AsyncMock(side_effect=lambda k: mock_redis_data.get(k, {}))
        mock_r.keys = AsyncMock(return_value=["task:progress:task2"])

        with patch("apps.agent.session_watchdog._get_watchdog_redis", return_value=mock_r), \
             patch("apps.agent.session_watchdog._send_notification_redis") as mock_notify, \
             patch("apps.agent.session_watchdog.bus") as mock_bus:
            mock_bus.publish = AsyncMock()
            count = await wd._scan_tasks()

        assert count == 0
        mock_bus.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_scan_tasks_max_retries_stops_retrying(self):
        """retry_count >= WATCHDOG_MAX_AUTO_RETRY → notify user, do NOT retry."""
        from apps.agent.session_watchdog import SessionWatchdog

        wd = SessionWatchdog()
        stale_time = (datetime.now(timezone.utc) - timedelta(seconds=400)).isoformat()

        mock_redis_data = {
            "task:progress:task3": {
                "status": "running",
                "task_id": "task3",
                "user_id": "user3",
                "expected_duration": "240",
                "last_alive": stale_time,
                "updated_at": stale_time,
                "retry_count": "2",
                "original_task": json.dumps({"task_id": "task3"}),
            },
        }
        mock_r = AsyncMock()
        mock_r.hgetall = AsyncMock(side_effect=lambda k: mock_redis_data.get(k, {}))
        mock_r.keys = AsyncMock(return_value=["task:progress:task3"])

        with patch("apps.agent.session_watchdog._get_watchdog_redis", return_value=mock_r), \
             patch("apps.agent.session_watchdog._send_notification_redis") as mock_notify, \
             patch("apps.agent.session_watchdog.bus") as mock_bus:
            mock_bus.publish = AsyncMock()
            count = await wd._scan_tasks()

        assert count == 1
        mock_notify.assert_called()
        mock_bus.publish.assert_not_called()


class TestSessionWatchdogScanSessions:
    """Tests for _scan_sessions() — session expiry warnings."""

    @pytest.mark.asyncio
    async def test_scan_sessions_warns_near_expiry(self):
        """Session with TTL < 5min remaining → warning sent."""
        from apps.agent.session_watchdog import SessionWatchdog

        wd = SessionWatchdog()
        near_expiry = time.time() + 200

        session_data = json.dumps({
            "state": "multi_turn",
            "active_agent": "quant",
            "expires_at": near_expiry,
        })

        mock_r = AsyncMock()
        mock_r.keys = AsyncMock(return_value=["session:user123:context"])
        mock_r.get = AsyncMock(return_value=session_data)

        with patch("apps.agent.session_watchdog._get_watchdog_redis", return_value=mock_r), \
             patch("apps.agent.session_watchdog._send_notification_redis") as mock_notify:
            count = await wd._scan_sessions()

        assert count == 1
        mock_notify.assert_called()

    @pytest.mark.asyncio
    async def test_scan_sessions_ignores_fresh_session(self):
        """Session with TTL > 5min remaining → no warning."""
        from apps.agent.session_watchdog import SessionWatchdog

        wd = SessionWatchdog()
        far_expiry = time.time() + 1000

        session_data = json.dumps({
            "state": "multi_turn",
            "active_agent": "quant",
            "expires_at": far_expiry,
        })

        mock_r = AsyncMock()
        mock_r.keys = AsyncMock(return_value=["session:user456:context"])
        mock_r.get = AsyncMock(return_value=session_data)

        with patch("apps.agent.session_watchdog._get_watchdog_redis", return_value=mock_r), \
             patch("apps.agent.session_watchdog._send_notification_redis") as mock_notify:
            count = await wd._scan_sessions()

        assert count == 0
        mock_notify.assert_not_called()


class TestSessionWatchdogScanDlq:
    """Tests for _scan_dlq() — DLQ message recovery."""

    @pytest.mark.asyncio
    async def test_scan_dlq_recovers_and_republishes(self):
        """DLQ message → extracted, republished to agent:tasks, acked."""
        from apps.agent.session_watchdog import SessionWatchdog

        wd = SessionWatchdog()

        mock_r = AsyncMock()
        mock_r.xgroup_create = AsyncMock(return_value=True)
        mock_r.xreadgroup = AsyncMock(return_value=[
            ("agent:tasks:dlq", [
                ("msg-001", {
                    "task_id": json.dumps("task-dlq-1"),
                    "user_id": json.dumps("user-dlq"),
                    "payload": json.dumps({"text": "hello"}),
                    "dlq_reason": json.dumps("exceeded 3 retries"),
                    "retry_count": json.dumps("3"),
                }),
            ]),
        ])
        mock_r.xack = AsyncMock(return_value=1)

        with patch("apps.agent.session_watchdog._get_watchdog_redis", return_value=mock_r), \
             patch("apps.agent.session_watchdog._send_notification_redis") as mock_notify, \
             patch("apps.agent.session_watchdog.bus") as mock_bus:
            mock_bus.publish = AsyncMock(return_value="new-msg-id")
            count = await wd._scan_dlq()

        assert count == 1
        mock_bus.publish.assert_called_once()
        mock_r.xack.assert_called_once_with("agent:tasks:dlq", "agents", "msg-001")
        mock_notify.assert_called_once()

    @pytest.mark.asyncio
    async def test_scan_dlq_empty_returns_zero(self):
        """Empty DLQ → returns 0."""
        from apps.agent.session_watchdog import SessionWatchdog

        wd = SessionWatchdog()

        mock_r = AsyncMock()
        mock_r.xgroup_create = AsyncMock(return_value=True)
        mock_r.xreadgroup = AsyncMock(return_value=[])

        with patch("apps.agent.session_watchdog._get_watchdog_redis", return_value=mock_r), \
             patch("apps.agent.session_watchdog.bus") as mock_bus:
            mock_bus.publish = AsyncMock()
            count = await wd._scan_dlq()

        assert count == 0
        mock_bus.publish.assert_not_called()


class TestSessionWatchdogRecoverTask:
    """Tests for _recover_task()."""

    @pytest.mark.asyncio
    async def test_recover_stream_task_republishes(self):
        """Stream task with original_task -> republished to agent:tasks."""
        from apps.agent.session_watchdog import SessionWatchdog

        wd = SessionWatchdog()

        mock_r = AsyncMock()
        mock_r.hset = AsyncMock()

        data = {
            "original_task": json.dumps({
                "task_id": "task-x",
                "user_id": "user-x",
                "payload": {"text": "retry me"},
            }),
        }

        with patch("apps.agent.session_watchdog.bus") as mock_bus, \
             patch("apps.agent.session_watchdog._send_notification_redis") as mock_notify, \
             patch("apps.agent.session_watchdog.asyncio.sleep", new_callable=AsyncMock):
            mock_bus.publish = AsyncMock(return_value="new-msg-id")
            await wd._recover_task("task-x", "user-x", data, 0, mock_r)

        mock_bus.publish.assert_called_once()
        call_args = mock_bus.publish.call_args
        assert call_args[0][0] == "agent:tasks"
        assert call_args[0][1]["retry_count"] == "1"

    @pytest.mark.asyncio
    async def test_recover_task_no_original_notifies_user(self):
        """Missing original_task -> notify user, mark zombie."""
        from apps.agent.session_watchdog import SessionWatchdog

        wd = SessionWatchdog()

        mock_r = AsyncMock()
        mock_r.hset = AsyncMock()

        data = {}  # no original_task

        with patch("apps.agent.session_watchdog._send_notification_redis") as mock_notify, \
             patch("apps.agent.session_watchdog.bus") as mock_bus:
            mock_bus.publish = AsyncMock()
            await wd._recover_task("task-y", "user-y", data, 0, mock_r)

        mock_bus.publish.assert_not_called()
        mock_r.hset.assert_called_with("task:progress:task-y", "status", "zombie")
        mock_notify.assert_called()


class TestSessionWatchdogLifecycle:
    """Integration tests for watchdog start/stop."""

    @pytest.mark.asyncio
    async def test_watchdog_start_and_stop(self):
        """Watchdog should start and stop cleanly without errors."""
        from apps.agent.session_watchdog import SessionWatchdog

        wd = SessionWatchdog()
        with patch("apps.agent.session_watchdog._get_watchdog_redis") as mock_redis:
            mock_r = AsyncMock()
            mock_r.keys = AsyncMock(return_value=[])
            mock_r.xgroup_create = AsyncMock(return_value=True)
            mock_r.xreadgroup = AsyncMock(return_value=[])
            mock_r.setex = AsyncMock()
            mock_r.hset = AsyncMock()
            mock_r.expire = AsyncMock()
            mock_redis.return_value = mock_r

            await wd.start()
            assert wd._running is True
            assert wd._scan_task is not None

            await asyncio.sleep(0.15)

            await wd.stop()
            assert wd._running is False
