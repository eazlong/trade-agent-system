"""
Portfolio 模型单元测试
"""

from decimal import Decimal

from apps.strategy_engine.base import Insight, StrategyContext
from apps.strategy_engine.portfolio import (
    ConfidenceWeightedPortfolio,
    EqualWeightPortfolio,
    SingleAssetPortfolio,
)


def make_ctx(
    symbol="BTC/USDT",
    balance=Decimal("10000"),
    position=Decimal("0"),
    params=None,
    prices=None,
):
    ctx = StrategyContext(
        symbol=symbol,
        timeframe="1h",
        mode="backtest",
        params=params or {"quantity": Decimal("0.01")},
        balance=balance,
        position=position,
    )
    if prices:
        for s, p in prices.items():
            ctx.set_price(s, p)
    return ctx


class TestSingleAssetPortfolio:
    def test_buy_with_quantity(self):
        ctx = make_ctx(position=Decimal("0.5"), prices={"BTC/USDT": Decimal("50000")})
        insights = [
            Insight(
                symbol="BTC/USDT",
                direction="buy",
                confidence=1.0,
                period="1h",
                source="test",
                quantity=Decimal("0.1"),
            )
        ]
        model = SingleAssetPortfolio()
        targets = model.allocate(insights, ctx)
        assert len(targets) == 1
        assert targets[0].target_quantity == Decimal("0.6")

    def test_sell_creates_zero_target(self):
        ctx = make_ctx(position=Decimal("0.5"))
        insights = [
            Insight(
                symbol="BTC/USDT",
                direction="sell",
                confidence=1.0,
                period="1h",
                source="test",
            )
        ]
        model = SingleAssetPortfolio()
        targets = model.allocate(insights, ctx)
        assert len(targets) == 1
        assert targets[0].target_quantity == Decimal("0")

    def test_sell_skipped_when_no_position(self):
        ctx = make_ctx(position=Decimal("0"))
        insights = [
            Insight(
                symbol="BTC/USDT",
                direction="sell",
                confidence=1.0,
                period="1h",
                source="test",
            )
        ]
        model = SingleAssetPortfolio()
        targets = model.allocate(insights, ctx)
        assert len(targets) == 0

    def test_hold_ignored(self):
        ctx = make_ctx()
        insights = [
            Insight(
                symbol="BTC/USDT",
                direction="hold",
                confidence=0.5,
                period="1h",
                source="test",
            )
        ]
        model = SingleAssetPortfolio()
        targets = model.allocate(insights, ctx)
        assert len(targets) == 0

    def test_buy_fallback_to_params(self):
        ctx = make_ctx(params={"quantity": Decimal("0.05")})
        insights = [
            Insight(
                symbol="BTC/USDT",
                direction="buy",
                confidence=1.0,
                period="1h",
                source="test",
            )
        ]
        model = SingleAssetPortfolio()
        targets = model.allocate(insights, ctx)
        assert len(targets) == 1
        assert targets[0].target_quantity == Decimal("0.05")


class TestEqualWeightPortfolio:
    def test_equal_weights(self):
        ctx = make_ctx(
            balance=Decimal("10000"),
            prices={"BTC/USDT": Decimal("50000"), "ETH/USDT": Decimal("3000")},
        )
        insights = [
            Insight(
                symbol="BTC/USDT",
                direction="buy",
                confidence=0.8,
                period="1h",
                source="test",
            ),
            Insight(
                symbol="ETH/USDT",
                direction="buy",
                confidence=0.9,
                period="1h",
                source="test",
            ),
        ]
        model = EqualWeightPortfolio()
        targets = model.allocate(insights, ctx)
        assert len(targets) == 2
        for t in targets:
            assert t.target_weight is not None
            assert abs(t.target_weight - 0.5) < 0.001

    def test_empty_insights(self):
        ctx = make_ctx()
        model = EqualWeightPortfolio()
        targets = model.allocate([], ctx)
        assert len(targets) == 0

    def test_hold_not_participating(self):
        ctx = make_ctx()
        insights = [
            Insight(
                symbol="BTC/USDT",
                direction="hold",
                confidence=0.5,
                period="1h",
                source="test",
            ),
        ]
        model = EqualWeightPortfolio()
        targets = model.allocate(insights, ctx)
        assert len(targets) == 0

    def test_target_weight_field_filled(self):
        ctx = make_ctx(
            balance=Decimal("10000"),
            prices={"BTC/USDT": Decimal("50000")},
        )
        insights = [
            Insight(
                symbol="BTC/USDT",
                direction="buy",
                confidence=1.0,
                period="1h",
                source="test",
            ),
        ]
        model = EqualWeightPortfolio()
        targets = model.allocate(insights, ctx)
        assert len(targets) == 1
        assert targets[0].target_weight == 1.0


class TestConfidenceWeightedPortfolio:
    def test_confidence_weighted(self):
        ctx = make_ctx(
            balance=Decimal("10000"),
            prices={"BTC/USDT": Decimal("50000"), "ETH/USDT": Decimal("3000")},
        )
        insights = [
            Insight(
                symbol="BTC/USDT",
                direction="buy",
                confidence=0.6,
                period="1h",
                source="test",
            ),
            Insight(
                symbol="ETH/USDT",
                direction="buy",
                confidence=0.4,
                period="1h",
                source="test",
            ),
        ]
        model = ConfidenceWeightedPortfolio()
        targets = model.allocate(insights, ctx)
        assert len(targets) == 2
        btc_target = [t for t in targets if t.symbol == "BTC/USDT"][0]
        eth_target = [t for t in targets if t.symbol == "ETH/USDT"][0]
        assert abs(btc_target.target_weight - 0.6) < 0.001
        assert abs(eth_target.target_weight - 0.4) < 0.001

    def test_empty_insights(self):
        ctx = make_ctx()
        model = ConfidenceWeightedPortfolio()
        targets = model.allocate([], ctx)
        assert len(targets) == 0

    def test_all_zero_confidence(self):
        ctx = make_ctx()
        insights = [
            Insight(
                symbol="BTC/USDT",
                direction="buy",
                confidence=0.0,
                period="1h",
                source="test",
            ),
        ]
        model = ConfidenceWeightedPortfolio()
        targets = model.allocate(insights, ctx)
        assert len(targets) == 0
