"""
Portfolio Construction Models — Phase 2

组合构建模型。将策略洞察（Insight）转化为带权重的目标持仓（PortfolioTarget）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal, ROUND_HALF_UP
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import Insight, PortfolioTarget, StrategyContext


class BasePortfolioModel(ABC):
    """组合构建模型基类。"""

    @abstractmethod
    def allocate(
        self,
        insights: list["Insight"],
        context: "StrategyContext",
    ) -> list["PortfolioTarget"]:
        """将多个 Insight 转化为带权重的 PortfolioTarget 列表。

        context 必须提供:
        - positions: dict[str, Decimal] — 各 symbol 当前持仓
        - total_capital: Decimal — 总可用资金
        - current_prices: dict[str, Decimal] — 各 symbol 当前价格
        """
        ...


class SingleAssetPortfolio(BasePortfolioModel):
    """单标的组合 — 保持 Phase 1 行为，直接使用 Insight.quantity 或 fallback params。"""

    def allocate(
        self,
        insights: list["Insight"],
        context: "StrategyContext",
    ) -> list["PortfolioTarget"]:
        targets = []
        for ins in insights:
            if ins.direction == "buy":
                qty = (
                    ins.quantity
                    if ins.quantity is not None
                    else Decimal(
                        str(
                            context.params.get(
                                "quantity", context.params.get("position_size", 0)
                            )
                        )
                    )
                )
                if qty <= Decimal("0"):
                    continue
                target_qty = context.position + qty
                targets.append(_make_target(ins.symbol, target_qty, ins.signal_name))
            elif ins.direction == "sell":
                if context.position <= Decimal("0"):
                    continue
                # 卖出：目标持仓量为 0，diff = 0 - position → 正确卖出
                targets.append(_make_target(ins.symbol, Decimal("0"), ins.signal_name))
            # hold = 不操作
        return targets


class EqualWeightPortfolio(BasePortfolioModel):
    """等权重分配 — N 个 Insight 各 1/N 资金。"""

    def allocate(
        self,
        insights: list["Insight"],
        context: "StrategyContext",
    ) -> list["PortfolioTarget"]:
        buy_insights = [i for i in insights if i.direction == "buy"]
        if not buy_insights:
            return []

        weight = Decimal("1") / Decimal(str(len(buy_insights)))
        weight = weight.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

        portfolio_ctx = context.to_portfolio_context()
        targets = []
        for ins in buy_insights:
            price = portfolio_ctx.get_price(ins.symbol)
            if price is None or price <= 0:
                qty = ins.quantity if ins.quantity is not None else Decimal("0")
                if qty <= Decimal("0"):
                    continue
                targets.append(
                    _make_target(ins.symbol, qty, ins.signal_name, float(weight))
                )
                continue

            allocated_capital = portfolio_ctx.total_capital * weight
            qty = (allocated_capital / price).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            )
            if qty <= Decimal("0"):
                continue
            targets.append(
                _make_target(ins.symbol, qty, ins.signal_name, float(weight))
            )

        return targets


class PctCapitalPortfolio(BasePortfolioModel):
    """按总资金百分比开仓 — 每次用 total_capital * position_pct 买入。

    用法：策略的 params_schema 中设置 position_pct（如 0.1 表示 10%），
    引擎根据当前价格和分配资金自动计算买入数量。
    """

    def allocate(
        self,
        insights: list["Insight"],
        context: "StrategyContext",
    ) -> list["PortfolioTarget"]:
        pct = Decimal(
            str(
                context.params.get(
                    "position_pct", context.params.get("position_size_pct", 0.1)
                )
            )
        )
        if not (Decimal("0") < pct <= Decimal("1")):
            return []

        portfolio_ctx = context.to_portfolio_context()
        targets = []
        for ins in insights:
            if ins.direction != "buy":
                # 卖出：清仓
                if ins.direction == "sell" and context.position > Decimal("0"):
                    targets.append(
                        _make_target(ins.symbol, Decimal("0"), ins.signal_name)
                    )
                continue

            price = portfolio_ctx.get_price(ins.symbol)
            if price is None or price <= Decimal("0"):
                continue

            allocated = portfolio_ctx.total_capital * pct
            qty = (allocated / price).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            )
            if qty <= Decimal("0"):
                continue

            target_qty = context.position + qty
            targets.append(
                _make_target(ins.symbol, target_qty, ins.signal_name, float(pct))
            )

        return targets


class ConfidenceWeightedPortfolio(BasePortfolioModel):
    """按 confidence 加权分配 — weight_i = conf_i / sum(conf)。"""

    def allocate(
        self,
        insights: list["Insight"],
        context: "StrategyContext",
    ) -> list["PortfolioTarget"]:
        buy_insights = [i for i in insights if i.direction == "buy"]
        if not buy_insights:
            return []

        total_conf = Decimal(str(sum(i.confidence for i in buy_insights)))
        if total_conf <= 0:
            return []

        portfolio_ctx = context.to_portfolio_context()
        targets = []
        for ins in buy_insights:
            weight_dec = Decimal(str(ins.confidence)) / total_conf
            weight_dec = weight_dec.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

            price = portfolio_ctx.get_price(ins.symbol)
            if price is None or price <= 0:
                qty = ins.quantity if ins.quantity is not None else Decimal("0")
                if qty <= Decimal("0"):
                    continue
                targets.append(
                    _make_target(ins.symbol, qty, ins.signal_name, float(weight_dec))
                )
                continue

            allocated_capital = portfolio_ctx.total_capital * weight_dec
            qty = (allocated_capital / price).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            )
            if qty <= Decimal("0"):
                continue
            targets.append(
                _make_target(ins.symbol, qty, ins.signal_name, float(weight_dec))
            )

        return targets


def _make_target(
    symbol: str,
    quantity: Decimal,
    reason: str,
    weight: float | None = None,
) -> "PortfolioTarget":
    from .base import PortfolioTarget

    return PortfolioTarget(
        symbol=symbol,
        target_quantity=quantity,
        reason=reason,
        target_weight=weight,
    )
