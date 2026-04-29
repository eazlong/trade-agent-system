"""
Unified task progress tracker for long-running async tasks.

Supports Celery tasks and Redis Stream Agent tasks with:
- Milestone notifications (key stages, throttled)
- Heartbeat keepalive (auto background thread)
- Redis Hash for running state + PG archive for completed tasks
"""

from __future__ import annotations

import json
import logging
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

tracker_context: ContextVar[TaskTracker | None] = ContextVar("tracker", default=None)

# ---------------------------------------------------------------------------
# Notification bridge — Celery worker sends via Redis pubsub, ASGI consumer pushes to Telegram
# ---------------------------------------------------------------------------
_PROGRESS_CHANNEL = "task:progress:notifications"


def _send_notification_redis(user_id: str, text: str) -> None:
    """Publish notification request to Redis pubsub for ASGI to consume."""
    import redis

    from django.conf import settings

    url = settings.REDIS_URL
    if url.rsplit("/", 1)[-1].isdigit():
        url = url.rsplit("/", 1)[0] + "/3"

    try:
        r = redis.from_url(url, decode_responses=True)
        r.publish(
            _PROGRESS_CHANNEL,
            json.dumps(
                {
                    "user_id": user_id,
                    "text": text,
                    "at": datetime.now(timezone.utc).isoformat(),
                }
            ),
        )
    except Exception:
        logger.warning(
            "[TaskTracker] failed to publish notification to Redis", exc_info=True
        )


def _format_progress_message(
    task_id: str, message: str, progress: float | None = None, elapsed: str = ""
) -> str:
    short_id = task_id[:8]
    parts = [f"\U0001f4ca 任务 #{short_id}", f"阶段：{message}"]
    if progress is not None:
        parts.append(f"进度：{int(progress * 100)}%")
    if elapsed:
        parts.append(f"耗时：{elapsed}")
    return "\n".join(parts)


def _format_duration(start_ts: float) -> str:
    secs = int(time.time() - start_ts)
    if secs < 60:
        return f"{secs}秒"
    mins = secs // 60
    secs = secs % 60
    return f"{mins}分{secs}秒" if secs else f"{mins}分钟"


# ---------------------------------------------------------------------------
# TaskTracker
# ---------------------------------------------------------------------------


