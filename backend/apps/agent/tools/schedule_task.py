"""定时任务工具 — 向系统提交定时任务，定时给不同 Agent 派发任务。

设计原则：
  - 一次性定时任务：使用 Celery apply_async(eta=...) 在指定时间执行
  - 周期性定时任务：使用 django-celery-beat 数据库存储，celery-beat 自动读取
  - 任务内容会路由到指定的 SubAgent 执行
  - Agent 通过两步完成：
    1. submit_scheduled_task → 立即返回 task_id 或 schedule_id
    2. get_task_result       → 查询任务状态 / 结果
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from .base import BaseTool, ToolResult

logger = logging.getLogger(__name__)


class SubmitScheduledTaskTool(BaseTool):
    """
    提交一次性定时任务，在指定时间点触发 Agent 任务。
    """

    name = "submit_scheduled_task"
    description = (
        "提交定时任务到系统，在指定时间自动执行。任务会在指定时间点路由到指定 Agent 处理。"
        "立即返回 schedule_id，使用 get_task_result 查询执行状态和结果。"
    )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "description": "目标 Agent 名称，如 analyst、quant、risk_advisor、coach、researcher",
                },
                "message": {
                    "type": "string",
                    "description": "要发送给 Agent 的任务内容/消息",
                },
                "run_at": {
                    "type": "string",
                    "description": "执行时间（ISO 格式，如 2026-04-19T09:00:00+08:00），支持 'now+5m'、'tomorrow 09:00' 等相对格式",
                },
                "user_id": {
                    "type": "string",
                    "description": "用户 ID（可选），用于关联用户上下文",
                },
            },
            "required": ["agent_name", "message", "run_at"],
        }

    def _parse_run_at(self, run_at: str) -> datetime:
        """解析执行时间，支持 ISO 格式和相对时间表达式。"""
        from datetime import timedelta

        run_at = run_at.strip()

        # 相对时间：now+5m, now+1h, now+30s, now+2d
        rel = __import__("re").match(r"^now\+(\d+)([smhd])$", run_at, __import__("re").IGNORECASE)
        if rel:
            value = int(rel.group(1))
            unit = rel.group(2).lower()
            deltas = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}
            return datetime.now(timezone.utc) + timedelta(**{deltas[unit]: value})

        # 相对时间：tomorrow HH:MM
        tom_match = __import__("re").match(r"^tomorrow\s+(\d{1,2}):(\d{2})$", run_at, __import__("re").IGNORECASE)
        if tom_match:
            hour, minute = int(tom_match.group(1)), int(tom_match.group(2))
            from datetime import timedelta
            tomorrow = datetime.now(timezone.utc).replace(
                hour=hour, minute=minute, second=0
            ) + timedelta(days=1)
            return tomorrow

        # ISO 格式
        try:
            return datetime.fromisoformat(run_at)
        except ValueError:
            pass

        raise ValueError(f"无法解析时间格式: {run_at}，支持 ISO 格式或 now+5m、tomorrow 09:00")

    async def execute(
        self,
        agent_name: str = "",
        message: str = "",
        run_at: str = "",
        user_id: str = "",
        **kwargs,
    ) -> ToolResult:
        if not agent_name or not message or not run_at:
            return ToolResult(
                success=False,
                error="agent_name、message、run_at 均为必填项",
            )

        try:
            from apps.agent.tasks import execute_scheduled_agent_task

            eta = self._parse_run_at(run_at)

            task = execute_scheduled_agent_task.apply_async(
                kwargs={
                    "agent_name": agent_name,
                    "message": message,
                    "user_id": user_id or "",
                },
                eta=eta,
            )

            eta_str = eta.isoformat()
            logger.info(
                "[SubmitScheduledTaskTool] scheduled task_id=%s agent=%s eta=%s",
                task.id, agent_name, eta_str,
            )
            return ToolResult(
                success=True,
                data={
                    "schedule_id": task.id,
                    "task_id": task.id,
                    "agent_name": agent_name,
                    "run_at": eta_str,
                    "status": "SCHEDULED",
                    "message": (
                        f"定时任务已提交，schedule_id={task.id}，"
                        f"将在 {eta_str} 触发 Agent={agent_name}。"
                        f"使用 get_task_result 查询执行状态。"
                    ),
                },
            )
        except ValueError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            logger.error("[SubmitScheduledTaskTool] submit failed: %s", e, exc_info=True)
            return ToolResult(success=False, error=f"提交定时任务失败: {e}")


class SubmitRecurringTaskTool(BaseTool):
    """
    提交周期性定时任务，通过 django-celery-beat 在数据库层面注册，celery-beat 自动读取。
    """

    name = "submit_recurring_task"
    description = (
        "提交周期性定时任务，按 crontab 表达式重复执行。"
        "任务会持久化到数据库，celery-beat 进程自动读取。"
    )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "description": "目标 Agent 名称，如 analyst、quant、risk_advisor、coach、researcher",
                },
                "message": {
                    "type": "string",
                    "description": "要发送给 Agent 的任务内容/消息",
                },
                "cron_expression": {
                    "type": "string",
                    "description": "Crontab 表达式，5个字段：分 时 日 月 周。例如 '0 9 * * 1-5' 表示工作日每天早上9点",
                },
                "task_name": {
                    "type": "string",
                    "description": "定时任务名称（用于标识此任务，如 'daily_btc_analysis'）",
                },
                "user_id": {
                    "type": "string",
                    "description": "用户 ID（可选），用于关联用户上下文",
                },
            },
            "required": ["agent_name", "message", "cron_expression", "task_name"],
        }

    async def execute(
        self,
        agent_name: str = "",
        message: str = "",
        cron_expression: str = "",
        task_name: str = "",
        user_id: str = "",
        **kwargs,
    ) -> ToolResult:
        if not agent_name or not message or not cron_expression or not task_name:
            return ToolResult(
                success=False,
                error="agent_name、message、cron_expression、task_name 均为必填项",
            )

        parts = cron_expression.strip().split()
        if len(parts) != 5:
            return ToolResult(
                success=False,
                error=f"cron_expression 需要 5 个字段（分 时 日 月 周），当前: {cron_expression}",
            )

        try:
            from asgiref.sync import sync_to_async
            from django_celery_beat.models import CrontabSchedule, PeriodicTask

            minute, hour, day_of_month, month_of_year, day_of_week = parts

            schedule, _ = await sync_to_async(CrontabSchedule.objects.get_or_create)(
                minute=minute,
                hour=hour,
                day_of_month=day_of_month,
                month_of_year=month_of_year,
                day_of_week=day_of_week,
            )

            import json as _json
            await sync_to_async(PeriodicTask.objects.update_or_create)(
                name=task_name,
                defaults={
                    "task": "apps.agent.tasks.execute_recurring_agent_task",
                    "crontab": schedule,
                    "kwargs": _json.dumps({
                        "agent_name": agent_name,
                        "message": message,
                        "user_id": user_id or "",
                        "task_name": task_name,
                    }),
                },
            )

            logger.info(
                "[SubmitRecurringTaskTool] registered task_name=%s agent=%s cron=%s",
                task_name, agent_name, cron_expression,
            )
            return ToolResult(
                success=True,
                data={
                    "task_name": task_name,
                    "agent_name": agent_name,
                    "cron_expression": cron_expression,
                    "status": "ACTIVE",
                    "message": (
                        f"周期定时任务已注册，task_name={task_name}，"
                        f"Agent={agent_name}，cron={cron_expression}。"
                        f"任务已持久化到数据库，celery-beat 将按 crontab 定时周期触发。"
                    ),
                },
            )
        except Exception as e:
            logger.error("[SubmitRecurringTaskTool] register failed: %s", e, exc_info=True)
            return ToolResult(success=False, error=f"注册周期任务失败: {e}")


class CancelScheduledTaskTool(BaseTool):
    """
    取消已提交的定时任务或周期任务。
    """

    name = "cancel_scheduled_task"
    description = (
        "取消已提交的一次性定时任务或周期性定时任务。"
        "对一次性任务调用 revoke，对周期任务从数据库中移除。"
    )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_id_or_name": {
                    "type": "string",
                    "description": "一次性任务的 task_id（如 xxx-xxx-xxx），或周期任务的 task_name（如 daily_btc_analysis）",
                },
                "task_type": {
                    "type": "string",
                    "description": "任务类型：scheduled=一次性定时任务，recurring=周期性任务",
                    "enum": ["scheduled", "recurring"],
                    "default": "scheduled",
                },
            },
            "required": ["task_id_or_name"],
        }

    async def execute(
        self,
        task_id_or_name: str = "",
        task_type: str = "scheduled",
        **kwargs,
    ) -> ToolResult:
        if not task_id_or_name:
            return ToolResult(success=False, error="task_id_or_name 为必填项")

        try:
            from celery_app import app as celery_app

            if task_type == "recurring":
                from asgiref.sync import sync_to_async
                from django_celery_beat.models import PeriodicTask

                deleted, _ = await sync_to_async(
                    lambda: PeriodicTask.objects.filter(name=task_id_or_name).delete()
                )()
                if deleted:
                    logger.info(
                        "[CancelScheduledTaskTool] removed recurring task_name=%s",
                        task_id_or_name,
                    )
                    return ToolResult(
                        success=True,
                        data={
                            "task_name": task_id_or_name,
                            "status": "CANCELLED",
                            "message": f"周期任务 {task_id_or_name} 已取消。",
                        },
                    )
                else:
                    return ToolResult(
                        success=False,
                        error=f"未找到周期任务: {task_id_or_name}",
                    )
            else:
                # 撤销一次性任务
                celery_app.control.revoke(task_id_or_name, terminate=True)
                logger.info(
                    "[CancelScheduledTaskTool] revoked task_id=%s",
                    task_id_or_name,
                )
                return ToolResult(
                    success=True,
                    data={
                        "task_id": task_id_or_name,
                        "status": "CANCELLED",
                        "message": f"一次性定时任务 {task_id_or_name} 已取消。",
                    },
                )
        except Exception as e:
            logger.error("[CancelScheduledTaskTool] cancel failed: %s", e, exc_info=True)
            return ToolResult(success=False, error=f"取消任务失败: {e}")


class ListScheduledTasksTool(BaseTool):
    """
    查看当前系统中的定时任务列表。
    """

    name = "list_scheduled_tasks"
    description = "查看当前系统中已注册的周期定时任务列表（数据库）。一次性任务可通过 get_task_result 查询。"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {},
        }

    async def execute(self, **kwargs) -> ToolResult:
        try:
            from asgiref.sync import sync_to_async
            from django_celery_beat.models import PeriodicTask

            tasks = await sync_to_async(list)(
                PeriodicTask.objects.filter(enabled=True).select_related('crontab', 'interval')
            )
            if not tasks:
                return ToolResult(
                    success=True,
                    data="当前没有已注册的周期定时任务。",
                )

            import json as _json

            lines = [f"共 {len(tasks)} 个周期定时任务（数据库）："]
            for task in tasks:
                schedule_desc = ""
                if task.crontab:
                    schedule_desc = f"crontab({task.crontab})"
                elif task.interval:
                    schedule_desc = str(task.interval)

                try:
                    kws = _json.loads(task.kwargs or "{}")
                except Exception:
                    kws = {}

                agent = kws.get("agent_name", "N/A")
                lines.append(
                    f"- **{task.name}** | Agent: {agent} | Schedule: {schedule_desc} | Enabled: {task.enabled}"
                )

            return ToolResult(success=True, data="\n".join(lines))
        except Exception as e:
            logger.error("[ListScheduledTasksTool] list failed: %s", e, exc_info=True)
            return ToolResult(success=False, error=f"查询任务列表失败: {e}")
