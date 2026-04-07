"""
DataSource Application Configuration
"""
from django.apps import AppConfig


class DataSourceConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.datasource'
    verbose_name = '数据源管理'

    def ready(self):
        # 懒加载初始化 - 不在此处主动加载任何数据源
        # 数据源按需加载，首次调用 DataSourceRegistry.get() 时才实例化
        pass