"""
Phase 1 集成测试 — _signal_to_insight 转换及管线端到端测试。
"""

from decimal import Decimal

from apps.strategy_engine.base import (
    Insight,
    OrderSignal,
    StrategyContext,
    _signal_to_insight,
    BaseStrategy,
)


class FakeStrategy(BaseStrategy):
    """用于测试的假策略"""

    name = "fake_strategy"

    def __init__(self, context: StrategyContext):
        super().__init__(context)
        self._return_signal = None

    def set_return_signal(self, signal: OrderSignal | None):
        self._return_signal = signal

    def on_bar(self, kline: dict, history: list[dict]) -> OrderSignal | None:
        return self._return_signal


class TestSignalToInsight:
    """_signal_to_insight() 转换测试"""

    def test_basic_conversion(self):
        signal = OrderSignal(
            side="buy",
            quantity=Decimal("0.01"),
            signal_name="entry",
        )
        insight = _signal_to_insight(signal, "TestStrategy")

        assert insight.direction == "buy"
        assert insight.confidence == 1.0
        assert insight.period == ""
        assert insight.source == "TestStrategy"
        assert insight.quantity == Decimal("0.01")
        assert insight.signal_name == "entry"

    def test_sell_conversion(self):
        signal = OrderSignal(
            side="sell",
            quantity=Decimal("0.5"),
            signal_name="exit",
        )
        insight = _signal_to_insight(signal, "TestStrategy")

        assert insight.direction == "sell"
        assert insight.signal_name == "exit"

    def test_symbol_is_empty_by_default(self):
        signal = OrderSignal(
            side="buy",
            quantity=Decimal("0.01"),
        )
        insight = _signal_to_insight(signal, "TestStrategy")
        assert insight.symbol == ""


class TestGenerateInsights:
    """generate_insights() 方法测试"""

    def _make_context(self):
        return StrategyContext(
            symbol="BTC/USDT",
            timeframe="1h",
            mode="backtest",
            params={"quantity": 0.01},
        )

    def test_returns_empty_when_no_signal(self):
        ctx = self._make_context()
        strategy = FakeStrategy(ctx)
        strategy.set_return_signal(None)

        insights = strategy.generate_insights({}, [])
        assert insights == []

    def test_returns_insight_when_signal(self):
        ctx = self._make_context()
        strategy = FakeStrategy(ctx)
        strategy.set_return_signal(
            OrderSignal(side="buy", quantity=Decimal("0.01"), signal_name="entry")
        )

        insights = strategy.generate_insights({}, [])
        assert len(insights) == 1
        assert insights[0].symbol == "BTC/USDT"
        assert insights[0].direction == "buy"

    def test_symbol_injected_from_context(self):
        ctx = self._make_context()
        ctx.symbol = "ETH/USDT"
        strategy = FakeStrategy(ctx)
        strategy.set_return_signal(OrderSignal(side="buy", quantity=Decimal("0.1")))

        insights = strategy.generate_insights({}, [])
        assert insights[0].symbol == "ETH/USDT"


class TestConstructPortfolio:
    """construct_portfolio() 方法测试"""

    def _make_context(self, position: Decimal = Decimal("0")):
        return StrategyContext(
            symbol="BTC/USDT",
            timeframe="1h",
            mode="backtest",
            params={"quantity": 0.01},
            position=position,
        )

    def test_buy_increases_position(self):
        ctx = self._make_context()
        insight = Insight(
            symbol="BTC/USDT",
            direction="buy",
            confidence=0.8,
            period="1h",
            source="Test",
            quantity=Decimal("0.01"),
        )

        targets = BaseStrategy.construct_portfolio(FakeStrategy(ctx), [insight], ctx)
        assert len(targets) == 1
        assert targets[0].target_quantity == Decimal("0.01")

    def test_sell_clears_position(self):
        ctx = self._make_context(position=Decimal("0.5"))
        insight = Insight(
            symbol="BTC/USDT",
            direction="sell",
            confidence=0.8,
            period="1h",
            source="Test",
        )

        targets = BaseStrategy.construct_portfolio(FakeStrategy(ctx), [insight], ctx)
        assert len(targets) == 1
        # sell 时 target_quantity = 0（目标持仓为零），_target_to_order 中 diff = 0 - position
        assert targets[0].target_quantity == Decimal("0")

    def test_sell_no_position_returns_empty(self):
        ctx = self._make_context(position=Decimal("0"))
        insight = Insight(
            symbol="BTC/USDT",
            direction="sell",
            confidence=0.8,
            period="1h",
            source="Test",
        )

        targets = BaseStrategy.construct_portfolio(FakeStrategy(ctx), [insight], ctx)
        assert targets == []

    def test_hold_no_action(self):
        ctx = self._make_context()
        insight = Insight(
            symbol="BTC/USDT",
            direction="hold",
            confidence=0.5,
            period="1h",
            source="Test",
        )

        targets = BaseStrategy.construct_portfolio(FakeStrategy(ctx), [insight], ctx)
        assert targets == []

    def test_empty_insights_returns_empty(self):
        ctx = self._make_context()
        targets = BaseStrategy.construct_portfolio(FakeStrategy(ctx), [], ctx)
        assert targets == []

    def test_fallback_to_params_quantity(self):
        ctx = self._make_context()
        insight = Insight(
            symbol="BTC/USDT",
            direction="buy",
            confidence=0.8,
            period="1h",
            source="Test",
            quantity=None,
        )

        targets = BaseStrategy.construct_portfolio(FakeStrategy(ctx), [insight], ctx)
        assert len(targets) == 1
        assert targets[0].target_quantity == Decimal("0.01")

    def test_multiple_insights_to_multiple_targets(self):
        ctx = self._make_context(position=Decimal("0.1"))
        insights = [
            Insight(
                symbol="BTC/USDT",
                direction="buy",
                confidence=0.8,
                period="1h",
                source="Test",
                quantity=Decimal("0.01"),
            ),
            Insight(
                symbol="BTC/USDT",
                direction="sell",
                confidence=0.9,
                period="1h",
                source="Test",
            ),
        ]

        targets = BaseStrategy.construct_portfolio(FakeStrategy(ctx), insights, ctx)
        assert len(targets) == 2
        # buy: position(0.1) + quantity(0.01) = 0.11
        assert targets[0].target_quantity == Decimal("0.11")
        # sell: target_quantity = 0（目标持仓为零，表示清仓）
        assert targets[1].target_quantity == Decimal("0")
