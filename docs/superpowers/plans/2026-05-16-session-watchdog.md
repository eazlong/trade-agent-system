# SessionWatchdog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build SessionWatchdog — dual-level (task + session) timed detection with auto-retry recovery to prevent silent task interruption without user reply.

**Architecture:** New `SessionWatchdog` asyncio background coroutine in ASGI lifespan scans Redis `task:progress:*` and `session:*:context` keys every 15s. Add `asyncio.wait_for(280s)` in consumer to catch LLM/tool hangs at the source. Extend existing `check_task_health` zombie handler and `nack_and_retry` to support Stream-based tasks. Modify `TaskTracker` with `alive()` and `expected_duration`.

**Tech Stack:** Python asyncio, aioredis, Celery, Redis Streams/Hashes

---

## File Structure

| File | Responsibility |
|------|---------------|
| `apps/agent/session_watchdog.py` (NEW) | SessionWatchdog class — three scan loops, task recovery, DLQ consumption |
| `apps/agent/task_tracker.py` (MODIFY) | Add `alive()`, `expected_duration` param, `last_alive` field |
| `apps/agent/bus.py` (MODIFY) | Save `original_task` to Redis Hash before DLQ in `nack_and_retry` |
| `apps/agent/consumer.py` (MODIFY) | Wrap `supervisor.handle()` in `asyncio.wait_for(280s)` |
| `apps/agent/tasks.py` (MODIFY) | Add Stream retry branch in `_handle_zombie_task` |
| `apps/agent/base.py` (MODIFY) | Call `tracker.alive()` after each tool execution in `_run_tool_loop` |
| `core/asgi.py` (MODIFY) | Start/stop Watchdog in lifespan |
| `apps/agent/tests/test_session_watchdog.py` (NEW) | Unit + integration tests |

---

### Task 1: TaskTracker — add `alive()` method and `expected_duration`

**Files:**
- Modify: `backend/apps/agent/task_tracker.py`
- Test via: existing `backend/apps/agent/tests/test_task_tracker.py`

- [ ] **Step 1: Add `expected_duration` parameter to `__init__`**

```python
# task_tracker.py — __init__ signature change
def __init__(
    self,
    task_id: str,
    user_id: str,
    channel: str = "telegram",
    task_type: str = "agent",
    heartbeat_interval: int = 60,
    original_task: dict | None = None,
    max_retries: int = 1,
    expected_duration: int = 240,  # NEW
):
    self.task_id = task_id
    self.user_id = user_id
    self.channel = channel
    self.task_type = task_type
    self.heartbeat_interval = heartbeat_interval
    self.original_task = original_task
    self.max_retries = max_retries
    self.expected_duration = expected_duration  # NEW
    self._last_push: float = 0
    self._start_ts: float = 0
    self._milestones: list[dict] = []
    self._redis_key = f"task:progress:{task_id}"
```

- [ ] **Step 2: Write `last_alive` and `expected_duration` in `start()`**

```python
# task_tracker.py — start() method, add to data dict
def start(self, initial_message: str = "任务已启动") -> None:
    self._start_ts = time.time()
    self._last_push = self._start_ts
    data: dict[str, Any] = {
        "step": initial_message,
        "progress": 0,
        "last_heartbeat": datetime.now(timezone.utc).isoformat(),
        "last_alive": datetime.now(timezone.utc).isoformat(),  # NEW
        "retry_count": 0,
        "max_retries": self.max_retries,
        "expected_duration": str(self.expected_duration),  # NEW
    }
    if self.original_task:
        data["original_task"] = json.dumps(self.original_task, ensure_ascii=False)
    self._save_redis("running", data)
    self._notify(_format_progress_message(self.task_id, initial_message))
    logger.info(
        "[TaskTracker] task %s started for user %s", self.task_id, self.user_id
    )
```

- [ ] **Step 3: Add `alive()` method after `milestone()`**

