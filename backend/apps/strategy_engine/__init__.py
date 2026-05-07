"""
策略统一执行引擎

回测和实盘共用同一份策略代码。
策略继承 BaseStrategy 并实现 on_bar 方法。
"""

from .base import (
    BaseStrategy,
    Insight,
    OrderSignal,
    PortfolioContext,
    PortfolioTarget,
    StrategyContext,
    _signal_to_insight,
)
from .portfolio import (
    BasePortfolioModel,
    ConfidenceWeightedPortfolio,
    EqualWeightPortfolio,
    SingleAssetPortfolio,
)
from .registry import StrategyRegistry, register_strategy
from .risk import (
    BaseRiskModel,
    MaxPositionsRisk,
    PositionSizeRisk,
    StopLossRisk,
    TotalExposureRisk,
)
from .universe import (
    BaseUniverseModel,
    FixedListUniverse,
    HybridUniverse,
    VolumeTopUniverse,
)

__all__ = [
    # 基础数据结构
    "BaseStrategy",
    "Insight",
    "OrderSignal",
    "PortfolioContext",
    "PortfolioTarget",
    "StrategyContext",
    "_signal_to_insight",
    # 注册中心
    "StrategyRegistry",
    "register_strategy",
    # Phase 2: Universe
    "BaseUniverseModel",
    "FixedListUniverse",
    "HybridUniverse",
    "VolumeTopUniverse",
    # Phase 2: Portfolio
    "BasePortfolioModel",
    "ConfidenceWeightedPortfolio",
    "EqualWeightPortfolio",
    "SingleAssetPortfolio",
    # Phase 2: Risk
    "BaseRiskModel",
    "MaxPositionsRisk",
    "PositionSizeRisk",
    "StopLossRisk",
    "TotalExposureRisk",
]
