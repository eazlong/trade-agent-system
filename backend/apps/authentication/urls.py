from django.urls import path
from . import views

urlpatterns = [
    path("register/", views.register, name="auth-register"),
    path("login/", views.login, name="auth-login"),
    path("token/refresh/", views.token_refresh, name="auth-token-refresh"),
    path("me/", views.me, name="auth-me"),
    # 用户级界面偏好（顶栏行情币种）。GET 读 + 顺手补 WS 订阅，PUT 整份替换。
    path("preferences/", views.preferences, name="auth-preferences"),
    path("logout/", views.logout, name="auth-logout"),
]