```python
# task_tracker.py — new method after milestone()
def alive(self) -> None:
    """Update last_alive timestamp (call after long tool executions)."""
    self._save_redis("running", {
        "last_alive": datetime.now(timezone.utc).isoformat(),
    })
```

- [ ] **Step 4: Run existing tests to verify no regression**

Run: `cd backend && DJANGO_SETTINGS_MODULE=core.settings.dev uv run pytest apps/agent/tests/test_task_tracker.py -v`
Expected: All existing tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/apps/agent/task_tracker.py
git commit -m "feat(task-tracker): add alive() method and expected_duration for watchdog support"
```

---

### Task 2: bus.py — save `original_task` to Redis before DLQ

**Files:**
- Modify: `backend/apps/agent/bus.py` (the `nack_and_retry` function)

- [ ] **Step 1: Read current `nack_and_retry` implementation**

Run: `grep -n "async def nack_and_retry" backend/apps/agent/bus.py`
Then read the function body to confirm the exact current code before modifying.

- [ ] **Step 2: Modify `nack_and_retry()` to save `original_task` to Redis Hash before DLQ**

In the `retry_count >= _MAX_RETRY` branch, before publishing to DLQ, save the original payload:

```python
# bus.py — inside nack_and_retry(), in the `if retry_count >= _MAX_RETRY:` block
# Add BEFORE the dlq publish:

        # Save original_task to Redis Hash so watchdog can recover it
        task_id = payload.get("task_id", "")
        if task_id:
            redis_key = f"task:progress:{task_id}"
            r = await _get_redis(stream)
            try:
                r.hset(redis_key, mapping={
                    "status": "dlq",
                    "retry_count": str(retry_count),
                    "original_task": json.dumps(payload),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                })
            finally:
                await r.aclose()
```

- [ ] **Step 3: Run existing tests to verify no regression**

Run: `cd backend && DJANGO_SETTINGS_MODULE=core.settings.dev uv run pytest apps/agent/tests/ -v -k "bus" 2>/dev/null || echo "No bus-specific tests found"`
Expected: No regressions

- [ ] **Step 4: Commit**

```bash
git add backend/apps/agent/bus.py
git commit -m "feat(bus): save original_task to Redis before DLQ for watchdog recovery"
```

---

### Task 3: SessionWatchdog — core class with three scan loops

**Files:**
- Create: `backend/apps/agent/session_watchdog.py`
- Create: `backend/apps/agent/tests/test_session_watchdog.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/apps/agent/tests/test_session_watchdog.py
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
                "original_task": json.dumps({"task_id":"task1","user_id":"user1","payload":{"text":"hello"}}),
            },
        }
        mock_r = AsyncMock()
        mock_r.hgetall = AsyncMock(side_effect=lambda k: mock_redis_data.get(k, {}))
        mock_r.scan_iter = MagicMock(return_value=["task:progress:task1"])

        with patch("apps.agent.session_watchdog._get_watchdog_redis", return_value=mock_r), \
             patch("apps.agent.session_watchdog._send_notification_redis") as mock_notify, \
             patch("apps.agent.session_watchdog.bus") as mock_bus:
            count = await wd._scan_tasks()

        assert count == 1
        mock_bus.publish.assert_called()  # should attempt recovery

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
                "original_task": json.dumps({"task_id":"task2"}),
            },
        }
        mock_r = AsyncMock()
        mock_r.hgetall = AsyncMock(side_effect=lambda k: mock_redis_data.get(k, {}))
        mock_r.scan_iter = MagicMock(return_value=["task:progress:task2"])

        with patch("apps.agent.session_watchdog._get_watchdog_redis", return_value=mock_r), \
             patch("apps.agent.session_watchdog._send_notification_redis") as mock_notify, \
             patch("apps.agent.session_watchdog.bus") as mock_bus:
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
                "original_task": json.dumps({"task_id":"task3"}),
            },
        }
        mock_r = AsyncMock()
        mock_r.hgetall = AsyncMock(side_effect=lambda k: mock_redis_data.get(k, {}))
        mock_r.scan_iter = MagicMock(return_value=["task:progress:task3"])

        with patch("apps.agent.session_watchdog._get_watchdog_redis", return_value=mock_r), \
             patch("apps.agent.session_watchdog._send_notification_redis") as mock_notify, \
             patch("apps.agent.session_watchdog.bus") as mock_bus:
            count = await wd._scan_tasks()

        assert count == 1
        mock_notify.assert_called()  # degraded notification
        mock_bus.publish.assert_not_called()  # no retry


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
        mock_r.scan_iter = MagicMock(return_value=["session:user123:context"])
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
        mock_r.scan_iter = MagicMock(return_value=["session:user456:context"])
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
                    "task_id": "task-dlq-1",
                    "user_id": "user-dlq",
                    "payload": json.dumps({"text":"hello"}),
                    "dlq_reason": "exceeded 3 retries",
                    "retry_count": "3",
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
            count = await wd._scan_dlq()

        assert count == 0
        mock_bus.publish.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && DJANGO_SETTINGS_MODULE=core.settings.dev uv run pytest apps/agent/tests/test_session_watchdog.py -v`
Expected: FAIL — module/class not found

- [ ] **Step 3: Write SessionWatchdog implementation**

```python
# backend/apps/agent/session_watchdog.py
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

