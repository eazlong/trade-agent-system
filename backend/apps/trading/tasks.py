"""订单与账户相关的 Celery 定时任务。

第①段强制前置：悬挂 ``pending`` 行的反查必须有一个**不依赖交易框架存活**的兜底
（CONTEXT.md 第 137 条：兜底扫描落成 beat 定时任务，当前 beat 里没有任何订单相关
任务）。进程内成交同步只在框架起来时跑，而悬挂行恰恰是「进程死过一次」的产物——
所以兜底必须是独立任务。

**悬挂扫描在框架跑时直接跳过**：那时扫描由进程内成交同步负责（10s 一轮），两处
同时写同一批行只会带来重复告警，不会带来更快的收敛。口径与进程内完全一致——
两处调的是同一个 ``sweep_dangling_orders``，共用同一个测试面。

第二件事是**净值快照的写入方**（CONTEXT.md 第 142 条）。它不能跳过：日内回撤
检查的期初净值只有这一个来源，而框架没跑的时候用户照样可能持仓浮亏。
"""

import asyncio
import logging

from celery import shared_task
from django.db import close_old_connections

logger = logging.getLogger(__name__)


@shared_task(bind=True, name="apps.trading.tasks.check_order_status")
def check_order_status(self) -> dict:
    """扫描悬挂订单并逐条消解（交易所反查 → failed / 未知 / 补回交易所 ID）。

    由 Celery Beat 调度。返回值进任务健康检查，不静默。
    """
    from apps.trading.pending_reconcile import sweep_dangling_orders

    # 交易框架在跑：让进程内那一轮负责，避免两个写入者
    from apps.trading.executor import OrderExecutor

    if OrderExecutor.get_instance() is not None:
        logger.info("[check_order_status] 交易框架活跃，跳过（由进程内成交同步负责）")
        return {"skipped": "order_executor_active"}

    try:
        result = asyncio.run(sweep_dangling_orders())
    except Exception as e:  # noqa: BLE001
        logger.error("[check_order_status] 悬挂订单扫描失败: %s", e, exc_info=True)
        raise
    finally:
        # 长时间运行/反复调度的任务必须自己收掉 DB 连接，否则连接会攒在 worker 上
        close_old_connections()

    payload = result.as_dict()
    if result.scanned:
        logger.info("[check_order_status] %s", payload)
    return payload


@shared_task(bind=True, name="apps.trading.tasks.snapshot_daily_equity")
def snapshot_daily_equity(self) -> dict:
    """写当日净值快照（每个有未停止会话的用户一条，当日只写第一条）。

    5 分钟一轮而非「日界跑一次」：幂等使得重试、进程重启、账户短暂不可达都能
    自愈，不需要「必须在日界准确跑」这种脆弱前提。取不到余额时不写、不编造，
    并把降级状态告警给用户（见 ``daily_snapshot``）。
    """
    from apps.trading.daily_snapshot import write_daily_snapshots

    try:
        result = asyncio.run(write_daily_snapshots())
    except Exception as e:  # noqa: BLE001
        logger.error("[snapshot_daily_equity] 净值快照写入失败: %s", e, exc_info=True)
        raise
    finally:
        close_old_connections()

    payload = result.as_dict()
    if result.users:
        logger.info("[snapshot_daily_equity] %s", payload)
    return payload
