from django.apps import AppConfig


class StrategyEngineConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.strategy_engine"
    verbose_name = "Strategy Engine"

    def ready(self):
        """应用启动时自动发现并注册策略"""
        import os
        from pathlib import Path

        from .registry import StrategyRegistry

        # 扫描动态生成的策略目录: ~/.tradelogx/strategies/
        dynamic_path = str(Path.home() / ".tradelogx" / "strategies")
        StrategyRegistry.set_strategy_path(dynamic_path)
        if os.path.isdir(dynamic_path):
            StrategyRegistry.discover()
