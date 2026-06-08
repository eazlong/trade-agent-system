"""Tests for ScheduledOneTimeTask model and persistence logic."""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from asgiref.sync import sync_to_async
from django.test import TestCase, TransactionTestCase
from unittest.mock import MagicMock, patch

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


class TestScheduledOneTimeTaskModel(TestCase):
    """Test ScheduledOneTimeTask model creation and fields."""

    def test_create_pending_task(self):
        """Should create a task with status=pending."""
        from apps.agent.models import ScheduledOneTimeTask

        run_at = datetime.now(timezone.utc) + timedelta(hours=1)
        task = ScheduledOneTimeTask.objects.create(
            task_name="test_reminder",
            agent_name="analyst",
            message="BTC price analysis",
            user_id="test-user-123",
            run_at=run_at,
        )
        self.assertEqual(task.status, "pending")
        self.assertIsInstance(task.id, UUID)
        self.assertEqual(task.celery_task_id, "")
        self.assertIsNone(task.executed_at)
        self.assertEqual(task.result, "")
        self.assertEqual(task.error, "")

    def test_terminal_statuses(self):
        """Should accept all valid terminal statuses."""
        from apps.agent.models import ScheduledOneTimeTask

        run_at = datetime.now(timezone.utc)
        for status in ("completed", "failed", "revoked", "missed"):
            task = ScheduledOneTimeTask.objects.create(
                task_name=f"test_{status}",
                agent_name="analyst",
                message="test",
                run_at=run_at,
                status=status,
            )
            self.assertEqual(task.status, status)


class TestExecuteScheduledTaskStateMachine(TestCase):
    """Test CAS state machine in execute_scheduled_agent_task."""

    def test_cas_marks_running_on_pending(self):
        """Pending task should be CAS'd to running when started."""
        from apps.agent.models import ScheduledOneTimeTask
        from apps.agent.tasks import _update_task_status

        run_at = datetime.now(timezone.utc)
        task = ScheduledOneTimeTask.objects.create(
            task_name="cas_test", agent_name="analyst",
            message="test", run_at=run_at,
        )
        affected = _update_task_status(str(task.id), "running")
        self.assertEqual(affected, 1)
        task.refresh_from_db()
        self.assertEqual(task.status, "running")
        self.assertIsNotNone(task.executed_at)

    def test_cas_skips_non_pending(self):
        """Non-pending task should be skipped (idempency)."""
        from apps.agent.models import ScheduledOneTimeTask
        from apps.agent.tasks import _update_task_status

        run_at = datetime.now(timezone.utc)
        task = ScheduledOneTimeTask.objects.create(
            task_name="cas_skip", agent_name="analyst",
            message="test", run_at=run_at, status="revoked",
        )
        affected = _update_task_status(str(task.id), "running")
        self.assertEqual(affected, 0)
        task.refresh_from_db()
        self.assertEqual(task.status, "revoked")

    def test_cas_concurrent_only_one_wins(self):
        """Only one concurrent CAS should succeed."""
        from apps.agent.models import ScheduledOneTimeTask
        from apps.agent.tasks import _update_task_status

        run_at = datetime.now(timezone.utc)
        task = ScheduledOneTimeTask.objects.create(
            task_name="cas_race", agent_name="analyst",
            message="test", run_at=run_at,
        )
        result1 = _update_task_status(str(task.id), "running")
        result2 = _update_task_status(str(task.id), "running")
        self.assertEqual(result1 + result2, 1)

    def test_mark_completed_on_success(self):
        """Successful task should be marked completed with result."""
        from apps.agent.models import ScheduledOneTimeTask
        from apps.agent.tasks import _finalize_task

        run_at = datetime.now(timezone.utc)
        task = ScheduledOneTimeTask.objects.create(
            task_name="finalize_ok", agent_name="analyst",
            message="test", run_at=run_at, status="running",
        )
        _finalize_task(str(task.id), "SUCCESS", result={"data": "ok"}, error=None)
        task.refresh_from_db()
        self.assertEqual(task.status, "completed")
        self.assertIn("ok", task.result)

    def test_mark_failed_on_error(self):
        """Task returning ERROR/FAILURE should be marked failed."""
        from apps.agent.models import ScheduledOneTimeTask
        from apps.agent.tasks import _finalize_task

        run_at = datetime.now(timezone.utc)
        task = ScheduledOneTimeTask.objects.create(
            task_name="finalize_fail", agent_name="analyst",
            message="test", run_at=run_at, status="running",
        )
        _finalize_task(str(task.id), "FAILURE", result=None, error="agent failed")
        task.refresh_from_db()
        self.assertEqual(task.status, "failed")
        self.assertIn("agent failed", task.error)


