"""
数据源应用 Models

目前数据源不需要持久化模型，所有数据存储在内存中。
此文件保留以便未来扩展。
"""

from django.db import models


class DataSourceConfig(models.Model):
    """
    数据源配置（可选）

    用于存储用户自定义的数据源配置，如 API Keys 等。
    敏感信息应加密存储。
    """

    name = models.CharField(max_length=64, unique=True)
    config = models.JSONField(default=dict)
    market_types = models.JSONField(
        default=list,
        blank=True,
        help_text='["spot"], ["futures"], 或同时配置。空列表=全部。',
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "datasource_configs"
        verbose_name = "数据源配置"
        verbose_name_plural = "数据源配置"

    def __str__(self):
        return self.name
