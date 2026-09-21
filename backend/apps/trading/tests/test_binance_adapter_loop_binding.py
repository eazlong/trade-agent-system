"""F3：HTTP 客户端绑定在别的 event loop 上时必须重建。

2026-09-21 启动窗口实测：适配器在启动阶段的 loop 里 connect()，随后被 runner/consumer
任务在另一个 loop 调用 → `RuntimeError: <asyncio.locks.Event ...> is bound to a
different event loop`，持仓同步/下单/RiskGuard 拉持仓全中招，靠 _reconnect 自愈（20-40s）。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase

from apps.trading.adapters.binance import BinanceAdapter


class _FakeClient:
    def __init__(self, name: str):
        self.name = name
        self.closed = False

    async def aclose(self):
        self.closed = True


class TestLoopAwareClient(SimpleTestCase):
    def test_rebuilds_client_when_loop_differs(self):
        """客户端记录的 loop 与当前 loop 不同 → 关掉旧的、换成新 loop 的客户端。"""
        a = BinanceAdapter("key", "secret", testnet=True)
        old = _FakeClient("old")
        a._client = old  # type: ignore[assignment]
        foreign_loop = asyncio.new_event_loop()
        a._client_loop = foreign_loop
        new = _FakeClient("new")

        async def fake_connect():
            a._client = new  # type: ignore[assignment]

        try:
            with patch.object(
                BinanceAdapter, "connect", AsyncMock(side_effect=fake_connect)
            ):
                asyncio.run(a._ensure_client_for_current_loop())
        finally:
            foreign_loop.close()

        self.assertTrue(old.closed, "旧客户端必须被关闭")
        self.assertIs(a._client, new)

    def test_same_loop_keeps_client(self):
        """同一 loop 内不得重建（避免每次请求都换客户端）。"""
        a = BinanceAdapter("key", "secret", testnet=True)
        old = _FakeClient("old")
        a._client = old  # type: ignore[assignment]

        async def run():
            a._client_loop = asyncio.get_running_loop()
            await a._ensure_client_for_current_loop()

        asyncio.run(run())

        self.assertFalse(old.closed)
        self.assertIs(a._client, old)

    def test_injected_client_without_loop_is_untouched(self):
        """测试注入的替身（未记录创建 loop）不碰。"""
        a = BinanceAdapter("key", "secret", testnet=True)
        injected = _FakeClient("injected")
        a._client = injected  # type: ignore[assignment]  # _client_loop 保持 None

        asyncio.run(a._ensure_client_for_current_loop())

        self.assertFalse(injected.closed)
        self.assertIs(a._client, injected)

    def test_disconnect_clears_loop_record(self):
        a = BinanceAdapter("key", "secret", testnet=True)
        a._client = _FakeClient("c")  # type: ignore[assignment]
        a._client_loop = asyncio.new_event_loop()

        asyncio.run(a.disconnect())

        self.assertIsNone(a._client)
        self.assertIsNone(a._client_loop)