class TestSubmitScheduledTaskToolPersistence(TestCase):
    """Test SubmitScheduledTaskTool creates DB record and pushes to Celery."""

    @patch("apps.agent.tasks.execute_scheduled_agent_task.apply_async")
    async def test_creates_db_record_and_pushes(self, mock_apply_async):
        """Should create ScheduledOneTimeTask and call apply_async with eta."""
        from apps.agent.tools.schedule_task import SubmitScheduledTaskTool
        from apps.agent.models import ScheduledOneTimeTask
        from datetime import datetime, timezone, timedelta

        mock_result = MagicMock()
        mock_result.id = "celery-test-id-123"
        mock_apply_async.return_value = mock_result

        tool = SubmitScheduledTaskTool()
        run_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        result = await tool.execute(
            agent_name="analyst",
            message="Analyze BTC",
            run_at=run_at,
            user_id="user-456",
        )

        self.assertTrue(result.success)
        db_uuid = result.data["schedule_id"]
        task = await sync_to_async(ScheduledOneTimeTask.objects.get)(id=db_uuid)
        self.assertEqual(task.status, "pending")
        self.assertEqual(task.agent_name, "analyst")
        self.assertEqual(task.celery_task_id, "celery-test-id-123")
        mock_apply_async.assert_called_once()
        call_kwargs = mock_apply_async.call_args[1]
        self.assertIn("scheduled_task_id", call_kwargs["kwargs"])
        self.assertEqual(call_kwargs["kwargs"]["scheduled_task_id"], db_uuid)
        self.assertEqual(result.data["celery_task_id"], "celery-test-id-123")


class TestCancelScheduledTaskTool(TransactionTestCase):
    """Test CancelScheduledTaskTool with real ORM (TransactionTestCase for async DB writes)."""

    @pytest.mark.asyncio
    async def test_cancel_pending_task(self):
        """Should revoke a pending task via CAS + Celery revoke."""
        from apps.agent.models import ScheduledOneTimeTask
        from apps.agent.tools import schedule_task as st_module
        from apps.agent.tools.schedule_task import CancelScheduledTaskTool

        task_uuid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        # Create a real pending task in DB
        run_at = datetime.now(timezone.utc)
        ScheduledOneTimeTask.objects.create(
            task_name="cancel_test",
            agent_name="analyst",
            message="test",
            celery_task_id="fake-celery-id",
            run_at=run_at,
            status="pending",
            id=task_uuid,
        )

        with patch.object(st_module, "celery_app") as mock_celery:
            tool = CancelScheduledTaskTool()
            result = await tool.execute(
                task_id_or_name=task_uuid, task_type="scheduled"
            )
            mock_celery.control.revoke.assert_called_once_with(
                "fake-celery-id", terminate=True
            )

        assert result.success
        assert "CANCELLED" in result.data["status"]
        assert result.data["task_id"] == task_uuid
        # Verify DB state changed to revoked
        task = ScheduledOneTimeTask.objects.get(id=task_uuid)
        self.assertEqual(task.status, "revoked")

    @pytest.mark.asyncio
    async def test_cancel_already_completed(self):
        """Should return 'cannot cancel' for terminal tasks."""
        from apps.agent.models import ScheduledOneTimeTask
        from apps.agent.tools.schedule_task import CancelScheduledTaskTool

        task_uuid = "00000000-0000-0000-0000-000000000001"
        run_at = datetime.now(timezone.utc)
        ScheduledOneTimeTask.objects.create(
            task_name="cancel_completed",
            agent_name="analyst",
            message="test",
            run_at=run_at,
            status="completed",
            id=task_uuid,
        )

        tool = CancelScheduledTaskTool()
        result = await tool.execute(
            task_id_or_name=task_uuid, task_type="scheduled"
        )

        assert not result.success
        assert "无法取消" in result.error

    @pytest.mark.asyncio
    async def test_cancel_task_without_celery_id(self):
        """Should revoke even if celery_task_id is empty."""
        from apps.agent.models import ScheduledOneTimeTask
        from apps.agent.tools import schedule_task as st_module
        from apps.agent.tools.schedule_task import CancelScheduledTaskTool

        task_uuid = "00000000-0000-0000-0000-000000000002"
        run_at = datetime.now(timezone.utc)
        ScheduledOneTimeTask.objects.create(
            task_name="cancel_no_celery",
            agent_name="analyst",
            message="test",
            run_at=run_at,
            status="pending",
            id=task_uuid,
        )

        with patch.object(st_module, "celery_app") as mock_celery:
            tool = CancelScheduledTaskTool()
            result = await tool.execute(
                task_id_or_name=task_uuid, task_type="scheduled"
            )
            # revoke should NOT be called since celery_task_id is empty
            mock_celery.control.revoke.assert_not_called()

        assert result.success
        assert "CANCELLED" in result.data["status"]