WATCHDOG_SCAN_INTERVAL = 15
WATCHDOG_MAX_AUTO_RETRY = 2
WATCHDOG_RETRY_DELAYS = [10, 60]
WATCHDOG_SESSION_WARN_BEFORE = 300
TASK_EXPECTED_DURATION = 240
TASK_HARD_TIMEOUT = 280
TASK_WATCHDOG_TIMEOUT = 360


async def _get_watchdog_redis():
    import redis.asyncio as aioredis
    from django.conf import settings

    url = settings.REDIS_URL
    if url.rsplit("/", 1)[-1].isdigit():
        url = url.rsplit("/", 1)[0] + "/3"
    return aioredis.from_url(url, decode_responses=True)


def _send_notification_redis(user_id: str, text: str) -> None:
    from apps.agent.task_tracker import _send_notification_redis as _send

    _send(user_id, text)


class SessionWatchdog:
    _scan_interval: int = WATCHDOG_SCAN_INTERVAL
    _running: bool = False
    _scan_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._running = True
        self._scan_task = asyncio.create_task(self._scan_loop())
        logger.info("[SessionWatchdog] started, interval=%ds", self._scan_interval)

    async def stop(self) -> None:
        self._running = False
        if self._scan_task:
            self._scan_task.cancel()
            try:
                await self._scan_task
            except (asyncio.CancelledError, RuntimeError):
                pass
        logger.info("[SessionWatchdog] stopped")

    async def _scan_loop(self) -> None:
        while self._running:
            try:
                task_count = await self._scan_tasks()
                session_count = await self._scan_sessions()
                dlq_count = await self._scan_dlq()

                if task_count or session_count or dlq_count:
                    logger.info(
                        "[SessionWatchdog] scan: tasks=%d sessions=%d dlq=%d",
                        task_count, session_count, dlq_count,
                    )

                r = await _get_watchdog_redis()
                try:
                    await r.setex("watchdog:heartbeat", 30, datetime.now(timezone.utc).isoformat())
                    await r.hset("watchdog:last_scan", mapping={
                        "task_scan_at": datetime.now(timezone.utc).isoformat(),
                        "task_count": str(task_count),
                        "session_count": str(session_count),
                        "dlq_count": str(dlq_count),
                    })
                    await r.expire("watchdog:last_scan", 60)
                finally:
                    await r.aclose()

            except asyncio.CancelledError:
                break
            except RuntimeError:
                break
            except Exception:
                logger.error("[SessionWatchdog] scan loop error", exc_info=True)

            try:
                await asyncio.sleep(self._scan_interval)
            except (asyncio.CancelledError, RuntimeError):
                break

    async def _scan_tasks(self) -> int:
        """Scan all running tasks and recover those that have timed out."""
        from . import bus

        r = await _get_watchdog_redis()
        count = 0
        try:
            keys = list(r.scan_iter("task:progress:*"))
            for key in keys:
                data = r.hgetall(key)
                if not data or data.get("status") != "running":
                    continue

                task_id = data.get("task_id", "")
                user_id = data.get("user_id", "")
                retry_count = int(data.get("retry_count", 0))
                expected_dur = int(data.get("expected_duration", TASK_EXPECTED_DURATION))
                last_alive_str = data.get("last_alive", "")
                updated_str = data.get("updated_at", "")

                ref_str = last_alive_str or updated_str
                if not ref_str:
                    continue
                try:
                    ref_dt = datetime.fromisoformat(ref_str)
                    age = (datetime.now(timezone.utc) - ref_dt).total_seconds()
                except ValueError:
                    continue

                if age > expected_dur * 1.5:
                    count += 1
                    if retry_count < WATCHDOG_MAX_AUTO_RETRY:
                        await self._recover_task(task_id, user_id, data, retry_count, r)
                    else:
                        short_id = task_id
                        mins = int(age) // 60
                        _send_notification_redis(
                            user_id,
                            f"任务 #{short_id} 处理超时（{mins}分钟）\n"
                            f"已自动重试 {retry_count} 次，请手动重新发送指令",
                        )
        finally:
            await r.aclose()
        return count

    async def _recover_task(
        self, task_id: str, user_id: str, data: dict, retry_count: int, r
    ) -> None:
        from . import bus

        original_task_str = data.get("original_task", "")
        if not original_task_str:
            _send_notification_redis(
                user_id,
                f"任务 #{task_id} 异常中断，请重新发送指令",
            )
            r.hset(f"task:progress:{task_id}", "status", "zombie")
            return

        try:
            original_task = json.loads(original_task_str)
        except (json.JSONDecodeError, TypeError):
            _send_notification_redis(
                user_id,
                f"任务 #{task_id} 数据损坏，请重新发送指令",
            )
            return

        celery_task_name = original_task.get("celery_task", "")
        new_task_id = ""

        if celery_task_name:
            from celery_app import app
            task_args = original_task.get("args", [])
            task_kwargs = original_task.get("kwargs", {})
            result = app.send_task(celery_task_name, args=task_args, kwargs=task_kwargs)
            new_task_id = result.id
        else:
            delay = WATCHDOG_RETRY_DELAYS[min(retry_count, len(WATCHDOG_RETRY_DELAYS) - 1)]
            await asyncio.sleep(delay)
            retry_payload = dict(original_task)
            retry_payload["retry_count"] = str(retry_count + 1)
            new_msg_id = await bus.publish("agent:tasks", retry_payload)
            new_task_id = new_msg_id

        r.hset(f"task:progress:{task_id}", mapping={
            "status": "watchdog_retrying",
            "retry_count": str(retry_count + 1),
            "new_task_id": new_task_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })

        short_id = task_id
        _send_notification_redis(
            user_id,
            f"任务 #{short_id} 超时无响应\n"
            f"已自动重试（第 {retry_count + 1}/{WATCHDOG_MAX_AUTO_RETRY} 次）\n"
            f"新任务 ID: {new_task_id if new_task_id else 'N/A'}",
        )

        logger.info(
            "[SessionWatchdog] task %s auto-retried -> %s (retry %d/%d)",
            task_id, new_task_id, retry_count + 1, WATCHDOG_MAX_AUTO_RETRY,
        )

    async def _scan_sessions(self) -> int:
        """Scan sessions approaching expiry and warn users."""
        r = await _get_watchdog_redis()
        count = 0
        try:
            keys = list(r.scan_iter("session:*:context"))
            for key in keys:
                try:
                    data = json.loads(r.get(key) or "{}")
                except (json.JSONDecodeError, TypeError):
                    continue
                if not data or data.get("state") != "multi_turn":
                    continue
                expires_at = data.get("expires_at", 0)
                remaining = expires_at - time.time()
                if 0 < remaining < WATCHDOG_SESSION_WARN_BEFORE:
                    user_id = key.split(":")[1]
                    _send_notification_redis(
                        user_id,
                        f"会话即将过期（{int(remaining)}秒）\n请继续对话以保持会话",
                    )
                    count += 1
        finally:
            await r.aclose()
        return count

    async def _scan_dlq(self) -> int:
        """Consume DLQ messages and republish to main stream."""
        from . import bus

        r = await _get_watchdog_redis()
        count = 0
        try:
            try:
                await r.xgroup_create("agent:tasks:dlq", "agents", id="0", mkstream=True)
            except Exception as e:
                if "BUSYGROUP" not in str(e):
                    raise

            results = await r.xreadgroup(
                groupname="agents",
                consumername="watchdog-dlq",
                streams={"agent:tasks:dlq": ">"},
                count=4,
                block=1000,
            )

            if not results:
                return 0

            for _stream, entries in results:
                for msg_id, fields in entries:
                    decoded = {}
                    for k, v in fields.items():
                        try:
                            decoded[k] = json.loads(v)
                        except (json.JSONDecodeError, TypeError):
                            decoded[k] = v

                    original_payload = dict(decoded)
                    original_payload.pop("dlq_reason", None)
                    original_payload.pop("original_stream", None)
                    original_payload["retry_count"] = "0"
                    original_payload["dlq_recovered"] = "true"

                    await bus.publish("agent:tasks", original_payload)
                    await r.xack("agent:tasks:dlq", "agents", msg_id)

                    user_id = decoded.get("user_id", "")
                    if user_id:
                        _send_notification_redis(
                            user_id,
                            "之前的任务已从死信队列恢复，正在重新处理...",
                        )
                    count += 1
                    logger.info("[SessionWatchdog] DLQ message %s recovered", msg_id)
        finally:
            await r.aclose()
        return count
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && DJANGO_SETTINGS_MODULE=core.settings.dev uv run pytest apps/agent/tests/test_session_watchdog.py -v`
Expected: 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/apps/agent/session_watchdog.py backend/apps/agent/tests/test_session_watchdog.py
git commit -m "feat(watchdog): add SessionWatchdog with task/session/DLQ scan loops"
```

