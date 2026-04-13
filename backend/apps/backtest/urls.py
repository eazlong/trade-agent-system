from django.urls import path
from . import views

urlpatterns = [
    path("results/", views.result_list),
    path("results/<uuid:pk>/", views.result_detail),
]
