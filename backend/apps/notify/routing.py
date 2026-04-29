from django.urls import path
from . import consumers
from apps.logging_app.consumers import LogConsumer
from apps.agent.consumers import ChatConsumer

websocket_urlpatterns = [
    path(
        "ws/notifications/",
        consumers.NotificationConsumer.as_asgi(),
        name="ws-notifications",
    ),
    path("ws/logs/", LogConsumer.as_asgi(), name="ws-logs"),
    path("ws/chat/", ChatConsumer.as_asgi(), name="ws-chat"),
]
