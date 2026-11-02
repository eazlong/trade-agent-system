"""实盘 runner 的历史深度必须跟着**策略实际声明的需求量**走。

## 这一份钉的是什么缺陷（txy 实测，2026-11-02）

`61d2662b`（AVAX/USDT, `momentum_mean_reversion_dynamic_sl`）`running` 6.14 天、
`orders = 0`、`risk_events = 0` —— 一根信号都没出过，也**没有被风控拒过**（这排除了
「下单被拦」那一类解释）。

真因是两套名字在说同一件事，而引擎只读了其中一个：

| 谁 | 写了什么 | 引擎读了吗 |
|---|---|---|
| `BaseStrategy.min_kline_length` | 类属性，默认 **500** | ✅ `live_mode.start()` 用它预加载 |
| 策略 `__init__` 里的 `self.min_bars` | **实例**属性，随参数算出来（如 `max(ema_trend_period,…) + 10`） | ❌ 一个都不读 |

该策略 config 是 `ema_trend_period=720` ⇒ `min_bars = 730`，而 runner 只预加载 500 根、
`on_kline` 又把 `_kline_history` 硬截在 500 根 ⇒ `len(history) < 730` **恒真** ⇒
`on_bar` 第一行 `return None`。跑多久都不会出信号。

回测侧看起来「正常」：`BacktestEngine` 吃整段 OHLCV（那条回测是 2021-01~09 的 1h，
约 6400 根），远超 730。所以这类缺陷的形状是**回测通过、实盘永久空转，而两边日志都干净**。

## 为什么在「历史深度」这个接缝上测，而不是在某个私有方法上

不变量本身是可观察的行为：**决策时策略拿到的历史，必须够它自己声明的门槛**。
所以这里只断言两件事：

1. `runner.start()` 向数据源要的根数 ≥ 策略的需求量；
2. 历史积累到超过旧上限（500）之后，`_kline_history` **不被截断到需求线以下**。

两者都不碰 `load_initial_history` / `max_history` 的名字，实现换写法（合并成一个方法、
改成配置项）时这些用例不该红。

## 刻意的边界：需求线取「策略自己报的数」，不在这里重算

`min_bars` 是策略的私有算法（每个策略不同：`max(a×3, b×3, 100)` / `lookback+atr+2` / …）。
本文件**不复制那些公式**，只断言「引擎给的历史 ≥ 策略说它要的」。复制公式会让用例在
策略改公式时假红，而它想证明的其实只是「引擎听策略的」。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from unittest.mock import AsyncMock

from django.test import SimpleTestCase

from apps.strategy_engine.live_mode import LiveStrategyRunner

#: 旧上限。**故意在用例里写死这个数**：它是要被证明「不再是天花板」的那个值，
#: 从别处读一遍就等于用被测对象验证被测对象。
OLD_CAP = 500

#: 让需求线明显高于旧上限，模拟 ema_trend_period=720 那类长周期策略。
DEMAND = 730


@dataclass
class _Kline(dict):
    """够用的 K 线条目（runner 只按下标/键读）。"""

    def __init__(self, ts: int):
        super().__init__(
            open="1",
            high="1",
            low="1",
            close="1",
            volume="1",
            timestamp=ts,
            is_closed=True,
        )


def _history(n: int) -> list[dict]:
    return [dict(_Kline(ts=1_700_000_000_000 + i * 3_600_000)) for i in range(n)]


class _Strategy:
    """最小策略替身：只暴露 runner 会读的东西。

    `min_bars` 是**实例**属性（真实策略都在 `__init__` 里算），`min_kline_length` 保持
    基类默认——这正是 txy 上那 9 个策略的形状：它们只写了前者。
    """

    name = "demand_probe"
    min_kline_length = OLD_CAP

    def __init__(self):
        self.min_bars = DEMAND
        self.on_start = lambda: None
        self.on_stop = lambda: None
        self.ctx = SimpleNamespaceLike()

    def select_universe(self):
        return ["DOGE/USDT"]

    def generate_insights(self, kline, history):
        return []

    def construct_portfolio(self, insights, ctx):
        return []

    def apply_risk_filters(self, targets, ctx):
        return targets

    def get_watch_signals(self):
        return []


class SimpleNamespaceLike:
    """runner 会读 ctx.price / ctx.position 等；这里只要不炸。"""

    symbol = "DOGE/USDT"
    position = Decimal("0")
    balance = Decimal("10000")

    def set_price(self, *args, **kwargs):
        pass


def _runner(strategy=None):
    """构造 runner，并把它所有会碰网络的启动步骤换成替身。

    只替换**外部**（REST 拉 K 线、订阅、信号注册、持仓同步），`_kline_history` 与
    上限那一段走真实实现——那正是被测对象。
    """
    strategy = strategy or _Strategy()
    runner = LiveStrategyRunner(
        strategy,
        "DOGE/USDT",
        "1h",
        "acct-1",
        user_id="user-1",
        live_session_id="sess-1",
    )
    runner._subscribe_kline = AsyncMock()
    runner._register_signal_monitors = AsyncMock()
    runner._sync_position_from_exchange = AsyncMock()
    runner._dispatcher = None
    return runner


def _start_with_requested_limit(runner) -> int:
    """跑一次 `start()`，返回它向数据源要的历史根数（None 归一成 -1）。"""
    requested: dict[str, int] = {}

    async def _fake_load(limit: int = 0):
        requested["limit"] = limit
        # 数据源按请求给了多少就多少（真实来源通常给到位）
        runner._kline_history = _history(limit)

    runner.load_initial_history = _fake_load
    asyncio.run(runner.start())
    return requested.get("limit", -1)


class TestStartAsksForEnoughHistory(SimpleTestCase):
    """启动预加载的根数必须够策略自己声明的门槛。"""

    def test_start_requests_at_least_what_the_strategy_declares(self):
        """策略要 730 根，runner 不能只按 `min_kline_length`(500) 去要。

        这条是缺陷的正面表述：修复前 `start()` 传的是 `strategy.min_kline_length`，
        于是发出 500 —— 与策略自己算出的 730 差 230 根，且这 230 根**永远补不上**
        （下面那条用例证它补不上）。
        """
        runner = _runner()
        requested = _start_with_requested_limit(runner)

        self.assertGreaterEqual(
            requested,
            DEMAND,
            f"runner 只向数据源要了 {requested} 根，而策略声明需要 {DEMAND} 根；"
            "差的那部分在实盘里永远补不上（见历史截断那条用例）",
        )

    def test_a_strategy_that_declares_nothing_still_gets_the_base_default(self):
        """没声明 `min_bars` 的策略照旧拿 `min_kline_length`——不因修复而缩水。

        反向边界：把「取 max」写成「只看 min_bars」时，这个用例会红。
        """

        class _Plain(_Strategy):
            def __init__(self):
                super().__init__()
                self.min_bars = None  # 没算过

        runner = _runner(_Plain())
        requested = _start_with_requested_limit(runner)
        self.assertGreaterEqual(requested, OLD_CAP)


class TestHistoryIsNotTruncatedBelowDemand(SimpleTestCase):
    """历史积累过旧上限之后，不能被截到需求线以下。"""

    def _feed(self, runner, count: int) -> None:
        """把 `count` 根**已收盘、时间戳递增**的 K 线喂进 on_kline。

        直接调 `on_kline`（公开入口）而不是往 `_kline_history` 里塞：截断逻辑就在
        那里面，绕过它来断言语义就变了。
        """
        runner._running = True

        async def _noop_process(kline):
            return None

        runner._process_kline = _noop_process
        for i in range(count):
            asyncio.run(
                runner.on_kline(dict(_Kline(ts=1_700_000_000_000 + i * 3_600_000)))
            )

    def test_history_grows_past_the_old_cap(self):
        """喂 `DEMAND + 50` 根之后，历史必须到得了 `DEMAND`。

        修复前 `max_history = 500` 硬编码 ⇒ 到 500 就停 ⇒ 永远够不着 730。
        这条用例就是那个「恒真」的字面复现：它在修复前必然红。
        """
        runner = _runner()
        runner._kline_history = []
        self._feed(runner, DEMAND + 50)

        self.assertGreaterEqual(
            len(runner._kline_history),
            DEMAND,
            f"喂了 {DEMAND + 50} 根，历史却只有 {len(runner._kline_history)} 根"
            f"（旧上限 {OLD_CAP}）；策略要求 {DEMAND} 根才决策，于是永不决策",
        )

    def test_history_still_has_a_cap(self):
        """上限必须仍然存在——不能为了满足需求就无界增长。

        反向边界：修复方式是「按需求取 max」，不是「删掉上限」。删掉之后长时间运行的
        会话会把内存吃光，而那种故障在短测试里看不出来。
        """
        runner = _runner()
        runner._kline_history = []
        self._feed(runner, DEMAND * 3)

        length = len(runner._kline_history)
        self.assertLess(
            length,
            DEMAND * 3,
            "历史无界增长了：上限被删掉，长时间运行的会话会吃光内存",
        )
        self.assertGreaterEqual(length, DEMAND, "上限不该低于策略需求（否则等于没修）")


class TestTheCapFollowsParameters(SimpleTestCase):
    """上限是**跟参数走**的：同一策略换 config，需求线随之变。"""

    def test_a_bigger_parameter_raises_the_cap(self):
        """`min_bars` 变大时，能留住的历史也要变大。

        这条防的是「把 730 写死成新常量」那种修法：它让这条会话活了，而下一个把
        `ema_trend_period` 调到 1500 的策略照样空转，且症状一模一样。

        **喂的根数必须越过小的那个上限**，否则两边都还没截断、长度相等，断言会把
        「没截断」误读成「上限是常量」。上限是 `需求 × HISTORY_HEADROOM`。
        """
        small = _runner()
        small.strategy.min_bars = 600
        small._history_requirement = small.strategy.min_bars
        small._kline_history = []

        big = _runner()
        big.strategy.min_bars = 1200
        big._history_requirement = big.strategy.min_bars
        big._kline_history = []

        # 越过小的那个上限（600×2=1200），但仍低于大的（1200×2=2400）
        feed = small.history_limit + 100

        for runner in (small, big):
            runner._running = True

            async def _noop(kline):
                return None

            runner._process_kline = _noop
            for i in range(feed):
                asyncio.run(
                    runner.on_kline(dict(_Kline(ts=1_700_000_000_000 + i * 3_600_000)))
                )

        self.assertEqual(
            len(small._kline_history),
            small.history_limit,
            "小的那个应当已经截断到它自己的上限",
        )
        self.assertEqual(
            len(big._kline_history),
            feed,
            "大的那个还没到上限，不该被截断",
        )
        self.assertGreater(
            big.history_limit,
            small.history_limit,
            "需求更大的策略上限没有更大：上限是一个常量，不跟参数走",
        )
        # 真正要保的那条：两个上限都 ≥ 各自的需求
        self.assertGreaterEqual(len(small._kline_history), 600)
        self.assertGreaterEqual(len(big._kline_history), 1200)
