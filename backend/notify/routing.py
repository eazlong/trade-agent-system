# your_app_name/routing.py
from django.urls import path
from notify.consumers.notify_consumer import NotifyConsumer

websocket_urlpatterns = [
    path('ws/notify/', NotifyConsumer.as_asgi()),
]