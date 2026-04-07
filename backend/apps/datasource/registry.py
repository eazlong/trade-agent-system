"""
数据源注册中心

使用装饰器模式实现懒加载单例，按需加载数据源。
"""
import asyncio
import threading
from typing import Dict, Type, Optional, Any, Callable
from functools import wraps
from collections import OrderedDict


class DataSourceRegistry:
    """
    数据源注册中心 - 单例模式

    使用类装饰器 @DataSourceRegistry.register 实现懒加载：
    - 首次调用 get() 时才实例化数据源
    - 单例模式确保同一数据源只有一个实例
    - 支持异步初始化
    """

    _instance: Optional['DataSourceRegistry'] = None
    _lock = threading.Lock()

    # 注册表：{name: class}
    _registry: Dict[str, Type] = {}

    # 实例缓存：{name: instance} (懒加载后存储)
    _instances: Dict[str, Any] = {}

    # 实例化锁（防止并发创建同一实例）
    _instance_locks: Dict[str, threading.Lock] = {}

    def __new__(cls) -> 'DataSourceRegistry':
        """单例模式"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def register(cls, name: str = None) -> Callable:
        """
        数据源注册装饰器

        Usage:
            @DataSourceRegistry.register('binance')
            class BinanceDataSource(BaseDataSource):
                ...

            # 或者使用类属性 name
            @DataSourceRegistry.register()
            class BinanceDataSource(BaseDataSource):
                name = 'binance'
        """
        def decorator(source_class: Type) -> Type:
            # 获取数据源名称
            source_name = name or getattr(source_class, 'name', None)
            if not source_name:
                raise ValueError(f"DataSource must have a 'name' attribute or be registered with explicit name")

            # 注册类（不实例化）
            cls._registry[source_name] = source_class

            # 创建实例化锁
            cls._instance_locks[source_name] = threading.Lock()

            # 添加便捷方法
            source_class.get = lambda: cls.get(source_name)
            source_class.is_loaded = lambda: source_name in cls._instances

            return source_class

        return decorator

    @classmethod
    def get(cls, name: str) -> Any:
        """
        获取数据源实例（懒加载）

        Args:
            name: 数据源名称

        Returns:
            数据源实例（首次调用时创建）
        """
        if name not in cls._registry:
            raise KeyError(f"DataSource '{name}' not registered. Available: {list(cls._registry.keys())}")

        # 检查是否已实例化
        if name in cls._instances:
            return cls._instances[name]

        # 获取实例化锁（防止并发创建）
        lock = cls._instance_locks.get(name)
        if not lock:
            lock = threading.Lock()
            cls._instance_locks[name] = lock

        with lock:
            # 双重检查（防止锁内重复创建）
            if name in cls._instances:
                return cls._instances[name]

            # 实例化数据源
            source_class = cls._registry[name]
            instance = source_class()

            # 存储实例
            cls._instances[name] = instance

            return instance

    @classmethod
    def get_all_loaded(cls) -> Dict[str, Any]:
        """获取所有已加载的数据源实例"""
        return cls._instances.copy()

    @classmethod
    def list_registered(cls) -> list:
        """列出所有已注册的数据源名称"""
        return list(cls._registry.keys())

    @classmethod
    def is_loaded(cls, name: str) -> bool:
        """检查数据源是否已加载"""
        return name in cls._instances

    @classmethod
    def unload(cls, name: str) -> bool:
        """
        卸载数据源实例

        Args:
            name: 数据源名称

        Returns:
            是否成功卸载
        """
        if name in cls._instances:
            instance = cls._instances[name]

            # 如果有 cleanup 方法，调用它
            if hasattr(instance, 'cleanup'):
                try:
                    instance.cleanup()
                except Exception as e:
                    print(f"Error cleaning up DataSource '{name}': {e}")

            # 移除实例
            del cls._instances[name]
            return True

        return False

    @classmethod
    def clear_all(cls) -> None:
        """清空所有数据源实例（保留注册表）"""
        for name in list(cls._instances.keys()):
            cls.unload(name)


# 全局便捷函数
def get_data_source(name: str) -> Any:
    """获取数据源实例"""
    return DataSourceRegistry.get(name)


def list_data_sources() -> list:
    """列出所有已注册的数据源"""
    return DataSourceRegistry.list_registered()