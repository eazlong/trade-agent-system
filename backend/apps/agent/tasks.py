"""Celery tasks for scheduled agent task execution and task progress archival."""

from __future__ import annotations

import asyncio
import logging

from celery_app import app

logger = logging.getLogger(__name__)


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
