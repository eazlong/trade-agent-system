"""日志保留策略的 Celery 任务。

**为什么需要它**：`system_logs` 此前**没有任何清理入口**。`celery_app.py` 里那条
`clean-audit-logs` 一直是注释 + TODO（`TODO: apps.agent.tasks.clean_old_audit_logs
尚未实现`），注释里写的却是 `apps.agent` 的审计表——`system_logs` 连这句注释都没有。

后果在 txy 上是可测的：2026-11-06 实测该表 **3,157,406 行 / 1210 MB**，其中
**2026-09 单月就 1,914,415 行**（`datasource` 模块 1.77M，内容是
`[DataSource] precise trigger key=...` 这类逐条 DEBUG）。表只增不减，而每一次
写入都是一次独立的 INSERT（旧 handler 还为此每条起一个线程，见 handler.py 的
module docstring）。

**两个设计决定**：

1. **分级保留，不是一刀切**。ERROR / CRITICAL 保留得久，INFO 及以下按较短窗口清理。
   理由：排查线上故障时真正要翻的是 ERROR，而 DEBUG/INFO 的绝大多数（如逐条的
   `precise trigger`）在产生当天就没人再看第二眼。一刀切会让"出事故时想回溯一个月
   前同类错误"变成不可能——那正是日志存在的意义。

2. **分批删除，不一次删光**。一次 DELETE 上百万行会长时间占用事务、膨胀 WAL、
   在 `--concurrency=2` 的小机器上把 DB 拖到无响应。这里每批上限 `BATCH_SIZE`，
   循环到清空或达到 `MAX_BATCHES`（后者防止一条任务被拖成小时级，剩下的留给下一轮）。

**幂等**：判定条件是 `created_at < cutoff`，重复跑只是删到同一批为止，不会误删
保留期内的行。所以不需要 `max_retries`——下一轮调度就是重试（与
`apps.regime.tasks.expire_candidates` 同一条纪律）。
"""

import logging
import os

from celery import shared_task

logger = logging.getLogger(__name__)

#: 各等级的保留天数。**要改口径就改这里**，不要在任务体里写魔法数字。
#: - ERROR / CRITICAL：90 天。事故复盘常要回看一个季度。
#: - WARNING：30 天。
#: - INFO / DEBUG：14 天。这两档是「量」的来源，也是历史上把表撑到 3M 行的部分。
RETENTION_DAYS = {
    "ERROR": 90,
    "CRITICAL": 90,
    "WARNING": 30,
    "INFO": 14,
    "DEBUG": 14,
}

#: 单批删除行数上限。小机器上别把事务拖长。
BATCH_SIZE = int(os.environ.get("LOG_RETENTION_BATCH_SIZE", "10000"))

#: 单轮最多跑几批。`2 核 / 3.6GiB` 的机器上宁可多跑几轮，也不要一条任务跑成小时级。
MAX_BATCHES = int(os.environ.get("LOG_RETENTION_MAX_BATCHES", "20"))


def _cutoff(level: str, now):
    """某等级对应的清理截止时刻（早于它的行会被删）。"""
    import datetime as _dt

    return now - _dt.timedelta(days=RETENTION_DAYS[level])


def purge_expired_logs(
    *, max_batches: int | None = None, batch_size: int | None = None
):
    """按 `RETENTION_DAYS` 分批删除过期日志，返回本轮各档删除计数。

    抽成普通函数（而不是全塞进 task 体）是为了能在测试里直接调、不必起 worker。
    任务 `clean_old_audit_logs` 只是它的调度入口。
    """
    from django.db import close_old_connections
    from django.utils import timezone

    from apps.logging_app.models import SystemLog

    max_batches = MAX_BATCHES if max_batches is None else max_batches
    batch_size = BATCH_SIZE if batch_size is None else batch_size

    now = timezone.now()
    deleted: dict[str, int] = {}

    for level, days in RETENTION_DAYS.items():
        cutoff = _cutoff(level, now)
        level_total = 0
        for _ in range(max_batches):
            # 每批前刷新连接：这条任务可能在 worker 上跑较久，连接可能已被
            # pgbouncer / DB idle timeout 掐掉（与 backtest tasks 同一纪律）。
            close_old_connections()

            # 先取一批 id 再按 id 删，而不是 `filter(...).delete()`：
            # 后者会把整个 queryset 的匹配行一次性放进一个事务，正是要避免的形态。
            ids = list(
                SystemLog.objects.filter(
                    level=level, created_at__lt=cutoff
                ).values_list("id", flat=True)[:batch_size]
            )
            if not ids:
                break
            SystemLog.objects.filter(id__in=ids).delete()
            level_total += len(ids)

        if level_total:
            deleted[level] = level_total

    return {
        "deleted": deleted,
        "total": sum(deleted.values()),
        "retention_days": dict(RETENTION_DAYS),
    }


@shared_task(name="apps.logging_app.tasks.clean_old_audit_logs")
def clean_old_audit_logs():
    """清理过期日志。

    名字沿用 `celery_app.py` 里那条一直没实现的注释键（`clean-audit-logs`），
    但落点是 `apps.logging_app`——这张表归它管。`agent_audit_logs` 是另一张表
    （当前 0 行），需要时另开一条任务，不在这里顺手删别人的数据。

    返回 ``{"deleted": {level: n}, "total": n}``。全 0 不是异常：日志本来就稀疏，
    按天调度时多数轮次只清掉几条。
    """
    result = purge_expired_logs()
    if result["total"]:
        logger.info(
            "[log-retention] deleted %d rows: %s", result["total"], result["deleted"]
        )
    return result
