"""OrderSerializer 触发策略字段测试。

验证 strategy_name 优先读订单上的持久化快照 triggered_strategy，
历史/未回填数据回退到 live_session→strategy 联表。
"""

from decimal import Decimal
from uuid import uuid4

from apps.trading.models import Order, Strategy, LiveSession
from apps.trading.serializers import OrderSerializer


def _order(**kw):
    defaults = {
        "id": uuid4(),
        "symbol": "BTCUSDT",
        "side": "buy",
        "order_type": "market",
        "quantity": Decimal("0.01"),
        "status": "pending",
    }
    defaults.update(kw)
    return Order(**defaults)


def _strategy(name: str) -> Strategy:
    return Strategy(id=uuid4(), name=name, code_path=f"strategies/{name}.py")


def test_strategy_name_prefers_triggered_strategy():
    """有快照时返回快照值，即使联表策略名不同。"""
    order = _order(triggered_strategy="ema_trend_weekday")
    order.live_session = LiveSession(id=uuid4(), strategy=_strategy("old_name"))
    data = OrderSerializer(order).data
    assert data["strategy_name"] == "ema_trend_weekday"
    assert data["triggered_strategy"] == "ema_trend_weekday"


def test_strategy_name_falls_back_to_live_session_when_no_snapshot():
    """无快照时回退到 live_session 策略名。"""
    order = _order(triggered_strategy="")
    order.live_session = LiveSession(
        id=uuid4(), strategy=_strategy("donchian_atr_trend_strategy")
    )
    data = OrderSerializer(order).data
    assert data["strategy_name"] == "donchian_atr_trend_strategy"


def test_strategy_name_none_when_no_strategy():
    """无快照且无联表策略时返回 None（手动单）。"""
    order = _order(triggered_strategy="")
    data = OrderSerializer(order).data
    assert data["strategy_name"] is None
    assert data["triggered_strategy"] == ""
