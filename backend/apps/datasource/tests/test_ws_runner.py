"""
ws_runner 持久 WS 事件循环与行情供给测试

覆盖：
- 持久事件循环单例与跨线程协程调度（模拟 API 请求线程调用）
- start_market_feed 守卫（pytest 环境不建立真实外网连接）
- ticker 端点纯 WS 存储读取（无 REST 回退）：
  命中返回 200（含 Binance 原始符号兼容），WS 未就绪返回 503
"""

import asyncio
import threading

from django.test import RequestFactory
from rest_framework.permissions import AllowAny

from apps.datasource.api.views import MarketDataViewSet
from apps.datasource.store import get_data_store
from apps.datasource.ws_runner import (
    DEFAULT_FEED_SYMBOLS,
    get_engine,
    run_ws_coroutine,
    start_market_feed,
)


class TestWSEngine:
    """持久事件循环测试"""

    def test_engine_singleton(self):
        """引擎进程内唯一"""
        assert get_engine() is get_engine()

    def test_run_coroutine(self):
        """协程在持久循环上运行并返回结果"""

        async def coro():
            await asyncio.sleep(0.01)
            return 42

        assert run_ws_coroutine(coro(), timeout=5) == 42

    def test_run_coroutine_from_other_thread(self):
        """可从非 asyncio 线程（如 API 请求线程）调用"""
        result_box: list = []

        def worker():
            async def coro():
                return "from-thread"

            result_box.append(run_ws_coroutine(coro(), timeout=5))

        t = threading.Thread(target=worker)
        t.start()
        t.join(5)
        assert not t.is_alive()
        assert result_box == ["from-thread"]


class TestStartMarketFeed:
    """行情供给启动守卫测试"""

    def test_skipped_in_pytest(self):
        """pytest 环境不启动供给（避免真实外网连接），且不污染幂等标记"""
        assert start_market_feed() is False
        assert start_market_feed() is False

    def test_default_feed_matches_default_header_symbols(self):
        """**出厂默认**四档 == 供给启动时的订阅清单。

        回归场景（2026-11-05）：顶栏画了第四档 BNB 后前端渲染出「BNB —」，因为
        `DEFAULT_FEED_SYMBOLS` 还只有三档；而 ticker 端点**只读 WS 内存存储、不做
        REST 回退**，未订阅的品种永远拿不到数（503，前端显示成「—」）。

        现在顶栏画哪几档是**用户偏好**（`User.header_symbols`），所以这条契约收窄成
        「没改过偏好的用户看着的那四档必须在启动时就订阅」。用户改成别的品种时补订阅
        由 `ensure_feed_symbols` 负责（见下面的 `TestEnsureFeedSymbols`）。
        """
        from apps.authentication.preferences import DEFAULT_HEADER_SYMBOLS

        assert DEFAULT_HEADER_SYMBOLS == DEFAULT_FEED_SYMBOLS


class TestTickerViewWSOnly:
    """ticker 端点纯 WS 存储读取，无 REST 回退"""

    def _call_ticker(self, symbol: str):
        class _TickerView(MarketDataViewSet):
            # 测试中绕过全局 JWT 认证/权限/限流，聚焦存储读取路径
            permission_classes = [AllowAny]
            authentication_classes = []
            throttle_classes = []

        factory = RequestFactory()
        request = factory.get(
            f"/api/datasource/api/market/ticker/?source=binance&symbol={symbol}"
        )
        return _TickerView.as_view({"get": "ticker"})(request)

    def test_returns_ws_store_data_with_raw_symbol(self):
        """WS 按 Binance 原始符号（BTCUSDT）写存储，请求用斜杠形式（BTC/USDT）"""
        store = get_data_store()
        store.clear_all()
        try:
            store.store(
                "ticker",
                "BTCUSDT",
                {
                    "symbol": "BTCUSDT",
                    "last_price": 79612.5,
                    "change_pct_24h": 1.25,
                    "source": "binance",
                },
                "binance",
            )
            response = self._call_ticker("BTC/USDT")
            assert response.status_code == 200
            assert response.data["last_price"] == 79612.5
            assert response.data["change_pct_24h"] == 1.25
        finally:
            store.clear_all()

    def test_returns_store_data_with_slashed_symbol(self):
        """请求符号与存储符号一致（斜杠形式）时直接命中"""
        store = get_data_store()
        store.clear_all()
        try:
            store.store(
                "ticker",
                "ETH/USDT",
                {"symbol": "ETH/USDT", "last_price": 3200.0, "source": "binance"},
                "binance",
            )
            response = self._call_ticker("ETH/USDT")
            assert response.status_code == 200
            assert response.data["last_price"] == 3200.0
        finally:
            store.clear_all()

    def test_503_when_ws_not_ready(self):
        """WS 流未就绪时返回 503（不再回退 REST fetch）"""
        store = get_data_store()
        store.clear_all()
        try:
            response = self._call_ticker("SOL/USDT")
            assert response.status_code == 503
            assert "WebSocket ticker stream not ready" in response.data["error"]
        finally:
            store.clear_all()