class TestStartupRecoveryCheck(TestCase):
    """Test startup_recovery_check reconciliation logic."""

    def setUp(self):
        self.now = datetime.now(timezone.utc)

    @patch("apps.agent.tasks._acquire_recovery_lock")
    @patch("apps.agent.tasks.execute_scheduled_agent_task")
    @patch("apps.agent.tasks._notify_user")
    def test_recovery_future_requeued(self, mock_notify, mock_task, mock_lock):
        """Future pending tasks should be re-queued with eta."""
        mock_lock.return_value = "worker:12345:1.0"
        mock_task.apply_async.return_value = MagicMock(id="new-celery-id-future")
        from apps.agent.tasks import startup_recovery_check
        from apps.agent.models import ScheduledOneTimeTask

        ScheduledOneTimeTask.objects.create(
            task_name="future", agent_name="analyst",
            message="future task", run_at=self.now + timedelta(hours=1),
            status="pending", celery_task_id="old-celery-id",
        )

        result = startup_recovery_check()
        task = ScheduledOneTimeTask.objects.get(task_name="future")
        self.assertNotEqual(task.celery_task_id, "old-celery-id")
        self.assertEqual(task.status, "pending")
        call_kwargs = mock_task.apply_async.call_args[1]
        self.assertIn("eta", call_kwargs)

    @patch("apps.agent.tasks._acquire_recovery_lock")
    @patch("apps.agent.tasks.execute_scheduled_agent_task")
    @patch("apps.agent.tasks._notify_user")
    def test_recovery_grace_immediate(self, mock_notify, mock_task, mock_lock):
        """Grace-period tasks should be immediately executed."""
        mock_lock.return_value = "worker:12345:1.0"
        mock_task.apply_async.return_value = MagicMock(id="new-celery-id-grace")
        from apps.agent.tasks import startup_recovery_check
        from apps.agent.models import ScheduledOneTimeTask

        ScheduledOneTimeTask.objects.create(
            task_name="grace", agent_name="analyst",
            message="grace task", run_at=self.now - timedelta(minutes=3),
            status="pending",
        )

        result = startup_recovery_check()
        task = ScheduledOneTimeTask.objects.get(task_name="grace")
        self.assertEqual(task.status, "pending")
        mock_task.apply_async.assert_called()
        call_kwargs = mock_task.apply_async.call_args[1]
        self.assertNotIn("eta", call_kwargs)

    @patch("apps.agent.tasks._acquire_recovery_lock")
    @patch("apps.agent.tasks.execute_scheduled_agent_task")
    @patch("apps.agent.tasks._notify_user")
    def test_recovery_missed_marked(self, mock_notify, mock_task, mock_lock):
        """Beyond-grace tasks should be marked missed."""
        mock_lock.return_value = "worker:12345:1.0"
        mock_task.apply_async.return_value = MagicMock(id="new-celery-id-missed")
        from apps.agent.tasks import startup_recovery_check
        from apps.agent.models import ScheduledOneTimeTask

        ScheduledOneTimeTask.objects.create(
            task_name="missed", agent_name="analyst",
            message="missed task", run_at=self.now - timedelta(hours=2),
            status="pending", user_id="user-123",
        )

        result = startup_recovery_check()
        task = ScheduledOneTimeTask.objects.get(task_name="missed")
        self.assertEqual(task.status, "missed")
        mock_notify.assert_called()

    @patch("apps.agent.tasks._acquire_recovery_lock")
    @patch("apps.agent.tasks.execute_scheduled_agent_task")
    @patch("apps.agent.tasks._notify_user")
    def test_recovery_zombie_running(self, mock_notify, mock_task, mock_lock):
        """Stale running tasks should be recovered like pending."""
        mock_lock.return_value = "worker:12345:1.0"
        mock_task.apply_async.return_value = MagicMock(id="new-celery-id-zombie")
        from apps.agent.tasks import startup_recovery_check
        from apps.agent.models import ScheduledOneTimeTask

        zombie_task = ScheduledOneTimeTask.objects.create(
            task_name="zombie", agent_name="analyst",
            message="zombie task", run_at=self.now - timedelta(minutes=15),
            status="running",
            celery_task_id="zombie-celery-id",
        )

        result = startup_recovery_check()
        task = ScheduledOneTimeTask.objects.get(task_name="zombie")
        self.assertEqual(task.status, "pending")
        mock_task.apply_async.assert_called()
