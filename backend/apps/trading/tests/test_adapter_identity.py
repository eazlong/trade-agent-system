"""适配器必须按**账户**解析，不能按交易所名（2026-09-22）。

事故形状：``OrderExecutor._adapters`` 以**交易所名**为键，同一交易所有两个活跃账户
时后加载的顶掉先加载的，而 ``_load_adapters`` 的日志只有一行 "Adapter loaded:
binance"，看不出谁顶了谁。于是下单、成交同步、持仓视图都可能拿着**另一个账户**的
密钥去做——订单落到错账户上是真实的资金错误，而日志里看不出来。

本文件钉的是新口径：

1. ``_account_adapters`` 以 ``ExchangeAccount.id`` 为键，每个活跃账户各有自己的
   适配器（包含各自的密钥与 testnet 属性）。
2. 同交易所有多个活跃账户 → 记进 ``_ambiguous_exchanges`` 并打 ERROR（带上全部账户
   id），而不是静默让后者顶掉前者；``_adapters`` 里留的那条是「最后加载的」，属于
   任意选择，只给拿不到账户 id 的调用方。
3. 按账户 id 解析**绝不**回退到同交易所另一个账户的适配器：拿不到就让调用方失败，
   并留下 WARNING。只有「账户清单为空」（真实运行里等价于一个适配器都没加载，只会
   出现在手工构造 ``_adapters`` 的测试夹具里）才允许按交易所名回退。
4. 成交同步按**订单自己的账户**取适配器：原先按交易所名分组，两个账户的订单都被
   交给同一个适配器去查，查不到就记 cancelled——把一张活着的单写成已撤销。
"""

from __future__ import annotations

import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from apps.trading.adapters.base import OrderNotFoundError
from apps.trading.executor import OrderExecutor


class _FakeAdapter:
    """只记下解密出来的密钥与自己被 connect/disconnect 的次数。"""

    def __init__(self, api_key, api_secret, testnet):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.connected = 0
        self.disconnected = 0

    async def connect(self):
        self.connected += 1

    async def disconnect(self):
        self.disconnected += 1


def _account(account_id, *, exchange="binance", label="", testnet=False,
             key=b"key", secret=b"secret"):
    return SimpleNamespace(
        id=account_id,
        exchange=exchange,
        label=label,
        testnet=testnet,
        api_key_enc=key,
        api_secret_enc=secret,
    )


def _fake_fernet():
    """解密＝原样返回，于是适配器里的密钥就是夹具写的那串，能逐个断言。"""
    fernet = MagicMock()
    fernet.decrypt.side_effect = lambda raw: bytes(raw)
    return fernet


