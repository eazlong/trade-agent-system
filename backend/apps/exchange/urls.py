from django.urls import path
from . import views

urlpatterns = [
    path("accounts/", views.account_list),
    path("accounts/<uuid:pk>/", views.account_detail),
]
