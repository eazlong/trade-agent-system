"""
Risk 模型单元测试
"""

import copy
from decimal import Decimal

from apps.strategy_engine.base import PortfolioTarget, StrategyContext
from apps.strategy_engine.risk import (
    MaxPositionsRisk,
    PositionSizeRisk,
    StopLossRisk,
    TotalExposureRisk,
)


def make_ctx(
    symbol="BTC/USDT",
    balance=Decimal("10000"),
    position=Decimal("0"),
    positions=None,
    prices=None,
):
    ctx = StrategyContext(
        symbol=symbol,
        timeframe="1h",
        mode="backtest",
        params={},
        balance=balance,
        position=position,
    )
    if positions:
        for s, p in positions.items():
            ctx.set_position(s, p)
    if prices:
        for s, p in prices.items():
            ctx.set_price(s, p)
    return ctx


def make_target(symbol, quantity):
    return PortfolioTarget(
        symbol=symbol,
        target_quantity=Decimal(str(quantity)),
        reason="test",
    )


class TestPositionSizeRisk:
    def test_accepts_within_limit(self):
        ctx = make_ctx(
            balance=Decimal("10000"),
            prices={"BTC/USDT": Decimal("50000")},
        )
        targets = [make_target("BTC/USDT", 0.01)]  # 500 USDT = 5% of 10000
        risk = PositionSizeRisk(max_pct=0.20)
        result = risk.filter(targets, ctx)
        assert len(result) == 1

    def test_rejects_exceeds_limit(self):
        ctx = make_ctx(
            balance=Decimal("10000"),
            prices={"BTC/USDT": Decimal("50000")},
        )
        targets = [make_target("BTC/USDT", 0.1)]  # 5000 USDT = 50% of 10000
        risk = PositionSizeRisk(max_pct=0.20)
        result = risk.filter(targets, ctx)
        assert len(result) == 0

    def test_passes_without_price(self):
        ctx = make_ctx(balance=Decimal("10000"))
        targets = [make_target("BTC/USDT", 0.01)]
        risk = PositionSizeRisk()
        result = risk.filter(targets, ctx)
        assert len(result) == 1

    def test_does_not_modify_input(self):
        ctx = make_ctx(balance=Decimal("10000"), prices={"BTC/USDT": Decimal("50000")})
        targets = [make_target("BTC/USDT", 0.01), make_target("ETH/USDT", 0.1)]
        original = copy.deepcopy(targets)
        risk = PositionSizeRisk(max_pct=0.20)
        risk.filter(targets, ctx)
        assert len(targets) == len(original)


class TestTotalExposureRisk:
    def test_accepts_within_limit(self):
        ctx = make_ctx(
            balance=Decimal("10000"),
            prices={"BTC/USDT": Decimal("50000"), "ETH/USDT": Decimal("3000")},
        )
        targets = [
            make_target("BTC/USDT", 0.01),  # 500 USDT
            make_target("ETH/USDT", 0.1),  # 300 USDT
        ]  # Total 800 USDT = 8% of 10000
        risk = TotalExposureRisk(max_exposure_pct=0.80)
        result = risk.filter(targets, ctx)
        assert len(result) == 2

    def test_rejects_over_exposure(self):
        ctx = make_ctx(
            balance=Decimal("10000"),
            prices={"BTC/USDT": Decimal("50000")},
        )
        targets = [
            make_target("BTC/USDT", 0.1),  # 5000 USDT
            make_target(
                "BTC/USDT", 0.05
            ),  # 2500 USDT (cumulative 7500 = 75%, ok at 80%)
            make_target("BTC/USDT", 0.02),  # 1000 USDT (cumulative 8500 = 85%, reject)
        ]
        risk = TotalExposureRisk(max_exposure_pct=0.80)
        result = risk.filter(targets, ctx)
        assert len(result) == 2  # First two pass, third rejected

    def test_empty_targets(self):
        ctx = make_ctx(balance=Decimal("10000"))
        risk = TotalExposureRisk()
        result = risk.filter([], ctx)
        assert len(result) == 0


class TestMaxPositionsRisk:
    def test_accepts_under_limit(self):
        ctx = make_ctx(positions={"BTC/USDT": Decimal("0")})
        targets = [make_target("BTC/USDT", 0.01), make_target("ETH/USDT", 0.1)]
        risk = MaxPositionsRisk(max_positions=5)
        result = risk.filter(targets, ctx)
        assert len(result) == 2

    def test_limits_new_positions(self):
        ctx = make_ctx(
            positions={"BTC/USDT": Decimal("0.1"), "ETH/USDT": Decimal("0.5")},
        )
        targets = [
            make_target("SOL/USDT", 1),
            make_target("XRP/USDT", 100),
        ]
        # 2 existing, max=3, only 1 new allowed
        risk = MaxPositionsRisk(max_positions=3)
        result = risk.filter(targets, ctx)
        assert len(result) == 1

    def test_does_not_limit_rebalancing(self):
        ctx = make_ctx(
            positions={"BTC/USDT": Decimal("0.5")},
        )
        targets = [make_target("BTC/USDT", 0.1)]  # 已有持仓，rebalance
        risk = MaxPositionsRisk(max_positions=1)
        result = risk.filter(targets, ctx)
        assert len(result) == 1


class TestStopLossRisk:
    def test_passes_all_targets(self):
        ctx = make_ctx(
            positions={"BTC/USDT": Decimal("0.5")},
            prices={"BTC/USDT": Decimal("50000")},
        )
        targets = [make_target("BTC/USDT", 0.1)]
        risk = StopLossRisk(threshold_pct=0.05)
        result = risk.filter(targets, ctx)
        assert len(result) == 1

    def test_empty_targets(self):
        ctx = make_ctx()
        risk = StopLossRisk()
        result = risk.filter([], ctx)
        assert len(result) == 0


class TestRiskChainExecution:
    def test_chain_passes_all(self):
        ctx = make_ctx(
            balance=Decimal("100000"),
            positions={"BTC/USDT": Decimal("0")},
            prices={"BTC/USDT": Decimal("50000")},
        )
        targets = [make_target("BTC/USDT", 0.1)]  # 5000 USDT = 5%
        risk_models = [
            PositionSizeRisk(max_pct=0.20),
            TotalExposureRisk(max_exposure_pct=0.80),
            MaxPositionsRisk(max_positions=5),
        ]
        result = targets
        for rm in risk_models:
            result = rm.filter(result, ctx)
        assert len(result) == 1

    def test_chain_stops_early(self):
        ctx = make_ctx(
            balance=Decimal("10000"),
            positions={"BTC/USDT": Decimal("0")},
            prices={"BTC/USDT": Decimal("50000")},
        )
        targets = [make_target("BTC/USDT", 0.1)]  # 5000 USDT = 50%
        risk_models = [
            PositionSizeRisk(max_pct=0.20),  # Will reject
            MaxPositionsRisk(max_positions=5),
        ]
        result = targets
        for rm in risk_models:
            result = rm.filter(result, ctx)
            if not result:
                break
        assert len(result) == 0
