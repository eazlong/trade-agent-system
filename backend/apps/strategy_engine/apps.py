from django.apps import AppConfig


class StrategyEngineConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.strategy_engine"
    verbose_name = "Strategy Engine"

    def ready(self):
        """应用启动时自动发现并注册策略"""
        import os
        from .registry import StrategyRegistry

        strategy_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "strategies",
        )
        StrategyRegistry.set_strategy_path(strategy_path)
        StrategyRegistry.discover()
