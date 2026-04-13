from django.urls import path
from . import views

urlpatterns = [
    path("register/", views.register, name="auth-register"),
    path("login/", views.login, name="auth-login"),
    path("token/refresh/", views.token_refresh, name="auth-token-refresh"),
    path("me/", views.me, name="auth-me"),
    path("logout/", views.logout, name="auth-logout"),
]
