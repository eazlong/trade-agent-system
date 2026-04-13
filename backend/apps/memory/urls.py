from django.urls import path
from . import views

urlpatterns = [
    path("", views.list_memories, name="memory-list"),
    path("<uuid:pk>/", views.delete_memory, name="memory-delete"),
]
