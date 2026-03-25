from django.urls import path, include
from rest_framework.routers import DefaultRouter
from trade.apis.views import TradeConfigViewSet, TradeViewSet, SymbolsViewSet, KlinesViewSet

router = DefaultRouter()
router.register(r'config', TradeConfigViewSet, basename='config')
router.register(r'order', TradeViewSet, basename='order')
router.register(r'symbols', SymbolsViewSet, basename='symbols')

from trade.apis.views_record import TbUserTradeRecordViewSet, TradeRecordCommentViewSet
router.register(r'records', TbUserTradeRecordViewSet)
router.register(r'comments', TradeRecordCommentViewSet)

urlpatterns = [
    path('trade/', include(router.urls)),
    path('trade/<symbol>/klines/<timeframe>/<start_time>/<end_time>', KlinesViewSet.as_view({'get': 'list'}), name='klines'),
]