---

### Task 4: consumer.py — add `asyncio.wait_for(280s)` timeout guard

**Files:**
- Modify: `backend/apps/agent/consumer.py:97-147` (`_dispatch` method)

- [ ] **Step 1: Wrap `supervisor.handle()` in `asyncio.wait_for` with 280s timeout**

Replace the `_dispatch` method:

```python
# consumer.py — _dispatch() method
async def _dispatch(self, fields: dict) -> str:
    import json
    from .base import AgentMessage
    from .supervisor import SupervisorAgent
    from .task_tracker import TaskTracker, tracker_context

    payload_raw = fields.get("payload", "{}")
    payload = (
        json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
    )

    user_id = fields.get("user_id", "")
    task_id = fields.get("task_id", "")
    text = payload.get("text", "")

    tracker = TaskTracker(
        task_id=task_id,
        user_id=user_id,
        task_type="agent",
    )
    token = tracker_context.set(tracker)
    try:
        tracker.start(f"收到指令：{text[:80]}")

        msg = AgentMessage(
            sender="channel",
            recipient="supervisor",
            user_id=user_id,
            payload=payload,
            intent=payload.get("intent"),
        )
        supervisor = SupervisorAgent.get_instance()

        # Hard timeout: 280s prevents LLM/tool hangs at source
        result = await asyncio.wait_for(
            supervisor.handle(msg),
            timeout=280,
        )

        if result.success:
            result_text = ""
            if isinstance(result.data, dict) and "content" in result.data:
                result_text = result.data["content"]
            else:
                result_text = str(result.data)
            tracker.complete(result_text[:300])
            return result_text
        else:
            tracker.fail(result.error)
            return f"[错误] {result.error}"
    except asyncio.TimeoutError:
        tracker.fail("处理超时（280秒），任务已自动重试")
        raise  # Let _handle() catch and call nack_and_retry
    except Exception as e:
        tracker.fail(str(e))
        raise
    finally:
        tracker_context.reset(token)
```

