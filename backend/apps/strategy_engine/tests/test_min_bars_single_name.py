"""「策略需要多少历史」只能有**一个名字**：`min_bars`。

## 这一份钉的是什么（txy 实测，2026-11-02）

同一条不变量此前有两个名字，而引擎只读其中一个，于是一整类策略在实盘里永久空转：

| 名字 | 形态 | 谁在用 |
|---|---|---|
| `min_kline_length` | **类**属性，默认 500 | 引擎（预加载）、`backtest_smma_fractal.py` |
| `min_bars` | **实例**属性，`__init__` 里按参数算 | 9 个策略自己 |

`momentum_mean_reversion_dynamic_sl` 的 `min_bars = max(ema_trend_period=720, …) + 10
= 730`，而引擎按 500 预加载、`on_kline` 又截在 500 ⇒ `on_bar` 第一行恒为假 ⇒ 6.14 天
0 单。上一个修复（`test_history_depth.py`）让引擎**同时读两个**，那解决了症状，但两个
名字仍然并存——下一个写策略的人还会二选一，而选错的那一半静默失效。

**所以这一份收的是「名字」，不是「数值」。** 目标：`min_bars` 是唯一可写的名字；
`min_kline_length` 退化成只读别名（老策略文件不改也能跑），且被写时给出弃用警告。

## 别名的方向为什么是这个

反过来（让 `min_bars` 变成 `min_kline_length` 的别名）做不到：`min_bars` 是**实例**
属性（9 个策略在 `__init__` 里算），类属性没法代理一个实例值。而 `min_kline_length` 是
类属性，把它做成 `property` 去读 `self.min_bars` 方向是通的。

顺带一个收益：`backtest_smma_fractal.py` 读的是 `getattr(cls, "min_kline_length", 0)`
（**类**上取），所以本文件必须保证它**在类上**也读得到（取默认值），否则那个脚本的预热
根数会静默变成 0。
"""

from __future__ import annotations

import warnings

import pytest

from apps.strategy_engine.base import BaseStrategy, StrategyContext


def _ctx(**params) -> StrategyContext:
    from decimal import Decimal

    return StrategyContext(
        symbol="DOGE/USDT",
        timeframe="1h",
        mode="live",
        params=params,
        balance=Decimal("1000"),
        position=Decimal("0"),
    )


def _make(cls):
    return cls(_ctx())


class _DeclaredViaMinBars(BaseStrategy):
    """新写法：只在 `__init__` 里设 `min_bars`（9 个真实策略的形状）。"""

    name = "declared_via_min_bars"
    description = "策略类型：测试\n核心指标：-\n适用场景：-\n入场逻辑：-"

    def __init__(self, context):
        super().__init__(context)
        self.min_bars = 730

    def on_bar(self, kline, history):
        return None


class _LegacyDeclaredViaClassAttr(BaseStrategy):
    """老写法：类属性 `min_kline_length`（`box_time_range_breakout_strategy` 那种）。"""

    name = "legacy_via_class_attr"
    description = "策略类型：测试\n核心指标：-\n适用场景：-\n入场逻辑：-"
    min_kline_length = 300

    def on_bar(self, kline, history):
        return None


class _DeclaresNothing(BaseStrategy):
    """什么都没声明：应当拿到基类默认值。"""

    name = "declares_nothing"
    description = "策略类型：测试\n核心指标：-\n适用场景：-\n入场逻辑：-"

    def on_bar(self, kline, history):
        return None


