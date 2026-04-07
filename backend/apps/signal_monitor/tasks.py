"""
信号监控 Celery 任务

定时检查所有活跃信号，由 Celery Beat 调度。
"""
import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name='apps.signal_monitor.tasks.check_signals')
def check_signals():
    """
    定时检查所有活跃信号。

    由 Celery Beat 每 30 秒调度一次。
    从 MemoryDataStore 获取最新 K 线数据，
    并行计算指标并评估条件。
    """
    from .engine import SignalMonitorEngine

    engine = SignalMonitorEngine.get_instance()
    results = engine.check_all_signals()

    if results:
        logger.info('Signal check triggered %d signals', len(results))
        for r in results:
            logger.info(
                'Signal triggered: %s (%s %s) - %s',
                r['monitor_name'],
                r['symbol'],
                r['indicator_type'],
                r['trigger_type'],
            )

    return {
        'triggered_count': len(results),
        'results': results,
    }


@shared_task(name='apps.signal_monitor.tasks.clean_expired_monitors')
def clean_expired_monitors():
    """清理过期的信号监控"""
    from django.utils import timezone
    from .models import SignalMonitor

    count = SignalMonitor.objects.filter(
        status='active',
        expires_at__lt=timezone.now(),
    ).update(status='expired')

    if count > 0:
        logger.info('Expired %d signal monitors', count)

    return {'expired_count': count}
