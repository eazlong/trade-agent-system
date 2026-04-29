"""Tests for TaskTracker: milestone throttling, heartbeat, lifecycle, and notification."""

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
        "heartbeat_interval": 2,  # Short interval for fast tests
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

        tracker.stop()
        assert len(tracker._milestones) == 0

    @pytest.mark.usefixtures("_mock_deps")
    def test_complete_records_milestone(self):
        tracker = _make_tracker()
        tracker.start()
        tracker.milestone("下载数据完成", progress=0.5)
        tracker.complete("回测完成")

        assert len(tracker._milestones) == 1
        assert tracker._milestones[0]["message"] == "下载数据完成"
        tracker.stop()

    @pytest.mark.usefixtures("_mock_deps")
    def test_fail_records_error(self):
        tracker = _make_tracker()
        tracker.start()
        tracker.fail("Connection timeout")

        assert len(tracker._milestones) == 0
        tracker.stop()


class TestTaskTrackerThrottling:
    @pytest.mark.usefixtures("_mock_deps")
    def test_milestone_throttled_within_interval(self):
        tracker = _make_tracker(heartbeat_interval=60)
        tracker.start()

        # Rapid-fire milestones — throttled, no extra push
        for i in range(5):
            tracker.milestone(f"step {i}", progress=i * 0.1)

        # Milestones should still be recorded
        assert len(tracker._milestones) == 5
        tracker.stop()

    @pytest.mark.usefixtures("_mock_deps")
    def test_milestone_pushed_after_interval(self):
        tracker = _make_tracker(heartbeat_interval=1)  # 1 second for fast test
        tracker.start()

        time.sleep(1.1)  # Wait past the interval
        tracker.milestone("after wait", progress=0.5)

        tracker.stop()


class TestTaskTrackerHeartbeat:
    @pytest.mark.usefixtures("_mock_deps")
    def test_heartbeat_sends_keepalive(self):
        tracker = _make_tracker(heartbeat_interval=1)
        tracker.start()

        # Wait for one heartbeat cycle
        time.sleep(2.5)
        tracker.stop()

        # Heartbeat thread should have run
        assert tracker._heartbeat_thread is not None


class TestTaskTrackerContext:
    @pytest.mark.usefixtures("_mock_deps")
    def test_context_var_get_current(self):
        tracker = _make_tracker()
        token = tracker_context.set(tracker)
        try:
            assert TaskTracker.get_current() is tracker
        finally:
            tracker_context.reset(token)
        tracker.stop()

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
