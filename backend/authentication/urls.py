from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import CaptchaAPIView, UserProfileViewSet

urlpatterns = [
    path('api/captcha/', CaptchaAPIView.as_view(), name='api-captcha'),
    path('api/user/profile/', UserProfileViewSet.as_view({'get': 'retrieve', 'put': 'update'}), name='user-profile'),
]
