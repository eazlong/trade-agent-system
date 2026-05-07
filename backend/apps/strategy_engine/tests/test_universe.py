"""
Universe 模型单元测试
"""

from apps.strategy_engine.universe import (
    FixedListUniverse,
    HybridUniverse,
    VolumeTopUniverse,
)


class FakeContext:
    def __init__(self, symbol="BTC/USDT", market_data=None):
        self.symbol = symbol
        self.market_data = market_data or {}


class TestFixedListUniverse:
    def test_returns_white_list(self):
        symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
        uni = FixedListUniverse(symbols)
        ctx = FakeContext()
        assert uni.select(ctx) == symbols

    def test_returns_copy_not_original(self):
        symbols = ["BTC/USDT", "ETH/USDT"]
        uni = FixedListUniverse(symbols)
        ctx = FakeContext()
        result = uni.select(ctx)
        result.append("XRP/USDT")
        assert len(uni.select(ctx)) == 2

    def test_empty_list(self):
        uni = FixedListUniverse([])
        ctx = FakeContext()
        assert uni.select(ctx) == []


class TestVolumeTopUniverse:
    def test_returns_top_n_by_volume(self):
        market_data = {
            "BTC/USDT": {"volume_24h": 100_000_000},
            "ETH/USDT": {"volume_24h": 50_000_000},
            "SOL/USDT": {"volume_24h": 200_000},
            "SHIT/USDT": {"volume_24h": 100},
        }
        uni = VolumeTopUniverse(top_n=2, min_volume_usdt=1_000)
        ctx = FakeContext(market_data=market_data)
        result = uni.select(ctx)
        assert result == ["BTC/USDT", "ETH/USDT"]

    def test_filters_below_min_volume(self):
        market_data = {
            "BTC/USDT": {"volume_24h": 100_000_000},
            "LOW/USDT": {"volume_24h": 500},
        }
        uni = VolumeTopUniverse(min_volume_usdt=10_000)
        ctx = FakeContext(market_data=market_data)
        result = uni.select(ctx)
        assert result == ["BTC/USDT"]

    def test_fallback_to_symbol_without_market_data(self):
        uni = VolumeTopUniverse(top_n=5)
        ctx = FakeContext()
        assert uni.select(ctx) == ["BTC/USDT"]

    def test_filters_by_quote_asset(self):
        market_data = {
            "BTC/USDT": {"volume_24h": 100_000_000},
            "BTC/BTC": {"volume_24h": 50_000_000},
        }
        uni = VolumeTopUniverse(quote_asset="USDT")
        ctx = FakeContext(market_data=market_data)
        result = uni.select(ctx)
        assert result == ["BTC/USDT"]


class TestHybridUniverse:
    def test_intersection_and_top_n(self):
        candidates = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"]
        market_data = {
            "BTC/USDT": {"volume_24h": 100_000_000},
            "ETH/USDT": {"volume_24h": 50_000_000},
            "SOL/USDT": {"volume_24h": 200_000},
            "XRP/USDT": {"volume_24h": 100},
        }
        uni = HybridUniverse(candidates, min_volume_usdt=1_000, top_n=2)
        ctx = FakeContext(market_data=market_data)
        result = uni.select(ctx)
        assert result == ["BTC/USDT", "ETH/USDT"]

    def test_fallback_when_no_market_data(self):
        candidates = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
        uni = HybridUniverse(candidates, top_n=2)
        ctx = FakeContext()
        result = uni.select(ctx)
        assert result == ["BTC/USDT", "ETH/USDT"]

    def test_fallback_when_all_filtered(self):
        candidates = ["A/USDT", "B/USDT"]
        market_data = {
            "A/USDT": {"volume_24h": 100},
            "B/USDT": {"volume_24h": 50},
        }
        uni = HybridUniverse(candidates, min_volume_usdt=10_000, top_n=1)
        ctx = FakeContext(market_data=market_data)
        result = uni.select(ctx)
        # Fallback to fixed candidates[:top_n]
        assert result == ["A/USDT"]

    def test_empty_candidates(self):
        uni = HybridUniverse([], top_n=3)
        ctx = FakeContext()
        assert uni.select(ctx) == []
