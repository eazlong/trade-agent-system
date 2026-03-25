from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
# from rest_framework_simplejwt.views import (
#     TokenObtainPairView,
#     TokenRefreshView,
# )
from core.auth import (
    CaptchaTokenObtainPairView,
    PasswordResetView,
    PasswordResetConfirmView,
    PasswordChangeView
)

urlpatterns = [
    path('admin/', admin.site.urls),

    # Captcha URLs
    path('captcha/', include('captcha.urls')),

    # Authentication APIs
    path('api/auth/', include('djoser.urls')),
    path('api/', include('djoser.urls.jwt')),
    path('', include('authentication.urls')),  # 添加authentication的URLs

    # Custom authentication views
    path('api/jwt/create/', CaptchaTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/auth/password/reset/', PasswordResetView.as_view(), name='password_reset'),
    path('api/auth/password/reset/confirm/', PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    path('api/auth/password/change/', PasswordChangeView.as_view(), name='password_change'),

    # API ROUTES
    path('api/', include('strategy.apis.urls')),
    path('api/', include('notify.apis.urls')),
    path('api/', include('trade.apis.urls')),
    path('api/', include('qtbot.apis.urls')),
    path('api/', include('assistant.apis.urls')),
    path('api/', include('ai_agent.urls')),
]


if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL,
                          document_root=settings.MEDIA_ROOT)