- [ ] **Step 2: Add `TimeoutError` catch in `_handle()` method**

In `_handle()` (consumer.py:76-95), add a specific handler before the general `except Exception`:

```python
# consumer.py — in _handle(), add after existing except blocks:
    except asyncio.TimeoutError:
        logger.warning(
            "[AgentTaskConsumer] timeout task_id=%s, retrying", task_id
        )
        await bus.ack(AGENT_TASKS, CG_AGENTS, msg_id)
        await bus.nack_and_retry(AGENT_TASKS, fields, retry_count)
```

- [ ] **Step 3: Run tests to verify no regression**

Run: `cd backend && DJANGO_SETTINGS_MODULE=core.settings.dev uv run pytest apps/agent/tests/ -v`
Expected: No regressions

- [ ] **Step 4: Commit**

```bash
git add backend/apps/agent/consumer.py
git commit -m "feat(consumer): add asyncio.wait_for(280s) timeout guard in _dispatch"
```

---

### Task 5: tasks.py — add Stream task retry branch in `_handle_zombie_task`

**Files:**
- Modify: `backend/apps/agent/tasks.py:308-405` (`_handle_zombie_task`)

- [ ] **Step 1: Add Stream retry branch when `original_task` has no `celery_task` field**

In `_handle_zombie_task`, the current code only handles Celery tasks. Add a Stream branch:

