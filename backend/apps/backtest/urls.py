from django.urls import path
from . import views

urlpatterns = [
    path("results/", views.result_list),
    path("results/create/", views.result_create),
    path("results/<uuid:pk>/", views.result_detail),
    path("results/<uuid:pk>/detail/", views.result_detail_full),
    path("results/<uuid:pk>/trades/", views.result_trades),
    path("results/<uuid:pk>/review/", views.result_review),
    path("results/<uuid:pk>/rerun/", views.result_rerun),
    # Grid search
    path("grid-search/", views.grid_search_create),
    path("grid-search/<uuid:pk>/", views.grid_search_detail),
    path("grid-search/<uuid:pk>/results/", views.grid_search_results),
    path("grid-search/<uuid:pk>/cancel/", views.grid_search_cancel),
]
