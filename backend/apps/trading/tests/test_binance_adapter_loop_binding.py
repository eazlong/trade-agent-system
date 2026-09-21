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
        """记录的 loop 与当前 loop 不同 → 建新客户端、换引用，旧的最终被关闭。"""
        a = BinanceAdapter("key", "secret", testnet=True)
        old = _FakeClient("old")
        a._client = old  # type: ignore[assignment]
        foreign_loop = asyncio.new_event_loop()
        a._client_loop = foreign_loop
        new = _FakeClient("new")

        async def scenario():
            a.CLIENT_CLOSE_GRACE_SECONDS = 0

            async def fake_create():
                a._client = new  # type: ignore[assignment]

            with patch.object(
                BinanceAdapter, "_create_client", AsyncMock(side_effect=fake_create)
            ):
                await a._ensure_client_for_current_loop()
            await asyncio.gather(*a._closing_tasks, return_exceptions=True)
            return a._client

        try:
            resolved = asyncio.run(scenario())
        finally:
            foreign_loop.close()

        self.assertTrue(old.closed, "旧客户端最终必须被关闭（否则连接池一直烂着）")
        self.assertIs(resolved, new)

    def test_old_client_is_not_closed_inline(self):
        """重建当刻不得内联关闭旧客户端——并发在途请求还在用它。

        线上实证（2026-09-21）：内联 aclose() 导致 SOL 持仓同步报
        `ClosedResourceError:`（空消息）被 fail-loud 拦了一轮。
        """
        a = BinanceAdapter("key", "secret", testnet=True)
        old = _FakeClient("old")
        a._client = old  # type: ignore[assignment]
        foreign_loop = asyncio.new_event_loop()
        a._client_loop = foreign_loop
        a.CLIENT_CLOSE_GRACE_SECONDS = 30

        async def scenario():
            async def fake_create():
                a._client = _FakeClient("new")  # type: ignore[assignment]

            with patch.object(
                BinanceAdapter, "_create_client", AsyncMock(side_effect=fake_create)
            ):
                await a._ensure_client_for_current_loop()

            assert not old.closed, "旧客户端不得被内联关闭"
            assert a._closing_tasks, "延迟关闭任务必须被强引用持有（否则会被 GC）"
            for task in list(a._closing_tasks):
                task.cancel()
            await asyncio.gather(*a._closing_tasks, return_exceptions=True)

        try:
            asyncio.run(scenario())
        finally:
            foreign_loop.close()

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
