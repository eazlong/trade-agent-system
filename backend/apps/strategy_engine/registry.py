"""
策略注册中心

复用 AgentRegistry 的装饰器注册模式。
策略类使用 @register_strategy 装饰器自动注册。
"""

from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import BaseStrategy

logger = logging.getLogger(__name__)


class StrategyRegistry:
    """策略注册中心，懒加载单例模式"""

    _instance: StrategyRegistry | None = None
    _strategies: dict[str, type[BaseStrategy]] = {}
    _strategy_path: str = ""
    _initialized = False

    def __init__(self):
        if not self._initialized:
            self._initialized = True

    @classmethod
    def get_instance(cls) -> "StrategyRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def register(cls, strategy_cls: type["BaseStrategy"]) -> type["BaseStrategy"]:
        """装饰器：注册策略类"""
        name = strategy_cls.name
        if name in cls._strategies:
            logger.warning(
                "Strategy '%s' already registered, overwriting with %s",
                name,
                strategy_cls.__name__,
            )
        cls._strategies[name] = strategy_cls
        logger.info("Strategy registered: %s (%s)", name, strategy_cls.__name__)
        return strategy_cls

    @classmethod
    def get(cls, name: str) -> "BaseStrategy":
        """获取策略实例（通过 StrategyLoader 加载后实例化）"""
        strategy_cls = cls._strategies.get(name)
        if strategy_cls is None:
            raise ValueError(f"Strategy not found: {name!r}")
        return strategy_cls

    @classmethod
    def get_class(cls, name: str) -> type["BaseStrategy"]:
        """获取策略类"""
        strategy_cls = cls._strategies.get(name)
        if strategy_cls is None:
            raise ValueError(f"Strategy not found: {name!r}")
        return strategy_cls

    @classmethod
    def list_registered(cls) -> list[str]:
        return list(cls._strategies.keys())

    @classmethod
    def set_strategy_path(cls, path: str) -> None:
        """设置策略文件目录路径"""
        cls._strategy_path = path

    @classmethod
    def discover(cls) -> list[str]:
        """自动发现并注册策略模块"""
        path = cls._strategy_path
        if not path:
            return cls.list_registered()
        return cls.discover_path(path)

    @classmethod
    def discover_path(cls, path: str) -> list[str]:
        """扫描指定目录，发现并注册策略模块"""
        import os
        import sys

        if not path:
            return cls.list_registered()

        if not os.path.isdir(path):
            logger.warning("Strategy path does not exist: %s", path)
            return cls.list_registered()

        discovered = []
        for filename in os.listdir(path):
            if filename.endswith(".py") and not filename.startswith("_"):
                module_name = filename[:-3]
                try:
                    # 确保路径在 sys.path 中
                    if path not in sys.path:
                        sys.path.insert(0, path)

                    # 避免缓存：如果模块已加载，先删除以便重新加载
                    if module_name in sys.modules:
                        del sys.modules[module_name]

                    mod = importlib.import_module(module_name)
                    # 触发装饰器注册
                    for attr_name in dir(mod):
                        attr = getattr(mod, attr_name)
                        if (
                            isinstance(attr, type)
                            and hasattr(attr, "name")
                            and getattr(attr, "__module__", "") == module_name
                        ):
                            from .base import BaseStrategy

                            if (
                                issubclass(attr, BaseStrategy)
                                and attr is not BaseStrategy
                            ):
                                cls.register(attr)
                                discovered.append(attr.name)
                except Exception as e:
                    logger.warning("Failed to discover strategy %s: %s", module_name, e)

        return discovered


def register_strategy(
    name: str | None = None,
) -> callable:
    """装饰器：注册策略类

    用法:
        @register_strategy
        class MyStrategy(BaseStrategy):
            name = "my_strategy"
            ...

        @register_strategy(name="custom_name")
        class MyStrategy(BaseStrategy):
            name = "my_strategy"  # will be overridden
            ...
    """

    def decorator(target):
        StrategyRegistry.register(target)
        return target

    return decorator


def get_strategy(name: str):
    """便捷函数：获取策略实例的类（需要由调用方传入 context 实例化）"""
    return StrategyRegistry.get_class(name)
