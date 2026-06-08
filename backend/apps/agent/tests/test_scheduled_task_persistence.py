"""Tests for ScheduledOneTimeTask model and persistence logic."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from uuid import UUID

from django.test import TestCase


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
