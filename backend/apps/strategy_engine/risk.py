"""
Risk Management Models — Phase 2

风控检查模型。链式执行，每个模型过滤不满足条件的 target。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import PortfolioTarget, StrategyContext

logger = logging.getLogger(__name__)


class BaseRiskModel(ABC):
    """风控检查模型基类。链式执行，每个模型过滤不满足条件的 target。

    约定:
    - 方法命名为 filter()，明确表达过滤语义
    - 不修改传入的 targets，返回新列表（不可变语义）
    - 单个模型抛异常时应能安全回滚（链上已生效的过滤无法回滚）
    """

    @abstractmethod
    def filter(
        self,
        targets: list["PortfolioTarget"],
        context: "StrategyContext",
    ) -> list["PortfolioTarget"]:
        """返回通过风控过滤的 targets 子集。不修改传入列表。"""
        ...


class PositionSizeRisk(BaseRiskModel):
    """单标的仓位风控 — 单标的占总资金比例不超过配置上限。"""

    def __init__(self, max_pct: float = 0.20):
        if not (0 < max_pct <= 1):
            raise ValueError(f"max_pct must be in (0, 1], got {max_pct}")
        self.max_pct = max_pct

    def filter(
        self,
        targets: list["PortfolioTarget"],
        context: "StrategyContext",
    ) -> list["PortfolioTarget"]:
        portfolio_ctx = context.to_portfolio_context()
        total = portfolio_ctx.total_capital
        if total <= 0:
            return list(targets)

        max_value = total * Decimal(str(self.max_pct))
        passed = []
        for t in targets:
            price = portfolio_ctx.get_price(t.symbol)
            if price is None or price <= 0:
                passed.append(t)
                continue
            target_value = t.target_quantity * price
            if target_value <= max_value:
                passed.append(t)
            else:
                logger.warning(
                    f"[PositionSizeRisk] rejected {t.symbol}: "
                    f"value={target_value} > max={max_value} "
                    f"(pct={self.max_pct})"
                )
        return passed


class TotalExposureRisk(BaseRiskModel):
    """总暴露风控 — 所有持仓总价值不超过总资金 × 暴露上限。"""

    def __init__(self, max_exposure_pct: float = 0.80):
        if not (0 < max_exposure_pct <= 1):
            raise ValueError(
                f"max_exposure_pct must be in (0, 1], got {max_exposure_pct}"
            )
        self.max_exposure_pct = max_exposure_pct

    def filter(
        self,
        targets: list["PortfolioTarget"],
        context: "StrategyContext",
    ) -> list["PortfolioTarget"]:
        portfolio_ctx = context.to_portfolio_context()
        total = portfolio_ctx.total_capital
        if total <= 0:
            return list(targets)

        max_total_exposure = total * Decimal(str(self.max_exposure_pct))
        passed = []
        current_exposure = Decimal("0")

        for t in targets:
            price = portfolio_ctx.get_price(t.symbol)
            if price is None or price <= 0:
                passed.append(t)
                continue

            target_value = t.target_quantity * price
            if current_exposure + target_value <= max_total_exposure:
                passed.append(t)
                current_exposure += target_value
            else:
                logger.warning(
                    f"[TotalExposureRisk] rejected {t.symbol}: "
                    f"total_exposure={current_exposure + target_value} "
                    f"> max={max_total_exposure}"
                )
        return passed


class MaxPositionsRisk(BaseRiskModel):
    """持仓数量风控 — 同时持仓标的总数不超过配置上限。"""

    def __init__(self, max_positions: int = 5):
        if max_positions < 1:
            raise ValueError(f"max_positions must be >= 1, got {max_positions}")
        self.max_positions = max_positions

    def filter(
        self,
        targets: list["PortfolioTarget"],
        context: "StrategyContext",
    ) -> list["PortfolioTarget"]:
        existing = {s for s, p in context.positions.items() if p > 0}
        existing_count = len(existing)
        new_positions = 0
        passed = []

        for t in targets:
            # 已持有的标的不计入新持仓数
            if t.symbol not in existing:
                if existing_count + new_positions >= self.max_positions:
                    logger.warning(
                        f"[MaxPositionsRisk] rejected {t.symbol}: "
                        f"max positions ({self.max_positions}) reached"
                    )
                    break
                new_positions += 1
            passed.append(t)

        return passed


class StopLossRisk(BaseRiskModel):
    """止损风控 — 当某标的浮亏超过阈值时插入平仓 target 并置顶。

    Phase 2 简化版：仅记录日志，不改变 targets 顺序。
    完整的止损逻辑需要 PnL 计算，依赖持仓成本价（Phase 2 后期增强）。
    """

    def __init__(self, threshold_pct: float = 0.05):
        if not (0 < threshold_pct <= 1):
            raise ValueError(f"threshold_pct must be in (0, 1], got {threshold_pct}")
        self.threshold_pct = threshold_pct

    def filter(
        self,
        targets: list["PortfolioTarget"],
        context: "StrategyContext",
    ) -> list["PortfolioTarget"]:
        # 简化版：遍历所有 targets，检查是否有标的需要止损
        # 完整的 PnL 计算需要持仓成本价，这在 Phase 2 后期补充
        for t in targets:
            current_pos = context.positions.get(t.symbol, Decimal("0"))
            if current_pos <= 0:
                continue

            price = context.current_prices.get(t.symbol)
            if price is None or price <= 0:
                continue

            # 标记已检查（通过 metadata 传递）
            t.metadata["stoploss_checked"] = True

        return list(targets)
