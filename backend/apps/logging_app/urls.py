from django.urls import path
from apps.logging_app import views

urlpatterns = [
    path("", views.LogListView.as_view(), name="log-list"),
    path("stream/", views.log_stream_view, name="log-stream"),
    path("test/", views.log_test_view, name="log-test"),
]
