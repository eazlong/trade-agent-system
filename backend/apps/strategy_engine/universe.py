"""
Universe Selection Models — MVP 精简版

标的筛选模型。策略通过 universe 模型决定可交易的 symbol 集合。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import StrategyContext


class BaseUniverseModel(ABC):
    """标的筛选模型基类。"""

    @abstractmethod
    def select(self, context: "StrategyContext") -> list[str]:
        """返回可交易的 symbol 列表。"""
        ...


class FixedListUniverse(BaseUniverseModel):
    """固定白名单 Universe — 返回预设的 symbol 列表。"""

    def __init__(self, symbols: list[str]):
        self.symbols = list(symbols)

    def select(self, context: "StrategyContext") -> list[str]:
        return list(self.symbols)
