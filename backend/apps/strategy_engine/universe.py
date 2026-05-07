"""
Universe Selection Models — Phase 2

标的筛选模型。策略通过 universe 模型决定可交易的 symbol 集合。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
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
    """固定白名单 Universe — 从配置返回预设的 symbol 列表。"""

    def __init__(self, symbols: list[str]):
        self.symbols = list(symbols)

    def select(self, context: "StrategyContext") -> list[str]:
        return list(self.symbols)


class VolumeTopUniverse(BaseUniverseModel):
    """按 24h 成交量排序取 Top-N 的 Universe。

    需要 context 提供 market_data 方法或属性来获取市场数据。
    如果 context 没有 market_data，fallback 到 [context.symbol]。
    """

    def __init__(
        self,
        top_n: int = 5,
        min_volume_usdt: float = 1_000_000,
        quote_asset: str = "USDT",
    ):
        self.top_n = top_n
        self.min_volume_usdt = min_volume_usdt
        self.quote_asset = quote_asset

    def select(self, context: "StrategyContext") -> list[str]:
        market_data = getattr(context, "market_data", None)
        if market_data is None:
            return [context.symbol]

        # market_data 应为 dict[str, dict]，每个 symbol 有 volume_24h 字段
        candidates = []
        for symbol, info in market_data.items():
            if not symbol.endswith(self.quote_asset):
                continue
            volume = Decimal(str(info.get("volume_24h", 0)))
            if volume >= self.min_volume_usdt:
                candidates.append((symbol, volume))

        # 按成交量降序排序，取 Top-N
        candidates.sort(key=lambda x: x[1], reverse=True)
        if not candidates:
            return [context.symbol]
        return [s for s, _ in candidates[: self.top_n]]


class HybridUniverse(BaseUniverseModel):
    """混合 Universe — 固定候选池 ∩ 动态过滤 → Top-N。

    先用 min_volume_usdt 过滤候选池中满足流动性要求的标的，
    再按成交量排序取 Top-N。如果过滤后无候选，fallback 到
    固定候选池前 top_n 个。
    """

    def __init__(
        self,
        candidates: list[str],
        min_volume_usdt: float = 1_000_000,
        top_n: int = 3,
    ):
        self.candidates = list(candidates)
        self.min_volume_usdt = min_volume_usdt
        self.top_n = top_n

    def select(self, context: "StrategyContext") -> list[str]:
        market_data = getattr(context, "market_data", None)
        if market_data is None:
            # 无市场数据时，fallback 到固定候选池前 top_n 个
            return self.candidates[: self.top_n]

        filtered = []
        for symbol in self.candidates:
            info = market_data.get(symbol, {})
            volume = Decimal(str(info.get("volume_24h", 0)))
            if volume >= self.min_volume_usdt:
                filtered.append((symbol, volume))

        if not filtered:
            # 空候选池：fallback 到固定候选池前 top_n 个
            return self.candidates[: self.top_n]

        filtered.sort(key=lambda x: x[1], reverse=True)
        return [s for s, _ in filtered[: self.top_n]]