class TestMinBarsIsTheOnlyNameToWrite:
    """`min_bars` 是唯一可写的名字；`min_kline_length` 是只读别名。"""

    def test_declaring_min_bars_is_visible_through_the_alias(self):
        """新写法设了 `min_bars`，老名字（别名）必须读到同一个数。

        这条保证「两类策略文件在引擎眼里是同一件事」：引擎仍在若干地方读
        `min_kline_length`（含仓库外脚本 `backtest_smma_fractal.py`），别名断了那些
        读取点会静默退回默认值。
        """
        strategy = _make(_DeclaredViaMinBars)
        assert strategy.min_bars == 730
        assert strategy.min_kline_length == 730

    def test_legacy_class_attr_still_works_and_sets_min_bars(self):
        """老写法（类属性 300）仍然有效，且**反向**把 `min_bars` 填成同一个数。

        反向填充是必须的：`min_bars` 现在是唯一名字，一个只写了老名字的策略若不回填，
        任何只读 `min_bars` 的代码（引擎的需求线、体检）都会读不到。
        """
        strategy = _make(_LegacyDeclaredViaClassAttr)
        assert strategy.min_kline_length == 300
        assert strategy.min_bars == 300

    def test_writing_the_alias_warns(self):
        """在 `__init__` 里写老名字必须给出弃用警告——但**不改变行为**。

        只警告不报错：策略文件在 `~/.tradelogx/strategies/`（不在 git 里，改不动所有
        副本），一条会让老策略起不来的硬化是不可接受的。所以这里是「可见的软收口」。
        """
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")

            class _WritesAlias(BaseStrategy):
                name = "writes_alias"
                description = "策略类型：测试\n核心指标：-\n适用场景：-\n入场逻辑：-"

                def __init__(self, context):
                    super().__init__(context)
                    self.min_kline_length = 120  # noqa: PLC0415 - 本用例正在验证弃用写法的行为

                def on_bar(self, kline, history):
                    return None

            strategy = _WritesAlias(_ctx())

        assert any(issubclass(w.category, DeprecationWarning) for w in caught), (
            f"写老名字应当有 DeprecationWarning，实际 {[w.category for w in caught]}"
        )
        # 软收口：值仍然生效，且同步到新名字
        assert strategy.min_bars == 120

    def test_declares_nothing_gets_the_base_default(self):
        """什么都没声明 → 默认 500（两条名字都读得到）。"""
        strategy = _make(_DeclaresNothing)
        assert strategy.min_bars == BaseStrategy.MIN_BARS_DEFAULT
        assert strategy.min_kline_length == BaseStrategy.MIN_BARS_DEFAULT

    def test_the_default_is_one_constant(self):
        """默认值只有一个来源：`MIN_BARS_DEFAULT`。

        此前它写死在 `min_kline_length: int = 500` 那一行，而 `live_mode.HISTORY_FLOOR`
        又抄了一份 500。两份默认值一旦分家，「策略没声明时引擎留多少」就有两个答案。
        """
        assert BaseStrategy.MIN_BARS_DEFAULT == 500
        from apps.strategy_engine.live_mode import LiveStrategyRunner

        assert LiveStrategyRunner.HISTORY_FLOOR == BaseStrategy.MIN_BARS_DEFAULT


class TestTheAliasIsReadableFromTheClass:
    """`getattr(cls, "min_kline_length", 0)` 必须仍返回默认值。

    `backend/scripts/backtest_smma_fractal.py:804` 是这么读的（在**类**上取，没有实例）。
    把 `min_kline_length` 做成纯实例 `property` 会让那一行拿到 `property` 对象本身或
    默认值 0 —— 脚本不会报错，只是预热根数静默变成 0，而它的日志照样打得出来。
    """

    def test_class_level_access_returns_an_int(self):
        value = getattr(_LegacyDeclaredViaClassAttr, "min_kline_length", 0)
        assert isinstance(value, int), (
            f"类级读取拿到了 {type(value).__name__}；"
            "backtest_smma_fractal.py 会把它拿去 min() 而静默算错预热根数"
        )
        assert value == 300

    def test_class_level_access_on_a_strategy_that_declares_nothing(self):
        value = getattr(_DeclaresNothing, "min_kline_length", 0)
        assert isinstance(value, int)
        assert value == BaseStrategy.MIN_BARS_DEFAULT

    def test_class_level_default_matches_an_instance_that_declares_nothing(self):
        """类级与实例级在「没声明」时必须给出**同一个数**。

        这是分级读取的一致性底线：类级回答「这个类没写老值」，实例级回答「这个实例
        没写新值」，两条都该落到 `MIN_BARS_DEFAULT`。若是分家，`backtest_smma_fractal.py`
        按类级读到的预热根数会与实盘 runner 按实例算的不一致，而两边日志都正常。
        """
        cls_value = getattr(_DeclaresNothing, "min_kline_length", 0)
        instance = _DeclaresNothing(_ctx())
        assert cls_value == instance.min_bars == instance.min_kline_length