class TaskTracker:
    """Track long-running task progress and notify users via Telegram."""

    def __init__(
        self,
        task_id: str,
        user_id: str,
        channel: str = "telegram",
        task_type: str = "agent",
        heartbeat_interval: int = 120,
    ):
        self.task_id = task_id
        self.user_id = user_id
        self.channel = channel
        self.task_type = task_type
        self.heartbeat_interval = heartbeat_interval
        self._last_push: float = 0
        self._start_ts: float = 0
        self._milestones: list[dict] = []
        self._stop_event = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self._redis_key = f"task:progress:{task_id}"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, initial_message: str = "任务已启动") -> None:
        """Mark task as started: write Redis, push notification, start heartbeat."""
        self._start_ts = time.time()
        self._last_push = self._start_ts
        self._save_redis("running", {"step": initial_message, "progress": 0})
        self._notify(_format_progress_message(self.task_id, initial_message))
        self._start_heartbeat()
        logger.info(
            "[TaskTracker] task %s started for user %s", self.task_id, self.user_id
        )

    def milestone(self, message: str, progress: float | None = None) -> None:
        """Record a milestone. Pushes immediately if throttle allows, else just logs to Redis."""
        now = time.time()
        data: dict[str, Any] = {"step": message}
        if progress is not None:
            data["progress"] = progress

        self._milestones.append(
            {
                "time": datetime.now(timezone.utc).isoformat(),
                "message": message,
                "progress": progress,
            }
        )
        self._save_redis("running", data)

        if now - self._last_push >= self.heartbeat_interval:
            elapsed = _format_duration(self._start_ts)
            self._notify(
                _format_progress_message(self.task_id, message, progress, elapsed)
            )
            self._last_push = now
        else:
            logger.debug("[TaskTracker] milestone throttled: %s", message)

    def heartbeat(self) -> None:
        """Manual heartbeat tick (usually auto-run by background thread)."""
        self._save_redis(
            "running", {"last_heartbeat": datetime.now(timezone.utc).isoformat()}
        )

    def complete(self, result: str) -> None:
        """Mark task completed: push final result, update Redis, trigger async archive."""
        self._stop_event.set()
        elapsed = _format_duration(self._start_ts)
        short_id = self.task_id[:8]
        self._notify(f"✅ 任务 #{short_id} 完成\n耗时：{elapsed}\n结果：{result[:300]}")
        self._save_redis(
            "completed",
            {
                "result": result,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "progress": 1.0,
                "milestones": json.dumps(self._milestones, ensure_ascii=False),
            },
        )
        self._trigger_archive(result)
        logger.info("[TaskTracker] task %s completed", self.task_id)

    def fail(self, error: str) -> None:
        """Mark task failed: push error, update Redis, archive."""
        self._stop_event.set()
        short_id = self.task_id[:8]
        self._notify(f"❌ 任务 #{short_id} 失败\n错误：{error[:300]}")
        self._save_redis(
            "failed",
            {
                "result": f"ERROR: {error}",
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "milestones": json.dumps(self._milestones, ensure_ascii=False),
            },
        )
        self._trigger_archive(f"ERROR: {error}")
        logger.error("[TaskTracker] task %s failed: %s", self.task_id, error)

    def stop(self) -> None:
        """Stop heartbeat thread. Called in finally block."""
        self._stop_event.set()
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            self._heartbeat_thread.join(timeout=2)

    @classmethod
    def get_current(cls) -> TaskTracker | None:
        """Get tracker from current context (for use in nested helper functions)."""
        return tracker_context.get()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _save_redis(self, status: str, data: dict) -> None:
        """Write progress to Redis Hash with TTL."""
        import redis

        from django.conf import settings

        url = settings.REDIS_URL
        if url.rsplit("/", 1)[-1].isdigit():
            url = url.rsplit("/", 1)[0] + "/3"

        try:
            r = redis.from_url(url, decode_responses=True)
            pipe = r.pipeline()
            pipe.hset(
                self._redis_key,
                mapping={
                    "task_id": self.task_id,
                    "user_id": self.user_id,
                    "channel": self.channel,
                    "task_type": self.task_type,
                    "status": status,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    **data,
                },
            )
            pipe.expire(self._redis_key, 86400)  # 24h TTL
            pipe.execute()
        except Exception:
            logger.warning(
                "[TaskTracker] failed to write Redis progress", exc_info=True
            )

    def _notify(self, text: str) -> None:
        """Send notification to user via Redis pubsub (consumed by ASGI process)."""
        try:
            _send_notification_redis(self.user_id, text)
        except Exception:
            logger.warning("[TaskTracker] notification delivery failed", exc_info=True)

    def _start_heartbeat(self) -> None:
        """Start daemon heartbeat thread."""

        def _tick():
            while not self._stop_event.is_set():
                waited = self._stop_event.wait(self.heartbeat_interval)
                if waited:  # stop event was set
                    break
                if time.time() - self._last_push >= self.heartbeat_interval:
                    elapsed = _format_duration(self._start_ts)
                    short_id = self.task_id[:8]
                    self._notify(
                        f"⏳ 任务 #{short_id} 仍在运行中...\n已运行：{elapsed}"
                    )
                    self._last_push = time.time()
                    self._save_redis(
                        "running",
                        {"last_heartbeat": datetime.now(timezone.utc).isoformat()},
                    )

        self._heartbeat_thread = threading.Thread(
            target=_tick, daemon=True, name=f"tracker-hb-{self.task_id[:8]}"
        )
        self._heartbeat_thread.start()

    def _trigger_archive(self, result: str) -> None:
        """Dispatch Celery task to archive this tracker's data to PostgreSQL."""
        try:
            from apps.agent.tasks import archive_task_progress

            archive_task_progress.delay(
                task_id=self.task_id,
                user_id=self.user_id,
                task_type=self.task_type,
                status=self._get_current_status(),
                progress=self._get_current_progress(),
                milestones=self._milestones,
                result=result,
            )
        except Exception:
            logger.warning(
                "[TaskTracker] failed to trigger archive task", exc_info=True
            )

    def _get_current_status(self) -> str:
        try:
            import redis
            from django.conf import settings

            url = settings.REDIS_URL
            if url.rsplit("/", 1)[-1].isdigit():
                url = url.rsplit("/", 1)[0] + "/3"
            r = redis.from_url(url, decode_responses=True)
            return r.hget(self._redis_key, "status") or "unknown"
        except Exception:
            return "unknown"

    def _get_current_progress(self) -> float:
        try:
            import redis
            from django.conf import settings

            url = settings.REDIS_URL
            if url.rsplit("/", 1)[-1].isdigit():
                url = url.rsplit("/", 1)[0] + "/3"
            r = redis.from_url(url, decode_responses=True)
            val = r.hget(self._redis_key, "progress")
            return float(val) if val else 0.0
        except Exception:
            return 0.0


# ---------------------------------------------------------------------------
# Context manager for clean setup/teardown
# ---------------------------------------------------------------------------


@contextmanager
def track_task(
    task_id: str,
    user_id: str,
    task_type: str = "agent",
    initial_message: str = "任务已启动",
):
    """Context manager that creates tracker, sets context, and cleans up."""
    tracker = TaskTracker(task_id=task_id, user_id=user_id, task_type=task_type)
    token = tracker_context.set(tracker)
    tracker.start(initial_message)
    try:
        yield tracker
    except Exception as e:
        tracker.fail(str(e))
        raise
    else:
        # Caller should call tracker.complete() explicitly with result
        pass
    finally:
        tracker.stop()
        tracker_context.reset(token)
