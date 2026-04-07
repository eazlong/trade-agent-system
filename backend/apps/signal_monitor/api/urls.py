"""信号监控 API 路由"""
from django.urls import path
from .views import (
    SignalMonitorListView,
    SignalMonitorDetailView,
    SignalMonitorTriggerLogView,
    BacktestImportView,
)

urlpatterns = [
    path('', SignalMonitorListView.as_view(), name='signal-monitor-list'),
    path('<uuid:monitor_id>/', SignalMonitorDetailView.as_view(), name='signal-monitor-detail'),
    path(
        '<uuid:monitor_id>/logs/',
        SignalMonitorTriggerLogView.as_view(),
        name='signal-monitor-logs',
    ),
    path(
        'import/backtest/<uuid:backtest_id>/',
        BacktestImportView.as_view(),
        name='signal-monitor-import-backtest',
    ),
]
