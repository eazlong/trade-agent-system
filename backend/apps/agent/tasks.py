"""Celery tasks for scheduled agent task execution, task progress archival, and health checks."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

from celery_app import app

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Health check constants
# ---------------------------------------------------------------------------
_HEARTBEAT_INTERVAL = 60  # seconds — push heartbeat if last_hb older than this
_ZOMBIE_TIMEOUT = 300  # seconds — mark as zombie if no update for this long


@app.task(bind=True, acks_late=True, track_started=True)
def execute_scheduled_agent_task(
    self,
    agent_name: str,
    message: str,
    user_id: str = "",
) -> dict:
    """
    在指定时间执行 Agent 任务（一次性定时任务）。

    由 SubmitScheduledTaskTool 通过 apply_async(eta=...) 触发。
    任务到达指定时间后，路由到目标 SubAgent 执行。

    Args:
        agent_name: 目标 Agent 名称（如 analyst、quant、researcher）
        message: 任务消息内容
        user_id: 用户 ID（可选）

    Returns:
        Agent 执行结果
    """
    from apps.agent.base import AgentMessage, AgentResult
    from apps.agent.supervisor import SupervisorAgent

    logger.info(
        "[execute_scheduled_agent_task] agent=%s user_id=%s",
        agent_name,
        user_id,
    )

    msg = AgentMessage(
        sender="scheduler",
        recipient=agent_name,
        payload={"text": message, "scheduled": True},
        user_id=user_id,
    )

    try:
        supervisor = SupervisorAgent.get_instance()
        loop = asyncio.new_event_loop()
        try:
            result: AgentResult = loop.run_until_complete(
                supervisor._route_to_agent(agent_name, msg)
            )
        finally:
            loop.close()

        if result.success:
            logger.info(
                "[execute_scheduled_agent_task] agent=%s completed, data=%s",
                agent_name,
                str(result.data)[:200],
            )
            return {
                "status": "SUCCESS",
                "agent_name": agent_name,
                "result": str(result.data),
            }
        else:
            logger.error(
                "[execute_scheduled_agent_task] agent=%s failed: %s",
                agent_name,
                result.error,
            )
            return {
                "status": "FAILURE",
                "agent_name": agent_name,
                "error": result.error,
            }
    except Exception as e:
        logger.error(
            "[execute_scheduled_agent_task] error: %s",
            e,
            exc_info=True,
        )
        return {
            "status": "ERROR",
            "agent_name": agent_name,
            "error": str(e),
        }


@app.task(bind=True, acks_late=True, track_started=True)
def execute_recurring_agent_task(
    self,
    agent_name: str,
    message: str,
    user_id: str = "",
    task_name: str = "",
) -> dict:
    """
    周期性执行 Agent 任务（Celery beat 调度）。

    由 SubmitRecurringTaskTool 动态注册到 beat_schedule。

    Args:
        agent_name: 目标 Agent 名称
        message: 任务消息内容
        user_id: 用户 ID（可选）
        task_name: 定时任务名称

    Returns:
        Agent 执行结果
    """
    from apps.agent.base import AgentMessage, AgentResult
    from apps.agent.supervisor import SupervisorAgent

    logger.info(
        "[execute_recurring_agent_task] task_name=%s agent=%s user_id=%s",
        task_name,
        agent_name,
        user_id,
    )

    msg = AgentMessage(
        sender="scheduler",
        recipient=agent_name,
        payload={"text": message, "scheduled": True, "task_name": task_name},
        user_id=user_id,
    )

    try:
        supervisor = SupervisorAgent.get_instance()
        loop = asyncio.new_event_loop()
        try:
            result: AgentResult = loop.run_until_complete(
                supervisor._route_to_agent(agent_name, msg)
            )
        finally:
            loop.close()

        if result.success:
            logger.info(
                "[execute_recurring_agent_task] task_name=%s agent=%s completed",
                task_name,
                agent_name,
            )
            return {
                "status": "SUCCESS",
                "task_name": task_name,
                "agent_name": agent_name,
                "result": str(result.data),
            }
        else:
            logger.error(
                "[execute_recurring_agent_task] task_name=%s agent=%s failed: %s",
                task_name,
                agent_name,
                result.error,
            )
            return {
                "status": "FAILURE",
                "task_name": task_name,
                "agent_name": agent_name,
                "error": result.error,
            }
    except Exception as e:
        logger.error(
            "[execute_recurring_agent_task] error: %s",
            e,
            exc_info=True,
        )
        return {
            "status": "ERROR",
            "task_name": task_name,
            "agent_name": agent_name,
            "error": str(e),
        }


@app.task(bind=True, acks_late=True)
def archive_task_progress(
    self,
    task_id: str,
    user_id: str,
    task_type: str,
    status: str,
    progress: float,
    milestones: list[dict],
    result: str,
) -> dict:
    """
    异步归档：将 Redis 中的已完成任务进度写入 PostgreSQL。

    由 TaskTracker.complete() / fail() 触发。
    """
    from datetime import datetime, timezone

    from apps.agent.models import TaskProgress

    logger.info(
        "[archive_task_progress] archiving task_id=%s status=%s", task_id, status
    )

    try:
        TaskProgress.objects.get_or_create(
            task_id=task_id,
            defaults={
                "task_type": task_type,
                "status": status,
                "progress": progress,
                "milestones": milestones,
                "result": result,
                "completed_at": datetime.now(timezone.utc),
                "user_id": user_id if len(user_id) == 36 else None,  # UUID format check
            },
        )
        return {"status": "SUCCESS", "task_id": task_id}
    except Exception as e:
        logger.error("[archive_task_progress] failed: %s", e, exc_info=True)
        return {"status": "ERROR", "error": str(e)}


# ---------------------------------------------------------------------------
# Task health check — centralized heartbeat & zombie detection
# ---------------------------------------------------------------------------


@app.task(bind=True)
def check_task_health(self) -> dict:
    """集中巡检所有 running 任务，推送心跳 + 检测僵尸任务并自动重试。"""
    import redis
    from django.conf import settings

    url = settings.REDIS_URL
    if url.rsplit("/", 1)[-1].isdigit():
        url = url.rsplit("/", 1)[0] + "/3"

    r = redis.from_url(url, decode_responses=True)
    now_ts = time.time()
    zombie_ids: list[tuple[str, str, float]] = []
    heartbeat_ids: list[tuple[str, str, float]] = []

    for key in r.scan_iter("task:progress:*"):
        data = r.hgetall(key)
        if not data or data.get("status") != "running":
            continue

        task_id = data.get("task_id", "")
        user_id = data.get("user_id", "")
        updated_str = data.get("updated_at", "")
        last_hb_str = data.get("last_heartbeat", "")

        try:
            updated = datetime.fromisoformat(updated_str) if updated_str else None
            last_hb = datetime.fromisoformat(last_hb_str) if last_hb_str else None
        except ValueError:
            continue

        if updated is None:
            continue

        age = (datetime.now(timezone.utc) - updated).total_seconds()

        if age > _ZOMBIE_TIMEOUT:
            zombie_ids.append((task_id, user_id, age))
            continue

        hb_age = (
            (datetime.now(timezone.utc) - last_hb).total_seconds() if last_hb else age
        )
        if hb_age >= _HEARTBEAT_INTERVAL:
            heartbeat_ids.append((task_id, user_id, age))

    for task_id, user_id, age in zombie_ids:
        _handle_zombie_task(r, task_id, user_id, age)

    for task_id, user_id, age in heartbeat_ids:
        redis_key = f"task:progress:{task_id}"
        extra = r.hgetall(redis_key)
        progress = float(extra.get("progress", 0) or 0)
        step = extra.get("step", "")
        _send_heartbeat_notification(task_id, user_id, age, progress, step)
        r.hset(
            redis_key,
            "last_heartbeat",
            datetime.now(timezone.utc).isoformat(),
        )

    return {
        "checked": len(heartbeat_ids) + len(zombie_ids),
        "heartbeats": len(heartbeat_ids),
        "zombies": len(zombie_ids),
    }


def _handle_zombie_task(
    r,
    task_id: str,
    user_id: str,
    age: float,
) -> None:
    """处理僵尸任务：自动重试（retry_count < max_retries）或通知用户。"""
    from apps.agent.task_tracker import _send_notification_redis

    redis_key = f"task:progress:{task_id}"
    data = r.hgetall(redis_key)
    retry_count = int(data.get("retry_count", 0))
    max_retries = int(data.get("max_retries", 1))
    original_task_str = data.get("original_task", "")

    short_id = task_id[:8]
    age_str = f"{int(age)}秒"

    if retry_count < max_retries and original_task_str:
        # 自动重试
        try:
            original_task = json.loads(original_task_str)
            celery_task = original_task.get("celery_task", "")

            if celery_task:
                # Celery task — resend via app.send_task
                task_args = original_task.get("args", [])
                task_kwargs = original_task.get("kwargs", {})
                new_result = app.send_task(celery_task, args=task_args, kwargs=task_kwargs)
                new_task_id = new_result.id
            else:
                # Stream task — republish to agent:tasks
                import asyncio
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

            r.hset(
                redis_key,
                mapping={
                    "status": "zombie_retrying",
                    "retry_count": retry_count + 1,
                    "new_task_id": new_task_id,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )

            _send_notification_redis(
                user_id,
                f"⚠️ 任务 #{short_id} 超时无响应（{age_str}）\n"
                f"🔄 已自动重试（第 {retry_count + 1}/{max_retries} 次）\n"
                f"新任务 ID: {new_task_id[:8]}",
            )

            archive_task_progress.delay(
                task_id=task_id,
                user_id=user_id,
                task_type=data.get("task_type", "agent"),
                status="zombie_retrying",
                progress=0.0,
                milestones=[],
                result=f"Zombie, auto-retry #{retry_count + 1}, new_task_id={new_task_id}",
            )

            logger.info(
                "[check_task_health] zombie %s auto-retried → %s",
                task_id,
                new_task_id,
            )
        except Exception:
            logger.error(
                "[check_task_health] auto-retry failed for %s", task_id, exc_info=True
            )
            r.hset(redis_key, "status", "zombie")
            _send_notification_redis(
                user_id,
                f"❌ 任务 #{short_id} 自动重试失败\n请手动处理",
            )
    else:
        # 通知用户，由用户决定重试或继续
        r.hset(
            redis_key,
            mapping={
                "status": "zombie",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        _send_notification_redis(
            user_id,
            f"🚨 任务 #{short_id} 超时无响应（{age_str}）\n"
            f"已自动重试 {retry_count}/{max_retries} 次，请手动处理\n"
            f"使用 /retry {task_id} 或 /continue {task_id}",
        )

        archive_task_progress.delay(
            task_id=task_id,
            user_id=user_id,
            task_type=data.get("task_type", "agent"),
            status="zombie",
            progress=0.0,
            milestones=[],
            result=f"Zombie after {retry_count} retries, age={age_str}",
        )

        logger.warning("[check_task_health] zombie %s — awaiting user action", task_id)


def _send_heartbeat_notification(
    task_id: str, user_id: str, age: float, progress: float = 0.0, step: str = ""
) -> None:
    """推送心跳通知——含当前进度和阶段。"""
    from apps.agent.task_tracker import _send_notification_redis

    short_id = task_id[:8]
    mins = int(age) // 60
    secs = int(age) % 60
    elapsed = f"{mins}分{secs}秒" if mins else f"{secs}秒"

    parts = [f"⏳ 任务 #{short_id} 运行中 | 耗时：{elapsed}"]
    if step:
        parts.append(f"阶段：{step}")
    if progress > 0:
        parts.append(f"进度：{int(progress * 100)}%")
    _send_notification_redis(user_id, "\n".join(parts))
