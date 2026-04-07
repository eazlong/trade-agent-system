"""
数据源应用 URLs
"""
from django.urls import path, include
from .api.urls import urlpatterns as api_urlpatterns

app_name = 'datasource'

urlpatterns = [
    path('api/', include(api_urlpatterns)),
]