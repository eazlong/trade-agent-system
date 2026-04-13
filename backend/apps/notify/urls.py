from django.urls import path
from . import views

urlpatterns = [
    path("", views.list_notifications),
    path("<uuid:pk>/read/", views.mark_read),
]
