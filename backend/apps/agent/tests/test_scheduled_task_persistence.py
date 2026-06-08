"""Tests for ScheduledOneTimeTask model and persistence logic."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from uuid import UUID

from asgiref.sync import sync_to_async
from django.test import TestCase
from unittest.mock import MagicMock, patch


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
        # Simulate concurrent CAS: only one should return 1
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
        from asgiref.sync import sync_to_async

        from apps.agent.tools.schedule_task import SubmitScheduledTaskTool
        from apps.agent.models import ScheduledOneTimeTask
        from datetime import datetime, timezone, timedelta
        from unittest.mock import MagicMock

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
