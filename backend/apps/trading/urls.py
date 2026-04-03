from django.urls import path
from . import views

urlpatterns = [
    path('orders/', views.order_list),
    path('orders/<uuid:pk>/', views.order_detail),
    path('strategies/', views.strategy_list),
]

