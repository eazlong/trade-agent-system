from django.urls import path, include
from rest_framework.routers import DefaultRouter
from qtbot.apis.views import (
    BotConfigViewSet,
    TradeRecordViewSet,
    BotOperatoerViewSet,
    TbUserTradeRecordViewSet,
    TradeRecordCommentViewSet,
)

router = DefaultRouter()
router.register(r'config', BotConfigViewSet, basename='config')
router.register(r'trade-records', TbUserTradeRecordViewSet)
router.register(r'comments', TradeRecordCommentViewSet)

urlpatterns = [
    path('bot/', include(router.urls)),
    path('bot/<pk>/record/', TradeRecordViewSet.as_view({'get': 'list'}), name='trade-record-list'),
    path('bot/record/<pk>/', TradeRecordViewSet.as_view({'get': 'retrieve'}), name='trade-record-retrieve'),
    path('bot/<pk>/destroy/', BotOperatoerViewSet.as_view({'delete': 'destroy'}), name='bot-operator-destroy'),
]