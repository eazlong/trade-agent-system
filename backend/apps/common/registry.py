from __future__ import annotations

import logging
from typing import Generic, Iterator, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class Registry(Generic[T]):
    """通用注册中心。

    承载"注册 / 查找 / 遍历 / 清理"的通用逻辑。
    任何领域特有的逻辑（如自动发现、懒加载实例化）应放在外层的封装类里。

    支持两种使用方式：
      1. 作为"类全局"封装的内部后端（兼容旧 API）
      2. 作为独立实例直接用于测试，天然隔离
    """

    __slots__ = ("_items", "_name")

    def __init__(self, name: str = "registry") -> None:
        self._items: dict[str, T] = {}
        self._name = name

    # ------ 写 ------
    def register(self, key: str, item: T, *, overwrite: bool = False) -> None:
        if not overwrite and key in self._items:
            logger.debug("[%s] overwrite skipped: %s", self._name, key)
            return
        self._items[key] = item
        logger.debug("[%s] registered: %s", self._name, key)

    def unregister(self, key: str) -> None:
        self._items.pop(key, None)

    # ------ 读 ------
    def get(self, key: str) -> T | None:
        return self._items.get(key)

    def __contains__(self, key: str) -> bool:
        return key in self._items

    def __iter__(self) -> Iterator[T]:
        return iter(self._items.values())

    def __len__(self) -> int:
        return len(self._items)

    def all(self) -> list[T]:
        return list(self._items.values())

    def keys(self) -> list[str]:
        return list(self._items.keys())

    # ------ 清理 ------
    def reset(self) -> None:
        """清空所有已注册项。测试隔离的入口。"""
        self._items.clear()

    def clear(self) -> None:
        """reset() 的别名，兼容 dict 习惯用法。"""
        self.reset()
