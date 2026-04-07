from django.urls import path, include

urlpatterns = [
    path('api/signal-monitor/', include('apps.signal_monitor.api.urls')),
]