class TestEnsureFeedSymbols:
    """`ensure_feed_symbols()`：用户改了顶栏品种之后，把它们补进供给（幂等、尽力而为）。

    这条链路的存在理由只有一个：ticker 端点只读 WS 内存存储、不做 REST 回退。用户把
    DOGE 配进顶栏若没人补订阅，那一档就是永久的「—」，而且**不会报错**——页面上看不出
    「没订阅」和「行情挂了」的区别。

    这里钉三件事：

    1. **幂等**：同一批品种重复调用，登记表只加一次；
    2. **供给没起来时不碰网络**（pytest 环境 `_feed_started` 为假）：登记仍然完成，
       因为它同时是看门狗判数据龄的依据；
    3. **登记进的是 supervisor 看门狗读的那份清单**——这一步不碰网络、任何情况下都
       能做成，少了它新品种断流也没人管。
    """

    def _reset_feed_state(self):
        from apps.datasource import ws_runner

        ws_runner._feed_state["source"] = None
        ws_runner._feed_state["data_type"] = None
        ws_runner._feed_state["market_type"] = None
        ws_runner._feed_state["symbols"] = []

    def test_records_new_symbols_in_feed_state(self):
        from apps.datasource import ws_runner

        self._reset_feed_state()
        try:
            added = ws_runner.ensure_feed_symbols(["DOGE/USDT", "BTC/USDT"])
            assert added == ["DOGE/USDT", "BTC/USDT"]
            assert ws_runner._feed_state["symbols"] == ["DOGE/USDT", "BTC/USDT"]
        finally:
            self._reset_feed_state()

    def test_is_idempotent(self):
        from apps.datasource import ws_runner

        self._reset_feed_state()
        try:
            ws_runner.ensure_feed_symbols(["DOGE/USDT"])
            assert ws_runner.ensure_feed_symbols(["DOGE/USDT"]) == []
            assert ws_runner._feed_state["symbols"] == ["DOGE/USDT"]

            # 新一批里「已登记 + 新」混合：只报新的
            assert ws_runner.ensure_feed_symbols(["DOGE/USDT", "XRP/USDT"]) == [
                "XRP/USDT"
            ]
            assert ws_runner._feed_state["symbols"] == ["DOGE/USDT", "XRP/USDT"]
        finally:
            self._reset_feed_state()

    def test_empty_input_is_noop(self):
        from apps.datasource import ws_runner

        self._reset_feed_state()
        try:
            assert ws_runner.ensure_feed_symbols([]) == []
            assert ws_runner._feed_state["symbols"] == []
        finally:
            self._reset_feed_state()

    def test_feed_not_started_does_not_touch_the_data_source(self, monkeypatch):
        """供给没起来时**不许**在这个调用里拉起真实连接（那是 supervisor 的活）。

        断言方式不能是「`DataSourceRegistry` 里没加载过 binance」——同一次跑里别的用例
        可能已经加载过它（顺序相关，会变成假红）。这里直接把取实例那条路堵死：
        供给没起来就根本不该走到它。
        """
        from apps.datasource import ws_runner
        from apps.datasource.registry import DataSourceRegistry

        self._reset_feed_state()

        def _forbidden(*args, **kwargs):
            raise AssertionError(
                "供给没起来时不该去取数据源实例（那会在请求线程里拉起真实连接）"
            )

        monkeypatch.setattr(DataSourceRegistry, "get", staticmethod(_forbidden))
        try:
            assert ws_runner._feed_started is False
            # 登记仍然完成（看门狗判数据龄要用），但不碰数据源
            assert ws_runner.ensure_feed_symbols(["DOGE/USDT"]) == ["DOGE/USDT"]
            assert ws_runner._feed_state["symbols"] == ["DOGE/USDT"]
        finally:
            self._reset_feed_state()


class TestPreferencesFeedWiring:
    """「用户偏好 → 补订阅」这条线的接线：默认值单一来源。

    `apps/authentication/preferences.py` 的 `DEFAULT_HEADER_SYMBOLS` 是**那份清单的定义处**，
    `ws_runner.DEFAULT_FEED_SYMBOLS` 是**启动时订阅的那份**。它们不同 = 没改过偏好的用户
    看到四档而订阅只有三档 → 一档永久「—」。这条在 preferences 自己的测试里也钉了一遍
    （两边都钉是有意的：谁被改坏都会红）。
    """

    def test_defaults_are_the_same_list_of_symbols(self):
        from apps.authentication.preferences import DEFAULT_HEADER_SYMBOLS

        assert DEFAULT_HEADER_SYMBOLS == DEFAULT_FEED_SYMBOLS