def _fake_order(**overrides):
    base = {
        "id": "order-uuid-1",
        "side": "buy",
        "symbol": "DOGE/USDT",
        "exchange_order_id": "111",
        "status": "submitted",
        "filled_quantity": Decimal("0"),
        "avg_fill_price": None,
        "realized_pnl": None,
        "error_message": "",
        "exchange_account": SimpleNamespace(exchange="binance"),
        "exchange_account_id": "acc-1",
        "live_session_id": None,
        "quantity": Decimal("100"),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class _LoadAdaptersTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.executor = OrderExecutor()
        self.addCleanup(setattr, OrderExecutor, "_instance", None)

    async def _load(self, *accounts):
        manager = MagicMock()
        manager.filter.return_value = list(accounts)
        with patch("apps.exchange.models.ExchangeAccount.objects", manager), patch.dict(
            "apps.trading.executor.ADAPTER_MAP", {"binance": _FakeAdapter}
        ), patch.object(
            OrderExecutor, "_get_fernet", MagicMock(return_value=_fake_fernet())
        ):
            await self.executor._load_adapters()


class TestAccountKeyedAdapters(_LoadAdaptersTest):
    async def test_each_active_account_gets_its_own_adapter(self):
        """两个同交易所账户 → 两个适配器，密钥各归各的（钱就卡在这条边界上）。"""
        await self._load(
            _account("acc-1", label="main", key=b"key-1", secret=b"secret-1"),
            _account("acc-2", label="sub", key=b"key-2", secret=b"secret-2",
                     testnet=True),
        )

        self.assertEqual(set(self.executor._account_adapters), {"acc-1", "acc-2"})
        first = self.executor._account_adapters["acc-1"]
        second = self.executor._account_adapters["acc-2"]
        self.assertIsNot(first, second)
        self.assertEqual((first.api_key, first.api_secret), ("key-1", "secret-1"))
        self.assertEqual((second.api_key, second.api_secret), ("key-2", "secret-2"))
        self.assertFalse(first.testnet)
        self.assertTrue(second.testnet)
        self.assertEqual((first.connected, second.connected), (1, 1))

    async def test_ambiguous_exchange_is_recorded_and_logged(self):
        """同交易所有两个账户 → 记进 `_ambiguous_exchanges` + ERROR 带全部账户 id。

        只写一句「有歧义」不够：出问题时得能从日志里看出是谁顶了谁。
        """
        with self.assertLogs("apps.trading.executor", level="ERROR") as logs:
            await self._load(_account("acc-1"), _account("acc-2"))

        self.assertEqual(self.executor._ambiguous_exchanges, {"binance"})
        self.assertTrue(self.executor.is_ambiguous_exchange("BINANCE"))
        joined = "\n".join(logs.output)
        self.assertIn("2 个活跃账户", joined)
        for account_id in ("acc-1", "acc-2"):
            self.assertIn(account_id, joined)

    async def test_name_map_keeps_the_last_loaded_account(self):
        """`_adapters` 里那条是「最后加载的」——任意选择，所以它只是遗留口径。"""
        await self._load(_account("acc-1"), _account("acc-2"))

        self.assertIs(
            self.executor._adapters["binance"],
            self.executor._account_adapters["acc-2"],
        )

    async def test_single_account_exchange_is_not_ambiguous(self):
        """单账户交易所照旧：按名字解析可信，两个地图指向同一个实例。"""
        await self._load(_account("acc-1"))

        self.assertEqual(self.executor._ambiguous_exchanges, set())
        self.assertFalse(self.executor.is_ambiguous_exchange("binance"))
        self.assertIs(
            self.executor._adapters["binance"],
            self.executor._account_adapters["acc-1"],
        )


class TestResolveAdapter(unittest.TestCase):
    def setUp(self):
        self.executor = OrderExecutor()
        self.addCleanup(setattr, OrderExecutor, "_instance", None)

    def test_account_id_wins_over_exchange_name(self):
        """按名字只会拿到「别人」的那个；按账户 id 才拿到自己的。"""
        mine, theirs = MagicMock(), MagicMock()
        self.executor._account_adapters = {"acc-1": mine, "acc-2": theirs}
        self.executor._adapters = {"binance": theirs}

        self.assertIs(self.executor._resolve_adapter("binance", "acc-1"), mine)
        self.assertIs(self.executor._resolve_adapter("binance", "acc-2"), theirs)

    def test_unknown_account_never_falls_back_to_another_account(self):
        """账户清单非空却没有这个账户 → None + WARNING。回退等于用别人的密钥下单。"""
        theirs = MagicMock()
        self.executor._account_adapters = {"acc-2": theirs}
        self.executor._adapters = {"binance": theirs}

        with self.assertLogs("apps.trading.executor", level="WARNING") as logs:
            self.assertIsNone(
                self.executor._resolve_adapter("binance", "acc-missing")
            )
        self.assertIn("acc-missing", "\n".join(logs.output))

    def test_legacy_by_name_fallback_when_account_map_is_empty(self):
        """账户地图为空才回退（手工构造 `_adapters` 的测试夹具走这条）。"""
        adapter = MagicMock()
        self.executor._adapters = {"binance": adapter}

        self.assertIs(self.executor._resolve_adapter("binance", "acc-1"), adapter)
        self.assertIsNone(self.executor._resolve_adapter("okx", "acc-1"))

    def test_caller_without_account_id_still_resolves_by_name(self):
        """cancel_order/get_positions/get_balance 拿不到账户 id，走遗留口径不变。"""
        adapter = MagicMock()
        self.executor._account_adapters = {"acc-1": adapter}
        self.executor._adapters = {"binance": adapter}

        self.assertIs(self.executor._resolve_adapter("binance", None), adapter)


class TestShutdownDisconnectsOnce(unittest.IsolatedAsyncioTestCase):
    async def test_shared_adapter_is_disconnected_exactly_once(self):
        """单账户交易所两个地图指向同一实例：断开一次，不是两次。"""
        executor = OrderExecutor()
        adapter = _FakeAdapter("k", "s", False)
        executor._account_adapters = {"acc-1": adapter}
        executor._adapters = {"binance": adapter}
        executor._ambiguous_exchanges = {"binance"}
        executor._running = True
        self.addCleanup(setattr, OrderExecutor, "_instance", None)

        await executor.shutdown()

        self.assertEqual(adapter.disconnected, 1)
        self.assertEqual(executor._adapters, {})
        self.assertEqual(executor._account_adapters, {})
        self.assertEqual(executor._ambiguous_exchanges, set())


class TestFillSyncUsesTheOrdersOwnAccount(unittest.IsolatedAsyncioTestCase):
    """同交易所两个账户的订单各用**自己那个账户**的适配器查成交。

    原先按交易所名分组 → 两个账户的订单都交给同一个（后加载的）适配器去查，
    查不到就记 cancelled：本地账面把一张活着的单写成已撤销。
    """

    async def test_each_order_is_queried_through_its_own_account_adapter(self):
        executor = OrderExecutor()
        executor._running = True
        mine, theirs = MagicMock(), MagicMock()
        mine.fetch_order = AsyncMock(side_effect=OrderNotFoundError("no such order"))
        theirs.fetch_order = AsyncMock(side_effect=OrderNotFoundError("no such order"))
        executor._account_adapters = {"acc-1": mine, "acc-2": theirs}
        executor._adapters = {"binance": theirs}
        self.addCleanup(setattr, OrderExecutor, "_instance", None)

        manager = MagicMock()
        manager.filter.return_value.select_related.return_value = [
            _fake_order(id="o-1", exchange_account_id="acc-1",
                        exchange_order_id="111"),
            _fake_order(id="o-2", exchange_account_id="acc-2",
                        exchange_order_id="222"),
        ]

        with patch("apps.trading.models.Order.objects", manager):
            await executor._sync_active_orders()

        mine.fetch_order.assert_awaited_once_with("111", "DOGE/USDT")
        theirs.fetch_order.assert_awaited_once_with("222", "DOGE/USDT")

    async def test_order_without_a_loaded_adapter_is_not_queried_by_another(self):
        """账户没有已加载的适配器（密钥缺失/已停用）→ 跳过，绝不借用别人的。"""
        executor = OrderExecutor()
        executor._running = True
        theirs = MagicMock()
        theirs.fetch_order = AsyncMock(side_effect=OrderNotFoundError("no such order"))
        executor._account_adapters = {"acc-2": theirs}
        executor._adapters = {"binance": theirs}
        self.addCleanup(setattr, OrderExecutor, "_instance", None)

        manager = MagicMock()
        manager.filter.return_value.select_related.return_value = [
            _fake_order(id="o-1", exchange_account_id="acc-1"),
        ]

        with patch("apps.trading.models.Order.objects", manager):
            await executor._sync_active_orders()

        theirs.fetch_order.assert_not_awaited()
