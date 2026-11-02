"""历史需求按策略分档：**自己声明了就听它的**，不套全局地板。

## 这一份钉的是「地板只兜底、不当配额」

上一笔（454f7a4）把 500 从天花板改成了地板，于是**每个**策略至少预加载 500 根。对需求
750 的策略那是必要的；对只声明 136 的策略那是纯浪费——每根 1h 都是往交易所拉的网络
请求，而 `box_time_range_breakout_strategy` 的 136 与实际需求一致（`lookback + atr + 2`）。

地板仍然要有：策略**什么都没声明**时，引擎得留够指标能用的量，不能退化成 0。

## 因此需求要先能区分「声明了」与「没声明」

`min_bars` 现在**总是存在**（`BaseStrategy.__init__` 会填默认值 500），所以
`getattr(strategy, "min_bars", None)` 永远拿得到数——「策略声明了 136」与「策略没声明、
拿到默认 500」在引擎眼里长得一模一样。本文件的第一组用例就钉这个区分：策略必须能回答
「我这个数是不是我自己算的」。

区分方式取「`min_bars` 是否落在实例 `__dict__` 里」：
* 策略声明 → 它自己在 `__init__` 里 `self.min_bars = ...` ⇒ 在实例 `__dict__` 里；
* 没声明 → 只有基类 `__init__` 落的那一份……**但那也在 `__dict__` 里**。

所以不能只看 `__dict__`。`BaseStrategy` 改为：没声明时**不落** `min_bars` 到实例，
让读取方走 `__getattr__` 拿到默认值；声明了就由策略自己覆盖。这样「在 `__dict__` 里」
就精确等于「策略自己写过」。本文件的用例锁住这个语义。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.strategy_engine.base import BaseStrategy, StrategyContext
from apps.strategy_engine.live_mode import LiveStrategyRunner


def _ctx(**params) -> StrategyContext:
    return StrategyContext(
        symbol="DOGE/USDT",
        timeframe="1h",
        mode="live",
        params=params,
        balance=Decimal("1000"),
        position=Decimal("0"),
    )


_SMALL = 136
_LARGE = 730


class _DeclaresSmall(BaseStrategy):
    """像 box 那样：自己算出一个远小于 500 的需求。"""

    name = "declares_small"
    description = "策略类型：测试\n核心指标：-\n适用场景：-\n入场逻辑：-"

    def __init__(self, context):
        super().__init__(context)
        self.min_bars = _SMALL

    def on_bar(self, kline, history):
        return None


class _DeclaresLarge(BaseStrategy):
    """像 momentum 那样：需求超过 500。"""

    name = "declares_large"
    description = "策略类型：测试\n核心指标：-\n适用场景：-\n入场逻辑：-"

    def __init__(self, context):
        super().__init__(context)
        self.min_bars = _LARGE

    def on_bar(self, kline, history):
        return None


class _DeclaresNothing(BaseStrategy):
    """什么都没声明：应当拿到默认值，并被地板兜住。"""

    name = "declares_nothing"
    description = "策略类型：测试\n核心指标：-\n适用场景：-\n入场逻辑：-"

    def on_bar(self, kline, history):
        return None


class _Legacy(BaseStrategy):
    """老写法：只写类属性 `min_kline_length`。"""

    name = "legacy"
    description = "策略类型：测试\n核心指标：-\n适用场景：-\n入场逻辑：-"
    min_kline_length = 300

    def on_bar(self, kline, history):
        return None


def _runner(cls) -> LiveStrategyRunner:
    strategy = cls(_ctx())
    return LiveStrategyRunner(
        strategy, "DOGE/USDT", "1h", "acct", live_session_id="sess"
    )


class TestDeclaredVersusInherited:
    """引擎必须能分出「策略自己写的」与「基类默认的」。"""

    def test_declaring_strategy_reports_its_own_number(self):
        strategy = _DeclaresSmall(_ctx())
        assert strategy.min_bars == _SMALL
        assert strategy.declares_min_bars is True

    def test_strategy_that_declares_nothing_is_not_a_declaration(self):
        """没声明 → 读得到默认值，但 `declares_min_bars` 必须是 False。"""
        strategy = _DeclaresNothing(_ctx())
        assert strategy.min_bars == BaseStrategy.MIN_BARS_DEFAULT
        assert strategy.declares_min_bars is False

    def test_legacy_class_attribute_counts_as_a_declaration(self):
        """老写法（类属性）也是「策略自己说的」，不能当成没声明。"""
        strategy = _Legacy(_ctx())
        assert strategy.min_bars == 300
        assert strategy.declares_min_bars is True


class TestTheFloorOnlyCatchesTheUndeclared:
    """地板兜住「没声明」；声明了就以声明为准（可以低于地板）。"""

    def test_declared_below_the_floor_is_respected(self):
        """声明 136 的策略不该被抬到 500——那 364 根是纯浪费。

        这是本票的主句。`box_time_range_breakout_strategy` 的 136 正好等于它
        `on_bar` 的实际门槛（`lookback + atr_period + 2`），多拉的根本用不上。
        """
        runner = _runner(_DeclaresSmall)
        assert runner.history_requirement == _SMALL, (
            f"声明 {_SMALL} 的策略被抬到了 {runner.history_requirement}：地板在当配额用"
        )

    def test_declared_above_the_floor_is_respected(self):
        """声明 730 的策略仍在 730（修复 454f7a4 要保的那条）。"""
        runner = _runner(_DeclaresLarge)
        assert runner.history_requirement == _LARGE

    def test_undeclared_falls_back_to_the_floor(self):
        """没声明 → 地板兜底，不退化成 0。"""
        runner = _runner(_DeclaresNothing)
        assert runner.history_requirement >= LiveStrategyRunner.HISTORY_FLOOR

    def test_legacy_declaration_is_also_respected(self):
        """老写法 300 < 500，同样按声明走（不因是别名就另立一套）。"""
        runner = _runner(_Legacy)
        assert runner.history_requirement == 300

    @pytest.mark.parametrize("declared", [1, 10, 136, 499, 500, 501, 730, 5000])
    def test_requirement_never_drops_below_the_declaration(self, declared):
        """无论声明多少，需求量都不得低于声明值。

        这条是不变量的底线：低于声明值就回到「`on_bar` 恒为假、实盘空转」那个缺陷。
        地板可以低于声明（不生效），但**绝不能把声明截短**。
        """
        runner = _runner(_DeclaresSmall)
        runner._history_requirement = declared
        assert runner.history_requirement >= declared


class TestObjectsThatAreNotBaseStrategy:
    """不是 `BaseStrategy` 子类的对象（鸭子类型策略 / 测试替身）。

    这条分支在生产里走不到（`LiveStrategyRunner` 的入参永远是 `BaseStrategy`），但它是
    测试替身与外部脚本的接缝。判错的代价是把一个写明的需求静默砍到地板——那正是本票
    要修掉的缺陷原形，只是换了一个受害者。
    """

    def test_a_duck_typed_strategy_with_a_large_declaration_is_not_truncated(self):
        """没有 `declares_min_bars` 但写了 `min_bars = 730` → 不能砍到 500。"""

        class _Duck:
            name = "duck"
            min_kline_length = 500
            min_bars = 730

            def on_start(self):
                pass

            def on_stop(self):
                pass

        runner = LiveStrategyRunner(
            _Duck(), "DOGE/USDT", "1h", "acct", live_session_id="sess"
        )
        assert runner.history_requirement >= 730, (
            f"鸭子类型策略声明了 730 却被砍到 {runner.history_requirement}"
        )

    def test_a_duck_typed_strategy_with_nothing_declared_still_gets_the_floor(self):
        class _EmptyDuck:
            name = "empty_duck"

            def on_start(self):
                pass

            def on_stop(self):
                pass

        runner = LiveStrategyRunner(
            _EmptyDuck(), "DOGE/USDT", "1h", "acct", live_session_id="sess"
        )
        assert runner.history_requirement >= LiveStrategyRunner.HISTORY_FLOOR


class TestLimitStillHasHeadroom:
    """上限仍然是「需求的倍数」，且永远 ≥ 需求。"""

    def test_limit_is_above_requirement_for_a_declared_small_strategy(self):
        runner = _runner(_DeclaresSmall)
        assert runner.history_limit >= runner.history_requirement
        assert runner.history_limit == _SMALL * LiveStrategyRunner.HISTORY_HEADROOM

    def test_a_tiny_declaration_still_gets_a_workable_history(self):
        """声明 1 根也不能让历史只有 1 根：指标需要若干根。

        `on_kline` 拿到历史才决策，而策略的指标实现会回看一个窗口。声明 1 根这种
        （写错的）声明如果被照单全收，`on_bar` 会在长窗口计算里抛 IndexError。
        """
        runner = _runner(_DeclaresSmall)
        runner._history_requirement = 1
        assert runner.history_limit >= 2  # 至少 headroom 生效
        # 且不会因此把「需求」也算成 1 以下
        assert runner.history_requirement == 1
