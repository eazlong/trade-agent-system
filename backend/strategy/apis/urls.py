from django.urls import path, include
from strategy.apis.views import StrategyTemplateViewSet
from rest_framework.routers import DefaultRouter

router = DefaultRouter()
router.register(r'templates', StrategyTemplateViewSet, basename='templates')

urlpatterns = [
    path('strategy/', include(router.urls)),
]