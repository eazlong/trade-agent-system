from django.urls import path
from . import views

urlpatterns = [
    path('chat/', views.chat, name='agent-chat'),
    path('frame/status/', views.frame_status, name='agent-frame-status'),
    path('frame/control/', views.frame_control, name='agent-frame-control'),
]
