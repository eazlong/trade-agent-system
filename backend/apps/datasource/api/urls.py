"""
数据源 API URL 配置
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    DataSourceViewSet,
    MarketDataViewSet,
    SubscriptionViewSet,
    QualityMonitorViewSet,
)

router = DefaultRouter()
router.register(r"sources", DataSourceViewSet, basename="datasource")
router.register(r"market", MarketDataViewSet, basename="market")
router.register(r"subscriptions", SubscriptionViewSet, basename="subscription")
router.register(r"quality", QualityMonitorViewSet, basename="quality")

urlpatterns = [
    path("", include(router.urls)),
]
