"""取消定时任务的可追溯性（2026-09-18，B 方案）。

背景（真实事故取证）：2026-07-05「每小时研究一个高频交易策略…」周期任务从
django_celery_beat_periodictask 表中消失，全程无任何记录——AgentAuditLog 全表
0 行、无 HTTP 删除端点、AgentAuditLog 无写入者、且后端无持久化日志（容器只活
几分钟），事后无法定位"谁在何时取消了它"。全库唯一的删除路径就是本工具的
`PeriodicTask.objects.filter(name=...).delete()`。

本文件锁死取消动作的可追溯性契约：
  1. 成功取消必须写 1 行 AgentAuditLog（机器取证；即使没有 user 上下文也写）
  2. 有 user_id 时必须额外写 1 条 Notification（用户可见，带 user + created_at）
  3. 未命中（没取消任何东西）不得留任何记录
  4. 审计写入失败必须不影响取消结果本身（否则审计变成新的故障点）
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from django_celery_beat.models import CrontabSchedule, PeriodicTask

from apps.agent.models import AgentAuditLog, ScheduledOneTimeTask
from apps.agent.tools.schedule_task import CancelScheduledTaskTool
from apps.notify.models import Notification

pytestmark = pytest.mark.django_db(transaction=True)


def _run(coro):
    return asyncio.run(coro)


def _make_recurring(name="hourly_research"):
    sched, _ = CrontabSchedule.objects.get_or_create(
        minute="0",
        hour="*",
        day_of_month="*",
        month_of_year="*",
        day_of_week="*",
        timezone="UTC",
    )
    return PeriodicTask.objects.create(
        name=name,
        task="apps.agent.tasks.execute_recurring_agent_task",
        crontab=sched,
        kwargs="{}",
        enabled=True,
    )


def _make_user(email="cancel@test.local", username="cancel_user"):
    return get_user_model().objects.create_user(
        email=email, username=username, password="pw12345"
    )


class TestCancelRecurringAudit:
    def test_cancel_recurring_writes_audit_and_notification(self):
        """成功取消周期任务 → 1 行审计 + 1 条用户通知（含任务名与调度）。"""
        user = _make_user()
        _make_recurring("hourly_research")

        result = _run(
            CancelScheduledTaskTool().execute(
                task_id_or_name="hourly_research",
                task_type="recurring",
                user_id=str(user.id),
            )
        )

        assert result.success is True
        assert PeriodicTask.objects.filter(name="hourly_research").count() == 0

        audits = list(AgentAuditLog.objects.all())
        assert len(audits) == 1, "取消动作必须落 1 行审计"
        assert audits[0].action == "cancel_scheduled_task"
        assert "hourly_research" in audits[0].input_summary
        assert "recurring" in audits[0].input_summary

        notes = list(Notification.objects.filter(user=user))
        assert len(notes) == 1, "有 user_id 时必须给用户可见通知"
        assert "hourly_research" in notes[0].message
        assert notes[0].channel == "web"

    def test_cancel_recurring_without_user_still_audits(self):
        """无 user 上下文（如系统/定时触发）→ 仍必须留机器审计。"""
        _make_recurring("no_user_task")

        result = _run(
            CancelScheduledTaskTool().execute(
                task_id_or_name="no_user_task", task_type="recurring"
            )
        )

        assert result.success is True
        assert AgentAuditLog.objects.count() == 1
        assert Notification.objects.count() == 0

    def test_cancel_missing_task_records_nothing(self):
        """未命中（任务不存在）→ 不得产生审计/通知噪声。"""
        result = _run(
            CancelScheduledTaskTool().execute(
                task_id_or_name="ghost_task", task_type="recurring"
            )
        )

        assert result.success is False
        assert AgentAuditLog.objects.count() == 0
        assert Notification.objects.count() == 0

    def test_audit_write_failure_does_not_break_cancel(self):
        """审计写入失败不能让取消本身失败（审计不该成为新故障点）。"""
        _make_recurring("resilient_task")

        with patch.object(
            AgentAuditLog.objects, "create", side_effect=RuntimeError("audit down")
        ):
            result = _run(
                CancelScheduledTaskTool().execute(
                    task_id_or_name="resilient_task", task_type="recurring"
                )
            )

        assert result.success is True
        assert PeriodicTask.objects.filter(name="resilient_task").count() == 0


class TestCancelOneTimeAudit:
    def test_cancel_one_time_writes_audit_and_notification(self):
        """一次性任务取消同样可追溯（revoke + 审计 + 通知）。"""
        user = _make_user(email="onetime@test.local", username="onetime_user")
        task = ScheduledOneTimeTask.objects.create(
            task_name="one_time_btc",
            agent_name="quant",
            message="回测",
            user_id=str(user.id),
            run_at=timezone.now(),
            status="pending",
            celery_task_id="",
        )

        result = _run(
            CancelScheduledTaskTool().execute(
                task_id_or_name=str(task.id),
                task_type="scheduled",
                user_id=str(user.id),
            )
        )

        assert result.success is True
        task.refresh_from_db()
        assert task.status == "revoked"
        assert AgentAuditLog.objects.count() == 1
        assert Notification.objects.filter(user=user).count() == 1


class TestCancelCleansOrphanSchedule:
    """取消周期任务时顺带清理孤儿调度行。

    django-celery-beat 删 PeriodicTask **不会**清它引用的 CrontabSchedule，
    线上实测因此遗留 8 行孤儿（含 2026-07-05 消失的那条 hourly research 的
    `0 * * * *`）。约束：只删引用计数归零的，绝不误删仍被别的任务共享的调度。
    """

    def _cr_id(self, name):
        return PeriodicTask.objects.get(name=name).crontab_id

    def test_cancel_deletes_exclusive_schedule(self):
        """独占调度 → 随任务一起清理。"""
        _make_recurring("solo_task")
        cr_id = self._cr_id("solo_task")
        assert CrontabSchedule.objects.filter(id=cr_id).count() == 1

        result = _run(
            CancelScheduledTaskTool().execute(
                task_id_or_name="solo_task", task_type="recurring"
            )
        )

        assert result.success is True
        assert CrontabSchedule.objects.filter(id=cr_id).count() == 0

    def test_cancel_keeps_shared_schedule(self):
        """仍被别的任务引用的调度 → 必须保留（不能误删）。"""
        _make_recurring("task_a")
        cr_id = self._cr_id("task_a")
        PeriodicTask.objects.create(
            name="task_b",
            task="apps.agent.tasks.execute_recurring_agent_task",
            crontab_id=cr_id,
            kwargs="{}",
            enabled=True,
        )

        result = _run(
            CancelScheduledTaskTool().execute(
                task_id_or_name="task_a", task_type="recurring"
            )
        )

        assert result.success is True
        assert CrontabSchedule.objects.filter(id=cr_id).count() == 1

    def test_schedule_cleanup_failure_does_not_break_cancel(self):
        """清理失败只告警，取消本身仍成功。"""
        _make_recurring("cleanup_fail_task")

        with patch.object(
            CancelScheduledTaskTool,
            "_cleanup_orphan_schedules",
            AsyncMock(side_effect=RuntimeError("cleanup down")),
        ):
            result = _run(
                CancelScheduledTaskTool().execute(
                    task_id_or_name="cleanup_fail_task", task_type="recurring"
                )
            )

        assert result.success is True
        assert PeriodicTask.objects.filter(name="cleanup_fail_task").count() == 0