```python
# tasks.py — in _handle_zombie_task(), inside the if retry_count < max_retries block:
            original_task = json.loads(original_task_str)
            celery_task = original_task.get("celery_task", "")

            if celery_task:
                # Celery task — resend via app.send_task (existing logic)
                task_args = original_task.get("args", [])
                task_kwargs = original_task.get("kwargs", {})
                new_result = app.send_task(celery_task, args=task_args, kwargs=task_kwargs)
                new_task_id = new_result.id
            else:
                # Stream task — republish to agent:tasks
                from apps.agent import bus as agent_bus
                retry_payload = dict(original_task)
                retry_payload["retry_count"] = str(retry_count + 1)

                async def _republish():
                    return await agent_bus.publish("agent:tasks", retry_payload)

                loop = asyncio.new_event_loop()
                try:
                    new_task_id = loop.run_until_complete(_republish())
                finally:
                    loop.close()
```

- [ ] **Step 2: Verify no regression**

Run: `cd backend && DJANGO_SETTINGS_MODULE=core.settings.dev uv run pytest apps/agent/tests/ -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add backend/apps/agent/tasks.py
git commit -m "fix(tasks): add Stream task retry branch in _handle_zombie_task"
```

---

### Task 6: base.py — call `tracker.alive()` after each tool execution

**Files:**
- Modify: `backend/apps/agent/base.py:176-263` (`_run_tool_loop`)

- [ ] **Step 1: Add `tracker.alive()` call after each tool execution in `_run_tool_loop`**

After the `for tc in tool_calls` loop that calls `_execute_tool_call`, add:

```python
# base.py — in _run_tool_loop, after the tool execution loop (~line 233)
            # Update last_alive after tool execution so watchdog knows task is alive
            from .task_tracker import tracker_context
            tracker = tracker_context.get(None)
            if tracker is not None:
                tracker.alive()
```

