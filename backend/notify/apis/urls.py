from django.urls import path, include
from rest_framework.routers import DefaultRouter
from notify.apis.views import NotifyConfigViewSet, NotifyLogViewSet

router = DefaultRouter()
router.register(r'configs', NotifyConfigViewSet, basename='configs')
router.register(r'history', NotifyLogViewSet, basename='history')

urlpatterns = [
    path('notify/', include(router.urls)),
]