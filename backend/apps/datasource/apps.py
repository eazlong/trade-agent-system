"""
DataSource Application Configuration
"""

from django.apps import AppConfig


class DataSourceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.datasource"
    verbose_name = "数据源管理"

    def ready(self):
        # 导入数据源模块，触发 @DataSourceRegistry.register 装饰器注册
        # 数据源实例仍然懒加载，首次调用 DataSourceRegistry.get() 时才实例化
        from apps.datasource.sources import BinanceDataSource  # noqa: F401
