from django.urls import path
from . import views

urlpatterns = [
    path('orders/', views.order_list),
    path('orders/<uuid:pk>/', views.order_detail),
    path('strategies/', views.strategy_list),
    path('summary/', views.trading_summary),
    path('positions/', views.position_list),
    path('accounts/', views.account_list),
]

