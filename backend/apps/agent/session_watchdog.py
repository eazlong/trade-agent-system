from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

from . import bus

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
        r = await _get_watchdog_redis()
        count = 0
        try:
            keys = await r.keys("task:progress:*")
            for key in keys:
                data = await r.hgetall(key)
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
                        mins = int(age) // 60
                        _send_notification_redis(
                            user_id,
                            f"任务 #{task_id[:8]} 处理超时（{mins}分钟）\n"
                            f"已自动重试 {retry_count} 次，请手动重新发送指令",
                        )
        finally:
            await r.aclose()
        return count

    async def _recover_task(
        self, task_id: str, user_id: str, data: dict, retry_count: int, r
    ) -> None:
        original_task_str = data.get("original_task", "")
        if not original_task_str:
            _send_notification_redis(
                user_id,
                f"任务 #{task_id[:8]} 异常中断，请重新发送指令",
            )
            await r.hset(f"task:progress:{task_id}", "status", "zombie")
            return

        try:
            original_task = json.loads(original_task_str)
        except (json.JSONDecodeError, TypeError):
            _send_notification_redis(
                user_id,
                f"任务 #{task_id[:8]} 数据损坏，请重新发送指令",
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

        await r.hset(f"task:progress:{task_id}", mapping={
            "status": "watchdog_retrying",
            "retry_count": str(retry_count + 1),
            "new_task_id": new_task_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })

        _send_notification_redis(
            user_id,
            f"任务 #{task_id[:8]} 超时无响应\n"
            f"已自动重试（第 {retry_count + 1}/{WATCHDOG_MAX_AUTO_RETRY} 次）\n"
            f"新任务 ID: {new_task_id[:8] if new_task_id else 'N/A'}",
        )

        logger.info(
            "[SessionWatchdog] task %s auto-retried -> %s (retry %d/%d)",
            task_id, new_task_id, retry_count + 1, WATCHDOG_MAX_AUTO_RETRY,
        )

    async def _scan_sessions(self) -> int:
        r = await _get_watchdog_redis()
        count = 0
        try:
            keys = await r.keys("session:*:context")
            for key in keys:
                raw = await r.get(key)
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
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
