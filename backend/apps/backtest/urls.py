from django.urls import path
from . import views

urlpatterns = [
    path('results/', views.result_list),
    path('results/<uuid:pk>/', views.result_detail),
    path('results/<uuid:pk>/detail/', views.result_detail_full),
    path('results/<uuid:pk>/trades/', views.result_trades),
    path('results/<uuid:pk>/review/', views.result_review),
]
