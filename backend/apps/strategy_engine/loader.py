"""
策略加载器

安全导入策略 .py 文件并实例化。
支持从文件路径加载和从已注册名称加载。
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import BaseStrategy, StrategyContext

logger = logging.getLogger(__name__)


class StrategyLoader:
    """策略加载器，从文件路径或模块名加载策略"""

    @staticmethod
    def load_from_path(
        file_path: str, context: "StrategyContext"
    ) -> "BaseStrategy":
        """
        从 Python 文件路径加载策略。

        Args:
            file_path: 策略文件的绝对或相对路径
            context: 策略运行上下文

        Returns:
            策略实例

        Raises:
            FileNotFoundError: 策略文件不存在
            ValueError: 文件中未找到策略类
        """
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"Strategy file not found: {file_path}")

        module_name = os.path.splitext(os.path.basename(file_path))[0]

        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec is None or spec.loader is None:
            raise ValueError(f"Cannot load module from {file_path}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        # 查找策略类
        strategy_cls = _find_strategy_class(module)
        return strategy_cls(context)

    @staticmethod
    def load_from_name(
        strategy_name: str, context: "StrategyContext"
    ) -> "BaseStrategy":
        """
        从已注册策略名称加载策略。

        Args:
            strategy_name: 策略名称（StrategyRegistry 中注册的 name）
            context: 策略运行上下文

        Returns:
            策略实例
        """
        from .registry import StrategyRegistry

        strategy_cls = StrategyRegistry.get_class(strategy_name)
        return strategy_cls(context)

    @staticmethod
    def validate_strategy_class(cls: type) -> bool:
        """验证策略类是否有效"""
        from .base import BaseStrategy

        if not issubclass(cls, BaseStrategy):
            return False
        if cls is BaseStrategy:
            return False
        # 检查必需属性
        if not getattr(cls, "name", None):
            return False
        # 检查必需方法
        if not hasattr(cls, "on_bar"):
            return False
        return True


def _find_strategy_class(module) -> type["BaseStrategy"]:
    """
    在模块中查找有效的策略类。

    策略类必须：
    1. 继承 BaseStrategy
    2. 不是 BaseStrategy 本身
    3. 有 name 属性
    """
    from .base import BaseStrategy

    strategies = []
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if (
            isinstance(attr, type)
            and issubclass(attr, BaseStrategy)
            and attr is not BaseStrategy
            and hasattr(attr, "name")
            and getattr(attr, "__module__", "") == module.__name__
        ):
            strategies.append(attr)

    if not strategies:
        raise ValueError(
            f"No valid strategy class found in module {module.__name__}"
        )

    if len(strategies) > 1:
        names = [s.name for s in strategies]
        logger.warning(
            "Multiple strategy classes found in module, using first: %s", names
        )

    return strategies[0]