This goes right after `messages.extend(tool_results)` and before the next loop iteration.

- [ ] **Step 2: Run tests to verify no regression**

Run: `cd backend && DJANGO_SETTINGS_MODULE=core.settings.dev uv run pytest apps/agent/tests/ -v`
Expected: All existing tests PASS

- [ ] **Step 3: Commit**

```bash
git add backend/apps/agent/base.py
git commit -m "feat(base): call tracker.alive() after tool execution in _run_tool_loop"
```

---

### Task 7: ASGI lifespan — start/stop SessionWatchdog

**Files:**
- Modify: `backend/core/asgi.py` (`LifespanHandler` class)

- [ ] **Step 1: Add global watchdog variable and integrate into lifespan**

```python
# core/asgi.py — add global near line 24:
_watchdog = None

# In LifespanHandler.__call__, startup section (after _consumer.start()):
                        from apps.agent.session_watchdog import SessionWatchdog

                        _watchdog = SessionWatchdog()
                        await _watchdog.start()
                        logger.info("[ASGI] SessionWatchdog started")

# In shutdown section (before _consumer.stop()):
                        if _watchdog:
                            await _watchdog.stop()
                            logger.info("[ASGI] SessionWatchdog stopped")
```

- [ ] **Step 2: Verify startup/shutdown order**

Startup: Consumer → Watchdog → Telegram → Progress Listener
Shutdown: Progress Listener → Telegram → Watchdog → Consumer

- [ ] **Step 3: Commit**

```bash
git add backend/core/asgi.py
git commit -m "feat(asgi): start/stop SessionWatchdog in lifespan handler"
```

---

### Task 8: Integration tests

**Files:**
- Modify: `backend/apps/agent/tests/test_session_watchdog.py` (append)

- [ ] **Step 1: Write integration tests for watchdog lifecycle and recovery**

```python
# Append to test_session_watchdog.py


class TestSessionWatchdogLifecycle:
    """Integration tests for watchdog start/stop."""

    @pytest.mark.asyncio
    async def test_watchdog_start_and_stop(self):
        """Watchdog should start and stop cleanly without errors."""
        from apps.agent.session_watchdog import SessionWatchdog

        wd = SessionWatchdog()
        await wd.start()
        assert wd._running is True
        assert wd._scan_task is not None

        await asyncio.sleep(0.1)

        await wd.stop()
        assert wd._running is False


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

        with patch("apps.agent.session_watchdog.bus") as mock_bus:
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
            await wd._recover_task("task-y", "user-y", data, 0, mock_r)

        mock_bus.publish.assert_not_called()
        mock_r.hset.assert_called_with("task:progress:task-y", "status", "zombie")
        mock_notify.assert_called()
```

- [ ] **Step 2: Run all watchdog tests**

Run: `cd backend && DJANGO_SETTINGS_MODULE=core.settings.dev uv run pytest apps/agent/tests/test_session_watchdog.py -v`
Expected: 10 tests PASS

- [ ] **Step 3: Run full agent test suite**

Run: `cd backend && DJANGO_SETTINGS_MODULE=core.settings.dev uv run pytest apps/agent/tests/ -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add backend/apps/agent/tests/test_session_watchdog.py
git commit -m "test(watchdog): add integration tests for watchdog lifecycle and recovery"
```

---

## Self-Review

1. **Spec coverage:** All 4 scenarios — LLM timeout (Task 4), tool stuck (Tasks 4+6), consumer crash (Tasks 3+5), session expiry (Task 3)
2. **No placeholders:** All code is concrete, no TBD/TODO
3. **Type consistency:** `expected_duration` str in Redis/int in Python; `last_alive` ISO8601 str; `retry_count` str in Hash; `original_task` JSON str
4. **File structure matches spec:** 7 modified + 2 new files per spec
