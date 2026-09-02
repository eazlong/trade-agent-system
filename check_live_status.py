#!/usr/bin/env python
"""检查实盘运行状态"""
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.dev")

import django
django.setup()

from apps.signal_monitor.models import SignalMonitor
from apps.strategy_engine.models import LiveTradingSession

# 检查活跃的信号监控
active_monitors = SignalMonitor.objects.filter(status='active')
print(f'Active monitors: {active_monitors.count()}')
for m in active_monitors[:5]:
    print(f'  - {m.id}: {m.symbol} {m.indicator_type} {m.trigger_condition}')

# 检查运行中的实盘策略
running_sessions = LiveTradingSession.objects.filter(status='running')
print(f'\nRunning sessions: {running_sessions.count()}')
for s in running_sessions[:5]:
    print(f'  - {s.id}: {s.strategy.name} {s.symbol} user={s.user_id}')