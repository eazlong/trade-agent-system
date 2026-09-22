"""RiskGuard 抓取失败日志必须可诊断（2026-09-20 事故回归）。

线上日志只有：
    2026-09-21 02:37:51 ERROR [apps.riskguard.guard] Failed to fetch positions from binance:
后面什么都没有 —— 因为 httpx 超时异常的 message 是空的，而旧实现只打印 {e}，
既看不出异常类型，也看不出到底是 positions 还是 balance 失败（两者在同一个 try 块里）。
排查被迫靠现场探测才定性为 httpx.ConnectTimeout。

契约：
  1. 日志必须包含异常类型名（空消息也能定位）。
  2. positions 与 balance 分开记录，各自指明是哪个操作失败。
  3. 连续失败次数可见（判断是偶发还是持续卡死）。
"""

from __future__ import annotations

import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from apps.riskguard.guard import RiskGuard
from apps.trading.executor import OrderExecutor


def _executor_with(adapter) -> OrderExecutor:
    """真 OrderExecutor，只手工填适配器地图（其余加载流程要连 DB 与交易所）。

    用真对象而不是 MagicMock：`_unique_adapters()` 里「账户地图与交易所名地图怎么
    合并、谁被顶掉」正是「哪个账户会被巡检」的那一环，替身自己实现一份就测不到它。
    """
    executor = OrderExecutor()
    executor._adapters = {"binance": adapter}
    return executor


class TestGuardFailureDiagnostics(unittest.IsolatedAsyncioTestCase):
    async def test_empty_message_timeout_logs_exception_type_and_operation(self):
        guard = RiskGuard("trading")
        adapter = MagicMock()
        adapter.get_positions = AsyncMock(side_effect=httpx.ConnectTimeout(""))
        executor = _executor_with(adapter)

        with patch(
            "apps.trading.executor.OrderExecutor.get_instance", return_value=executor
        ), self.assertLogs("apps.riskguard.guard", level="ERROR") as cm:
            await guard._check_floating_pnl()

        msg = "\n".join(cm.output)
        self.assertIn("positions", msg)
        self.assertIn("ConnectTimeout", msg, "空消息超时必须能从日志看出异常类型")
        self.assertIn("binance", msg)

    async def test_balance_failure_is_reported_as_balance_not_positions(self):
        guard = RiskGuard("trading")
        adapter = MagicMock()
        adapter.get_positions = AsyncMock(return_value=[])
        adapter.get_balance = AsyncMock(side_effect=httpx.ConnectTimeout(""))
        executor = _executor_with(adapter)

        with patch(
            "apps.trading.executor.OrderExecutor.get_instance", return_value=executor
        ), self.assertLogs("apps.riskguard.guard", level="ERROR") as cm:
            await guard._check_floating_pnl()

        msg = "\n".join(cm.output)
        self.assertIn("balance", msg, "balance 失败不能报成 positions")
        self.assertIn("ConnectTimeout", msg)

    async def test_consecutive_failures_are_counted_and_reset_on_success(self):
        guard = RiskGuard("trading")
        adapter = MagicMock()
        adapter.get_positions = AsyncMock(side_effect=httpx.ConnectTimeout(""))
        executor = _executor_with(adapter)

        with patch(
            "apps.trading.executor.OrderExecutor.get_instance", return_value=executor
        ), self.assertLogs("apps.riskguard.guard", level="ERROR") as cm:
            await guard._check_floating_pnl()
            await guard._check_floating_pnl()

        msgs = "\n".join(cm.output)
        self.assertIn("consecutive_failures=1", msgs)
        self.assertIn("consecutive_failures=2", msgs, "持续失败必须能看出在累积")

        adapter.get_positions = AsyncMock(return_value=[])
        adapter.get_balance = AsyncMock(return_value={"USDT": 1})
        # 注意：patch 必须同样覆盖成功那次调用，否则 get_instance() 返回 None 直接早退
        with patch(
            "apps.trading.executor.OrderExecutor.get_instance", return_value=executor
        ):
            await guard._check_floating_pnl()
        self.assertEqual(
            guard._position_fetch_failures, 0, "成功后计数必须归零"
        )


class TestFloatingPnlCoversEveryAccount(unittest.IsolatedAsyncioTestCase):
    """浮亏巡检必须覆盖**每个账户**，而不只是每个交易所名（2026-09-22）。

    旧实现遍历 `executor._adapters`（按交易所名索引）。同一交易所有两个活跃账户时
    那张表里只有最后加载的那个，另一个账户的持仓**永远不被巡检**——浮亏超阈值也没有
    告警，日志上还看不出少了什么。这与成交同步按账户分组修掉的是同一类错误。
    """

    def tearDown(self):
        OrderExecutor._instance = None

    async def test_every_account_is_polled_and_the_label_tells_them_apart(self):
        guard = RiskGuard("trading")
        dead = MagicMock()
        dead.get_positions = AsyncMock(side_effect=httpx.ConnectTimeout(""))
        live = MagicMock()
        live.get_positions = AsyncMock(return_value=[])
        live.get_balance = AsyncMock(return_value={"USDT": Decimal("1")})

        executor = OrderExecutor()
        executor._account_adapters = {"acct-1": dead, "acct-2": live}
        # `_load_adapters` 加载时顺手记下的账户→交易所：被顶掉的那个账户不在
        # `_adapters` 里，交易所名只能从这里来。
        executor._account_exchanges = {"acct-1": "binance", "acct-2": "binance"}
        executor._adapters = {"binance": live}  # 后加载的把 dead 顶掉

        with patch(
            "apps.trading.executor.OrderExecutor.get_instance", return_value=executor
        ), self.assertLogs("apps.riskguard.guard", level="ERROR") as cm:
            await guard._check_floating_pnl()

        msg = "\n".join(cm.output)
        self.assertIn(
            "binance#acct-1",
            msg,
            "标签必须能区分同交易所的两个账户：只写 binance 等于没告诉是哪个账户",
        )
        live.get_positions.assert_awaited_once()
        self.assertEqual(
            [label for label, _ in executor._unique_adapters()],
            ["binance#acct-1", "binance#acct-2"],
            "两个账户都必须被巡检，且谁是谁一眼可读",
        )
