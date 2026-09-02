from django.http import JsonResponse
from django.urls import path, include


def health_check(request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path('api/health/', health_check),
    path('api/auth/', include('apps.authentication.urls')),
    path('api/agent/', include('apps.agent.urls')),
    path('api/exchange/', include('apps.exchange.urls')),
    path('api/trading/', include('apps.trading.urls')),
    path('api/risk/', include('apps.risk.urls')),
    path('api/backtest/', include('apps.backtest.urls')),
    path('api/channel/', include('apps.channel.urls')),
    path('api/notify/', include('apps.notify.urls')),
    path('api/memory/', include('apps.memory.urls')),
    path('api/skill/', include('apps.skill.urls')),
    path('api/datasource/', include('apps.datasource.urls')),
    path('api/signal-monitor/', include('apps.signal_monitor.api.urls')),
    path('api/logs/', include('apps.logging_app.urls')),
]
