import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings.dev')

app = Celery('trade_agent')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

app.conf.beat_schedule = {
    # Sync positions from exchange every 5 minutes
    'sync-positions': {
        'task': 'apps.trading.tasks.sync_positions',
        'schedule': 300.0,
    },
    # Clean expired agent memory (L2) every hour
    'clean-agent-memory': {
        'task': 'apps.memory.tasks.clean_expired_memory',
        'schedule': crontab(minute=0),
    },
    # Clean old audit logs every day at 02:00 UTC
    'clean-audit-logs': {
        'task': 'apps.agent.tasks.clean_old_audit_logs',
        'schedule': crontab(hour=2, minute=0),
    },
    # Check signal monitors every 30 seconds
    'check-signals': {
        'task': 'apps.signal_monitor.tasks.check_signals',
        'schedule': 30.0,
    },
    # Clean expired signal monitors every hour
    'clean-expired-monitors': {
        'task': 'apps.signal_monitor.tasks.clean_expired_monitors',
        'schedule': crontab(minute=15),
    },
}
