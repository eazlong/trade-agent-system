"""
Celery signals for automatic TaskTracker lifecycle management.

These signals auto-start/stop trackers for Celery tasks,
so task code only needs to call tracker.milestone() for intermediate updates.
"""

from __future__ import annotations

import logging

from celery.signals import task_failure

logger = logging.getLogger(__name__)


# 不需要 TaskTracker 追踪的基础设施任务
_SKIP_TRACKER_TASKS = frozenset(
    {
        "apps.agent.tasks.archive_task_progress",
        "apps.agent.tasks.check_task_health",
        "apps.agent.tasks.check_session_expiry",
    }
)


@task_failure.connect
def _on_task_failure(sender=None, task_id=None, exception=None, **_kwargs):
    """Ensure tracker records failure even if postrun didn't fire."""
    from apps.agent.task_tracker import tracker_context

    tracker = tracker_context.get()
    if tracker is None:
        return

    try:
        tracker.fail(str(exception))
    except Exception:
        pass
    finally:
        tracker_context.set(None)