class TestClassLevelAliasCannotKnowAnInstanceValue:
    """在**类**上读老名字，只能拿到「类上写过的」或默认值，拿不到实例算出来的值。

    这是别名机制的**固有边界**，不是缺陷：`min_bars = lookback + atr_period + 2` 依赖
    实例参数，类上根本不存在那个数。但边界必须被明确写下，否则下一个人会以为「类级读取
    也该给出 136」而去改实现。

    `backtest_smma_fractal.py:804` 正踩在这个边界上：它按类级取，所以对一个**只写
    `self.min_bars`** 的策略，它拿到的是默认值 500 而不是策略实际要的数。方向见
    `test_the_age_of_this_limitation_is_safe_not_silent`。
    """

    def test_a_strategy_declaring_only_min_bars_reports_the_default_at_class_level(
        self,
    ):
        instance = _DeclaredViaMinBars(_ctx())
        assert instance.min_bars == 730
        assert instance.min_kline_length == 730
        # 类上不存在这个数，只能给默认值
        assert getattr(_DeclaredViaMinBars, "min_kline_length", 0) == (
            BaseStrategy.MIN_BARS_DEFAULT
        )

    def test_the_age_of_this_limitation_is_safe_not_silent(self):
        """把边界的方向钉下来：类级默认 500 < 真实需求 730 ⇒ 脚本会**少**预热。

        这条用例的价值不在断言本身，而在它写清了后果方向：少预热对
        `backtest_smma_fractal.py` 的「信号形态诊断」影响有限（它只喂数据看形状），
        但若有人把这个脚本当性能基准读，那 230 根的差异就是不成立的。
        """
        instance = _DeclaredViaMinBars(_ctx())
        cls_value = getattr(_DeclaredViaMinBars, "min_kline_length", 0)
        assert cls_value < instance.min_bars, (
            "类级默认已 >= 真实需求时，这条边界不再有害，可以删掉本用例"
        )


class TestNoStrategyFileUsesBothNames:
    """真实策略文件里不许**同时**写两个名字。

    这是「统一」的可执行部分：引擎侧已经容错（两个都读、取 max），但一个文件同时写两个
    值仍然是隐患——`box_time_range_breakout_strategy` 就写着 `min_kline_length = 300`
    而在 `on_bar` 里另算 `lookback + atr_period + 2`，两个数谁对没人说得清。

    扫的是 `~/.tradelogx/strategies/`（生产实际加载的目录）。目录不存在时跳过——那些
    环境里没有策略文件可查，不是失败。
    """

    def test_no_file_declares_both(self):
        import re
        from pathlib import Path

        directory = Path.home() / ".tradelogx" / "strategies"
        if not directory.is_dir():
            pytest.skip(f"{directory} 不存在（该环境不加载策略文件）")

        offenders = []
        for path in sorted(directory.glob("*.py")):
            text = path.read_text(errors="ignore")
            has_alias = re.search(r"^\s*min_kline_length\s*=", text, re.M)
            has_min_bars = re.search(r"^\s*(self\.)?min_bars\s*=", text, re.M)
            if has_alias and has_min_bars:
                offenders.append(path.name)

        assert not offenders, (
            f"这些策略文件同时写了两个名字：{offenders}。"
            "`min_bars` 是唯一的名字；`min_kline_length` 只留给还没迁移的老文件"
        )
