"""
DataSource Application Configuration
"""

import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class DataSourceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.datasource"
    verbose_name = "数据源管理"

    def ready(self):
        # 导入数据源模块，触发 @DataSourceRegistry.register 装饰器注册
        # 数据源实例仍然懒加载，首次调用 DataSourceRegistry.get() 时才实例化
        from apps.datasource.sources import BinanceDataSource  # noqa: F401

        # 启动 WS 实时行情供给（head bar 数据源，纯 WS 流，无 REST 回退）
        # 详见 ws_runner.py：持久事件循环 + supervisor（断线自动重连）
        from apps.datasource.base import DataType, MarketType
        from apps.datasource.ws_runner import (
            DEFAULT_FEED_SYMBOLS,
            start_market_feed,
        )

        try:
            start_market_feed(
                source="binance",
                symbols=DEFAULT_FEED_SYMBOLS,
                data_type=DataType.TICKER,
                market_type=MarketType.SPOT,
            )
        except Exception as e:
            logger.error("[DataSource] failed to start market feed: %s", e)
