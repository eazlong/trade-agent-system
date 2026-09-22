"""Regime Application Configuration."""

from django.apps import AppConfig


class RegimeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.regime"
    verbose_name = "行情阶段机制"

    def ready(self):
        # 本期无注册表、无后台线程：判定与切片都由显式任务驱动，
        # 不在 app 加载时启动任何东西（避免 import 期副作用）。
        pass
