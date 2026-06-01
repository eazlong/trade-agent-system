"""Tests for TaskTracker: milestone throttling, lifecycle, notification, and retry fields."""

from __future__ import annotations

import time
import uuid
from unittest.mock import MagicMock, patch

import pytest

from apps.agent.task_tracker import TaskTracker, tracker_context

REDIS_PATCH = "redis.from_url"
NOTIFY_PATCH = "apps.agent.task_tracker._send_notification_redis"


def _make_tracker(**kwargs):
    defaults = {
        "task_id": str(uuid.uuid4()),
        "user_id": "test-user",
    }
    defaults.update(kwargs)
    return TaskTracker(**defaults)


@pytest.fixture(autouse=True)
def _clear_context():
    """Reset context before and after each test."""
    tracker_context.set(None)
    yield
    tracker_context.set(None)


@pytest.fixture()
def _mock_deps():
    """Mock Redis and notification."""
    mock_r = MagicMock()
    mock_r.pipeline.return_value = MagicMock()
    with patch(REDIS_PATCH, return_value=mock_r), patch(NOTIFY_PATCH):
        yield mock_r


class TestTaskTrackerLifecycle:
    @pytest.mark.usefixtures("_mock_deps")
    def test_start_writes_redis_and_notifies(self):
        tracker = _make_tracker()
        tracker.start()
        assert len(tracker._milestones) == 0

    @pytest.mark.usefixtures("_mock_deps")
    def test_complete_records_milestone(self):
        tracker = _make_tracker()
        tracker.start()
        tracker.milestone("下载数据完成", progress=0.5)
        tracker.complete("回测完成")

        assert len(tracker._milestones) == 1
        assert tracker._milestones[0]["message"] == "下载数据完成"

    @pytest.mark.usefixtures("_mock_deps")
    def test_fail_records_error(self):
        tracker = _make_tracker()
        tracker.start()
        tracker.fail("Connection timeout")
        assert len(tracker._milestones) == 0

    @pytest.mark.usefixtures("_mock_deps")
    def test_start_writes_retry_fields(self):
        """start() should write retry_count=0 and max_retries to Redis."""
        tracker = _make_tracker(max_retries=3)
        tracker.start()
        assert tracker.max_retries == 3

    @pytest.mark.usefixtures("_mock_deps")
    def test_start_stores_original_task(self):
        """start() should store original_task in Redis."""
        original = {"celery_task": "some.task", "args": [], "kwargs": {"user_id": "u1"}}
        tracker = _make_tracker(original_task=original)
        tracker.start()
        assert tracker.original_task == original


class TestTaskTrackerThrottling:
    @pytest.mark.usefixtures("_mock_deps")
    def test_milestone_throttled_within_interval(self):
        tracker = _make_tracker(heartbeat_interval=60)
        tracker.start()

        for i in range(5):
            tracker.milestone(f"step {i}", progress=i * 0.1)

        assert len(tracker._milestones) == 5

    @pytest.mark.usefixtures("_mock_deps")
    def test_milestone_pushed_after_interval(self):
        tracker = _make_tracker(heartbeat_interval=1)
        tracker.start()

        time.sleep(1.1)
        tracker.milestone("after wait", progress=0.5)


class TestTaskTrackerContext:
    @pytest.mark.usefixtures("_mock_deps")
    def test_context_var_get_current(self):
        tracker = _make_tracker()
        token = tracker_context.set(tracker)
        try:
            assert TaskTracker.get_current() is tracker
        finally:
            tracker_context.reset(token)

    def test_get_current_returns_none_without_tracker(self):
        assert TaskTracker.get_current() is None


class TestTaskTrackerFormat:
    def test_format_progress_message(self):
        from apps.agent.task_tracker import _format_progress_message

        msg = _format_progress_message(
            "abc12345-xxxx", "下载数据", progress=0.3, elapsed="2分钟"
        )
        assert "abc12345" in msg
        assert "下载数据" in msg
        assert "30%" in msg
        assert "2分钟" in msg

    def test_format_duration(self):
        from apps.agent.task_tracker import _format_duration

        now = time.time()
        assert _format_duration(now) == "0秒"


class TestTaskTrackerToolCall:
    """Tests for tool_call notification feature."""

    @pytest.mark.usefixtures("_mock_deps")
    def test_tool_call_basic(self):
        """Test basic tool_call notification with arguments."""
        tracker = _make_tracker(channel="telegram")
        tracker.start("任务启动")

        # Mock _notify to capture notification text
        with patch.object(tracker, '_notify') as mock_notify:
            tracker.tool_call("get_kline_data", {"symbol": "BTCUSDT", "interval": "1h"})

            # Verify _notify was called
            mock_notify.assert_called_once()

            # Verify notification format
            notification_text = mock_notify.call_args[0][0]
            assert "🔧 执行工具：get_kline_data" in notification_text
            assert "symbol='BTCUSDT'" in notification_text
            assert "interval='1h'" in notification_text
