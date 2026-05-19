"""
Celery signals for automatic TaskTracker lifecycle management.

These signals auto-start/stop trackers for Celery tasks,
so task code only needs to call tracker.milestone() for intermediate updates.
"""

from __future__ import annotations

import logging

from celery.signals import task_failure, task_postrun, task_prerun

logger = logging.getLogger(__name__)


# 不需要 TaskTracker 追踪的基础设施任务
_SKIP_TRACKER_TASKS = frozenset(
    {
        "apps.agent.tasks.archive_task_progress",
        "apps.agent.tasks.check_task_health",
        "apps.agent.tasks.check_session_expiry",
    }
)


@task_prerun.connect
def _on_task_prerun(sender=None, task_id=None, args=None, kwargs=None, **_kwargs):
    """Auto-create tracker if the task provides user_id."""
    from apps.agent.task_tracker import TaskTracker, tracker_context

    if tracker_context.get() is not None:
        return  # Already has a tracker (manually created)

    task_name = sender.name if sender else ""
    if task_name in _SKIP_TRACKER_TASKS:
        return

    user_id = kwargs.get("user_id") if kwargs else None
    if not user_id:
        return

    task_type = kwargs.get("task_type", "agent")
    strategy_name = kwargs.get("strategy_name")
    if strategy_name:
        task_type = "backtest"

    # Capture original task params for auto-retry on zombie detection
    original_task = {
        "celery_task": sender.name if sender else "",
        "args": list(args) if args else [],
        "kwargs": dict(kwargs) if kwargs else {},
    }

    tracker = TaskTracker(
        task_id=task_id,
        user_id=user_id,
        task_type=task_type,
        original_task=original_task,
    )
    tracker_context.set(tracker)
    tracker.start()
    logger.info("[task_tracker] auto-started for task %s user %s", task_id, user_id)


@task_postrun.connect
def _on_task_postrun(sender=None, task_id=None, retval=None, state=None, **_kwargs):
    """Auto-complete or fail the tracker when task ends."""
    from apps.agent.task_tracker import tracker_context

    tracker = tracker_context.get()
    if tracker is None:
        return

    try:
        if state == "SUCCESS":
            result_str = str(retval)[:500] if retval else "完成"
            tracker.complete(result_str)
        elif state in ("FAILURE", "REJECTED"):
            error_msg = str(retval) if retval else "未知错误"
            tracker.fail(error_msg)
    except Exception:
        logger.warning(
            "[task_tracker] failed to finalize task %s", task_id, exc_info=True
        )
    finally:
        tracker_context.set(None)


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
