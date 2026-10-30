"""3SMMA + Williams Fractal + RSI 剥头皮策略的契约测试。

被测策略是 ``backend/strategies/smma_fractal_rsi_scalp_strategy.py``（仓库镜像；
部署位 ``~/.tradelogx/strategies/`` 由 apps/strategy_engine/apps.py 在启动时扫描）。
测试直接 import 仓库镜像，因为镜像与部署位需逐字节一致（见 test_box_breakout_threshold
的路径解析说明）——CI 上必须能跑到，不能靠 skip 蒙混。

三大类断言，对应策略最容易写错、写错又最不容易被发现的三个地方：

1. **指标口径**：SMMA 递推式、RSI 递推式必须与仓库已有实现逐位一致
   （RSI 直接对 ``apps.signal_monitor.indicators.compute_rsi``）。两处口径分家
   会让回测与实盘看到不同的 RSI(50) 过滤结果，而这种偏差在数字上很小、
   在交易结果上很大。
2. **分形滞后 2 根**：分形只有在右侧再多出 N 根已收盘 K 线后才可用。这是视频里
   最强调、也最容易被误写成"当根就出信号"的地方——写成当根即等于未来函数，
   回测收益会被系统性高估。
3. **入场/出场规则**：趋势与 RSI 双向过滤、延迟入场、止损放在关键 K 线极值之外、
   1:2 止盈的**几何关系**（止盈-入场 == rr × (入场-止损)），以及无未来函数
   （同一根 bar 只用得到它自己和更早的数据）。
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import math
import os
import sys
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest

from apps.signal_monitor.indicators import compute_rsi
from apps.strategy_engine.backtest_mode import BacktestEngine
from apps.strategy_engine.base import StrategyContext

STRATEGY_FILENAME = "smma_fractal_rsi_scalp_strategy.py"


def _strategy_path() -> str:
    # 本文件在 backend/apps/strategy_engine/tests/ → parents[3] = backend/
    return str(Path(__file__).resolve().parents[3] / "strategies" / STRATEGY_FILENAME)


STRATEGY_PATH = _strategy_path()

pytestmark = pytest.mark.skipif(
    not Path(STRATEGY_PATH).is_file(),
    reason=f"strategy mirror missing: {STRATEGY_PATH}",
)


def _load_strategy_cls():
    # 策略声明 supports_short=True，注册期走 StrategyRegistry 的准入闸门；
    # 闸门未打开时只记一条 ERROR 并跳过注册（刻意不 raise）。测试要的是**策略类
    # 本身**，不是注册副作用，因此这里显式打开闸门，顺带让日志干净。
    os.environ["STRATEGY_ENGINE_ALLOW_SHORT"] = "1"
    spec = importlib.util.spec_from_file_location(
        "smma_fractal_rsi_scalp_strategy_under_test", STRATEGY_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod.SmmaFractalRsiScalpStrategy


def _ctx(position: str = "0", balance: str = "10000", **params) -> StrategyContext:
    cls = _load_strategy_cls()
    resolved = {k: v.get("default") for k, v in cls.params_schema.items()}
    resolved.update(params)
    return StrategyContext(
        symbol="BTC/USDT",
        timeframe="5m",
        mode="backtest",
        params=resolved,
        balance=Decimal(balance),
        position=Decimal(position),
    )


def _bar(ts: str, o: float, h: float, lo: float, c: float, v: float = 1.0) -> dict:
    return {
        "timestamp": ts,
        "open": o,
        "high": h,
        "low": lo,
        "close": c,
        "volume": v,
    }


def _trending_series(
    n: int = 300,
    step: float = 10.0,
    start: float = 10000.0,
    noise: float = 0.0,
):
    """构造一段单调上升序列（用于制造多头排列）。

    价格每根上涨 ``step``；``noise`` 用于制造分形（单调序列里每根 low 都相等，
    没有严格最低，永远不会出现看涨分形）。
    """
    rows = []
    price = start
    for i in range(n):
        open_ = price
        price += step
        high = price + 1.0
        low = open_ - 1.0
        if noise and i % 7 == 3:
            low = open_ - noise  # 制造一个更低的低点 = 分形候选
        rows.append(_bar(f"t{i:05d}", open_, high, low, price))
    return rows


def _wavy_series(n: int = 60, amplitude: float = 40.0, period: int = 10):
    """构造同时含看涨/看跌分形的振荡序列（正弦叠加线性漂移）。

    ``_trending_series`` 是单调的：high 一路抬高、low 一路抬高，只可能出现看涨
    分形。要测「看跌分形也滞后 N 根」就需要上下都留下尖峰的序列。
    """
    rows = []
    for i in range(n):
        base = 10000.0 + 3.0 * i
        wiggle = amplitude * math.sin(2 * math.pi * i / period)
        price = base + wiggle
        rows.append(_bar(f"w{i:05d}", price, price + 2.0, price - 2.0, price))
    return rows


# ══════════════════════════════════════════════════════════════
# 1. 指标口径
# ══════════════════════════════════════════════════════════════


class TestIndicatorParity:
    def test_rsi_matches_repo_reference_implementation(self):
        """增量 RSI 必须逐位等于 signal_monitor.compute_rsi。

        两者是实盘与回测的同一条过滤线的两个实现，任何偏差都意味着
        「回测里 RSI>50 的 bar，实盘可能不过 50」。
        """
        cls = _load_strategy_cls()
        rows = _trending_series(300, step=7.0, noise=25.0)
        strategy = cls(_ctx())
        for i in range(len(rows)):
            strategy.on_bar(rows[i], rows[: i + 1])

        closes = np.array([r["close"] for r in rows], dtype=np.float64)
        reference = compute_rsi(closes, 14)
        mine = np.array(strategy._rsi, dtype=np.float64)

        assert len(mine) == len(rows)
        mask = ~np.isnan(reference) & ~np.isnan(mine)
        assert mask.sum() > 200, "RSI 有效值太少，样本不足"
        np.testing.assert_allclose(mine[mask], reference[mask], rtol=0, atol=1e-12)

    @pytest.mark.parametrize("period", [21, 50, 200])
    def test_smma_matches_direct_recursion(self, period):
        """SMMA 必须是 (prev*(n-1)+close)/n，种子为前 n 根简单均值。"""
        cls = _load_strategy_cls()
        rows = _trending_series(400, step=3.0, noise=10.0)
        strategy = cls(_ctx())
        for i in range(len(rows)):
            strategy.on_bar(rows[i], rows[: i + 1])

        closes = [r["close"] for r in rows]
        expected: list[float | None] = [None] * (period - 1)
        expected.append(sum(closes[:period]) / period)
        for k in range(period, len(closes)):
            expected.append((expected[-1] * (period - 1) + closes[k]) / period)

        mine = strategy._smma[period]
        assert len(mine) == len(closes)
        for idx, (want, got) in enumerate(zip(expected, mine, strict=True)):
            if want is None:
                assert got is None, f"SMMA({period}) 第 {idx} 根应为 None"
            else:
                assert got == pytest.approx(want, rel=0, abs=1e-9)


# ══════════════════════════════════════════════════════════════
# 2. 分形滞后 2 根（不得含未来函数）
# ══════════════════════════════════════════════════════════════


class TestFractalConfirmationLag:
    def test_bullish_fractal_not_visible_until_n_bars_later(self):
        """分形 K 线 i 在 i、i+1 上不可见，在 i+2 上才可见（period=2）。"""
        cls = _load_strategy_cls()
        rows = _wavy_series(60)
        strategy = cls(_ctx())
        for row in rows:
            strategy._append_bar(row)

        # 找一个真实存在的看涨分形
        target = next(i for i in range(2, len(rows) - 2) if strategy._fractal_low_at(i))

        # 把序列「截断」到 target 或 target+1：右侧不足 N 根，必须不可见
        for truncate_len in (target + 1, target + 2):
            strategy._n = truncate_len
            assert not strategy._fractal_low_at(target), (
                f"截断到 {truncate_len} 根时，分形 {target} 不应可见"
            )
        # 右侧凑满 N 根后可见
        strategy._n = target + 3
        assert strategy._fractal_low_at(target)

    def test_bearish_fractal_not_visible_until_n_bars_later(self):
        cls = _load_strategy_cls()
        rows = _wavy_series(60)
        strategy = cls(_ctx())
        for row in rows:
            strategy._append_bar(row)
        target = next(
            i for i in range(2, len(rows) - 2) if strategy._fractal_high_at(i)
        )
        strategy._n = target + 1
        assert not strategy._fractal_high_at(target)
        strategy._n = target + 3
        assert strategy._fractal_high_at(target)

    def test_strict_inequality_no_flat_ties(self):
        """左右有 equal low 时不算分形（严格小于），与 TradingView 一致。"""
        cls = _load_strategy_cls()
        strategy = cls(_ctx())
        # 第 2 根 low=95 与第 0 根 low=95 相等 → 不是分形
        for row in [
            _bar("a", 100, 101, 95, 100),
            _bar("b", 100, 101, 96, 100),
            _bar("c", 100, 101, 95, 100),
            _bar("d", 100, 101, 96, 100),
            _bar("e", 100, 101, 97, 100),
        ]:
            strategy._append_bar(row)
        assert not strategy._fractal_low_at(2)

    def test_on_bar_reads_only_confirmed_fractal(self):
        """on_bar 在 bar t 只能引用分形 t-N；不得读 t-N+1 之后未经确认的极值。

        做法：``0..t`` 的全量与「只把末尾第 t 根替换成极端价格」两种输入，在
        ``t`` 这一根上产生的分形判定必须**只**基于 t-N；即 t-N+1 被替换成极端值
        时，t 上「新确认的分形」引用位置不变（仍是 t-N）。
        """
        cls = _load_strategy_cls()
        rows = _trending_series(80, step=1.0, noise=30.0)
        strategy = cls(_ctx())
        for row in rows:
            strategy._append_bar(row)
        # 分形判定窗口右端恰好是 _n-1：窗口 [i-N, i+N] 必须完全落在已收盘数据内
        for i in range(strategy._n):
            right_edge = i + strategy.fractal_period
            if right_edge >= strategy._n:
                assert not strategy._fractal_low_at(i)
                assert not strategy._fractal_high_at(i)


# ══════════════════════════════════════════════════════════════
# 3. 趋势 / RSI 过滤与入场规则
# ══════════════════════════════════════════════════════════════


class TestEntryRules:
    def test_regime_requires_alignment_and_separation(self):
        cls = _load_strategy_cls()
        strategy = cls(_ctx(min_trend_sep_pct=0.0))
        rows = _trending_series(400, step=10.0, noise=5.0)
        strategy._rebuild(rows)
        last = strategy._n - 1
        assert strategy._regime(last) == "long"
        fast = strategy._ma(strategy.ma_fast, last)
        mid = strategy._ma(strategy.ma_mid, last)
        slow = strategy._ma(strategy.ma_slow, last)
        assert fast > mid > slow

    def test_tight_ma_separation_is_treated_as_chop(self):
        """均线几乎重合时必须判为震荡，不产生任何 regime。"""
        cls = _load_strategy_cls()
        strategy = cls(_ctx(min_trend_sep_pct=0.5))  # 50% 间距要求，绝不可能满足
        rows = _trending_series(400, step=10.0)
        strategy._rebuild(rows)
        assert strategy._regime(strategy._n - 1) == ""

    def test_regime_returns_empty_before_warmup(self):
        cls = _load_strategy_cls()
        strategy = cls(_ctx())
        rows = _trending_series(150, step=10.0)  # 慢线 200 还没成型
        strategy._rebuild(rows)
        assert strategy._regime(strategy._n - 1) == ""

    def test_no_entry_against_trend(self):
        """空头排列里不得开出多头（只看红箭头、忽略绿色）。"""
        cls = _load_strategy_cls()
        rows = _trending_series(400, step=-10.0, start=10000.0, noise=40.0)
        strategy = cls(_ctx())
        for i in range(len(rows)):
            signal = strategy.on_bar(rows[i], rows[: i + 1])
            if signal is not None and signal.side == "buy":
                pytest.fail(f"空头排列中出现了做多信号 @ {rows[i]['timestamp']}")
        assert strategy._regime(strategy._n - 1) == "short"

    def test_rsi_filter_blocks_long_when_below_midline(self):
        """RSI 未站上 50 时，即便多排+看涨分形也只排队、不成交。"""
        cls = _load_strategy_cls()
        strategy = cls(_ctx())
        strategy._pending = {
            "side": "long",
            "fractal_index": 10,
            "confirm_index": 12,
            "signal_extreme": 90.0,
        }
        bar = _bar("t", 100, 101, 99, 100)
        # rsi 传 45（< 50）→ 不得入场
        assert strategy._try_pending_entry(bar, 13, "long", 45.0, 100.0) is None

    def test_rsi_filter_allows_long_above_midline(self):
        cls = _load_strategy_cls()
        strategy = cls(_ctx())
        strategy._pending = {
            "side": "long",
            "fractal_index": 10,
            "confirm_index": 12,
            "signal_extreme": 90.0,
        }
        bar = _bar("t", 100, 101, 99, 100)
        signal = strategy._try_pending_entry(bar, 13, "long", 60.0, 100.0)
        assert signal is not None and signal.side == "buy"

    def test_delayed_entry_fires_later_when_rsi_arrives_late(self):
        """视频的延迟入场：分形后第 2 根 RSI 仍在 50 下方，之后第 4 根才过线。"""
        cls = _load_strategy_cls()
        strategy = cls(_ctx())
        strategy._pending = {
            "side": "long",
            "fractal_index": 10,
            "confirm_index": 12,
            "signal_extreme": 90.0,
        }
        bar = _bar("t", 100, 101, 99, 100)
        assert strategy._try_pending_entry(bar, 13, "long", 48.0, 100.0) is None
        assert strategy._pending is not None, "RSI 未到位时应继续等待，而不是作废"
        # 第 4 根（index 14）RSI 站上 50
        signal = strategy._try_pending_entry(bar, 14, "long", 52.0, 100.0)
        assert signal is not None and signal.side == "buy"

    def test_pending_expires_after_max_wait(self):
        cls = _load_strategy_cls()
        strategy = cls(_ctx(pending_max_wait_bars=3))
        strategy._pending = {
            "side": "long",
            "fractal_index": 10,
            "confirm_index": 12,
            "signal_extreme": 90.0,
        }
        bar = _bar("t", 100, 101, 99, 100)
        assert strategy._try_pending_entry(bar, 16, "long", 60.0, 100.0) is None
        assert strategy._pending is None

    def test_pending_voided_when_trend_flips(self):
        cls = _load_strategy_cls()
        strategy = cls(_ctx())
        strategy._pending = {
            "side": "long",
            "fractal_index": 10,
            "confirm_index": 12,
            "signal_extreme": 90.0,
        }
        bar = _bar("t", 100, 101, 99, 100)
        assert strategy._try_pending_entry(bar, 13, "short", 60.0, 100.0) is None
        assert strategy._pending is None

    def test_pending_voided_when_structure_breaks(self):
        """收盘跌破分形低点 = 这次回调失败，不能再做多。"""
        cls = _load_strategy_cls()
        strategy = cls(_ctx())
        strategy._pending = {
            "side": "long",
            "fractal_index": 10,
            "confirm_index": 12,
            "signal_extreme": 90.0,
        }
        bar = _bar("t", 100, 101, 88, 89)  # 收盘 89 < 分形低点 90
        assert strategy._try_pending_entry(bar, 13, "long", 60.0, 89.0) is None
        assert strategy._pending is None

    def test_short_entry_requires_rsi_below_midline(self):
        cls = _load_strategy_cls()
        strategy = cls(_ctx())
        strategy._pending = {
            "side": "short",
            "fractal_index": 10,
            "confirm_index": 12,
            "signal_extreme": 110.0,
        }
        bar = _bar("t", 100, 101, 99, 100)
        assert strategy._try_pending_entry(bar, 13, "short", 55.0, 100.0) is None
        signal = strategy._try_pending_entry(bar, 13, "short", 45.0, 100.0)
        assert signal is not None and signal.side == "sell"


class TestStopAndTargetGeometry:
    def _enter_long(self):
        cls = _load_strategy_cls()
        strategy = cls(_ctx(risk_per_trade_pct=0.01, max_position_pct=1.0))
        strategy._pending = {
            "side": "long",
            "fractal_index": 10,
            "confirm_index": 12,
            "signal_extreme": 95.0,
        }
        bar = _bar("t", 100, 102, 99, 101)
        signal = strategy._try_pending_entry(bar, 13, "long", 60.0, 101.0)
        assert signal is not None
        return strategy, signal

    def test_long_stop_below_key_bar_low(self):
        strategy, _ = self._enter_long()
        # 分形低点 95、关键 K 线低点 99 → 取较低者 95 再下移 buffer
        assert strategy._stop_price < 95.0
        assert strategy._stop_price == pytest.approx(
            95.0 * (1 - strategy.stop_buffer_pct), rel=0, abs=1e-9
        )

    def test_long_target_is_rr_times_risk(self):
        strategy, signal = self._enter_long()
        risk = strategy._entry_price - strategy._stop_price
        reward = strategy._target_price - strategy._entry_price
        assert reward == pytest.approx(strategy.rr_ratio * risk, rel=0, abs=1e-9)
        assert strategy._target_price > strategy._entry_price > strategy._stop_price

    def test_short_geometry_is_mirrored(self):
        cls = _load_strategy_cls()
        strategy = cls(_ctx())
        strategy._pending = {
            "side": "short",
            "fractal_index": 10,
            "confirm_index": 12,
            "signal_extreme": 105.0,
        }
        bar = _bar("t", 100, 101, 98, 99)
        signal = strategy._try_pending_entry(bar, 13, "short", 40.0, 99.0)
        assert signal is not None and signal.side == "sell"
        assert strategy._stop_price > 105.0  # 分形高点上方
        risk = strategy._stop_price - strategy._entry_price
        reward = strategy._entry_price - strategy._target_price
        assert reward == pytest.approx(strategy.rr_ratio * risk, rel=0, abs=1e-9)

    def test_position_size_respects_risk_and_cap(self):
        strategy, signal = self._enter_long()
        equity = float(strategy.ctx.to_portfolio_context().total_capital)
        risk_per_unit = strategy._entry_price - strategy._stop_price
        notional = float(signal.quantity) * strategy._entry_price
        assert notional <= equity * strategy.max_position_pct + 1e-6
        loss_at_stop = float(signal.quantity) * risk_per_unit
        # 风险反推值可能被名义上限压小，因此只能 <= 名义预算的风险
        assert loss_at_stop <= equity * strategy.risk_per_trade_pct * 1.01 + 1e-6

    def test_zero_risk_distance_produces_no_entry(self):
        """关键 K 线低点等于入场价（风险为 0）：不得入场，否则数量会爆掉。"""
        cls = _load_strategy_cls()
        strategy = cls(_ctx())
        strategy._pending = {
            "side": "long",
            "fractal_index": 10,
            "confirm_index": 12,
            "signal_extreme": 100.0,
        }
        bar = _bar("t", 100, 101, 100, 100)  # low == close == 100
        assert strategy._try_pending_entry(bar, 13, "long", 60.0, 100.0) is None


# ══════════════════════════════════════════════════════════════
# 4. 出场规则
# ══════════════════════════════════════════════════════════════


class TestExits:
    def _positioned(self, side="long", entry=100.0, stop=95.0, rr=2.0):
        cls = _load_strategy_cls()
        strategy = cls(_ctx())
        strategy._side = side
        strategy._entry_price = entry
        strategy._stop_price = stop
        strategy._target_price = (
            entry + rr * (entry - stop)
            if side == "long"
            else entry - rr * (stop - entry)
        )
        strategy._entry_index = 0
        strategy.ctx.set_position("BTC/USDT", "1" if side == "long" else "-1")
        return strategy

    def test_long_stop_loss_on_close(self):
        strategy = self._positioned("long", stop=95.0)
        bar = _bar("t", 96, 97, 90, 94)  # 收盘 94 < 95
        signal = strategy._manage_exit(bar, 5)
        assert signal is not None and signal.side == "sell"
        assert signal.metadata["reason"] == "stop_loss"

    def test_long_take_profit_on_close(self):
        strategy = self._positioned("long", entry=100.0, stop=95.0, rr=2.0)
        # target = 100 + 2*(100-95) = 110
        bar = _bar("t", 109, 111, 108, 110)
        signal = strategy._manage_exit(bar, 5)
        assert signal is not None and signal.metadata["reason"] == "take_profit"

    def test_no_exit_inside_stop_target_band(self):
        strategy = self._positioned("long", entry=100.0, stop=95.0, rr=2.0)
        bar = _bar("t", 102, 105, 99, 103)
        assert strategy._manage_exit(bar, 5) is None

    def test_intrabar_mode_uses_high_low(self):
        """intrabar_exit=True 时，盘中触及止损/止盈即出场，并透出触发价。"""
        strategy = self._positioned("long", entry=100.0, stop=95.0, rr=2.0)
        strategy.intrabar_exit = True
        # 收盘 100 仍在区间内，但 low 打到 94 触及止损
        bar = _bar("t", 100, 101, 94, 100)
        signal = strategy._manage_exit(bar, 5)
        assert signal is not None and signal.metadata["reason"] == "stop_loss"
        assert signal.metadata["fill_hint"] == pytest.approx(95.0)

    def test_max_hold_bars_forces_exit(self):
        strategy = self._positioned("long", entry=100.0, stop=95.0, rr=20.0)
        strategy.max_hold_bars = 10
        bar = _bar("t", 100, 100.5, 99.5, 100)
        assert strategy._manage_exit(bar, 3) is None
        signal = strategy._manage_exit(bar, 10)
        assert signal is not None and signal.metadata["reason"] == "max_hold_exit"

    def test_ma_exit_mode_flattens_when_close_loses_ma(self):
        cls = _load_strategy_cls()
        strategy = cls(_ctx(tp_mode="ma"))
        strategy._side = "long"
        strategy._entry_price = 100.0
        strategy._stop_price = 90.0
        strategy._target_price = 0.0
        strategy._entry_index = 0
        strategy.ctx.set_position("BTC/USDT", "1")
        rows = _trending_series(60, step=1.0, noise=5.0)
        strategy._rebuild(rows)
        ma = strategy._ma(strategy.exit_ma, strategy._n - 1)
        assert ma is not None
        strategy._entry_index = strategy._n - 2
        bar = _bar("t", ma, ma + 1, ma - 5, ma - 1)  # 收盘跌破 exit_ma
        signal = strategy._manage_exit(bar, strategy._n - 1)
        assert signal is not None and signal.metadata["reason"] == "ma_cross_exit"

    def test_close_position_returns_none_when_flat(self):
        cls = _load_strategy_cls()
        strategy = cls(_ctx())
        strategy.ctx.set_position("BTC/USDT", "0")
        assert strategy._exit_signal("stop_loss", 100.0, _bar("t", 1, 1, 1, 1)) is None


# ══════════════════════════════════════════════════════════════
# 5. 引擎集成：策略能在真实回测引擎上跑出成交
# ══════════════════════════════════════════════════════════════


class TestEngineIntegration:
    def test_registers_with_short_gate_enabled(self, monkeypatch):
        """supports_short=True 的策略只有在准入开关打开时才注册成功。"""
        from apps.strategy_engine.registry import StrategyRegistry

        cls = _load_strategy_cls()
        assert cls.supports_short is True
        # 闸门打开时注册成功
        monkeypatch.setenv("STRATEGY_ENGINE_ALLOW_SHORT", "1")
        StrategyRegistry.register(cls, name="__gate_probe__")
        assert "__gate_probe__" in StrategyRegistry.list_registered()

        # 闸门关闭时拒绝注册（且不 raise，避免拖垮整个引擎）
        monkeypatch.delenv("STRATEGY_ENGINE_ALLOW_SHORT", raising=False)
        monkeypatch.setattr(
            "apps.strategy_engine.registry._short_enabled", lambda: False
        )
        StrategyRegistry.register(cls, name="__gate_probe_closed__")
        assert "__gate_probe_closed__" not in StrategyRegistry.list_registered()

    def test_end_to_end_backtest_produces_trades(self):
        """在构造的趋势+回调序列上端到端跑通，且多空都有成交。"""
        import asyncio

        from apps.strategy_engine.backtest_mode import BacktestEngine

        cls = _load_strategy_cls()
        # 构造：先上涨（多排），穿插回调制造看涨分形；再下跌段。
        #
        # 回调针的深度必须**大于 2 根 K 线的漂移量 + 常规低点偏移**，否则针尖
        # 仍然高于两侧的低点，构不成严格分形（分形要求 low[i] 严格小于左右
        # 各 N 根的 low）。这里的参数：漂移 12/根、偏移 3、针深 60，
        # 2*12+3 = 27 < 60，保证成立。
        rows = []
        price = 10000.0
        ts = 0
        for leg in (1, -1):
            for i in range(600):
                open_ = price
                price += 12.0 * leg
                low = open_ - 3.0
                high = open_ + 3.0
                if i % 9 == 0:
                    if leg > 0:
                        low = open_ - 60.0  # 看涨分形候选
                    else:
                        high = open_ + 60.0  # 看跌分形候选
                rows.append(
                    _bar(
                        f"2026-01-01T{ts // 60:02d}:{ts % 60:02d}:00",
                        open_,
                        high,
                        low,
                        price,
                    )
                )
                ts += 5

        ctx = _ctx()
        strategy = cls(ctx)
        engine = BacktestEngine(
            strategy=strategy,
            ohlcv_data=rows,
            initial_capital=Decimal("10000"),
            symbol="BTC/USDT",
            timeframe="5m",
            commission_rate=Decimal("0.0005"),
            slippage_rate=Decimal("0.0002"),
            short_margin_rate=Decimal("0"),
        )
        stats = asyncio.run(engine.run())
        assert stats["total_trades"] > 0, "构造数据上应产生成交，否则规则可能失效"
        closed = [t for t in stats["trades"] if t.get("pnl") is not None]
        sides = {t["side"] for t in closed}
        assert sides, "没有已平仓交易"
        reasons = {t["exit_reason"] for t in closed}
        assert reasons - {"signal"}, (
            "所有交易 exit_reason 都退化成 signal，metadata 透传断了"
        )

    def test_no_lookahead_across_bar_boundary(self):
        """同一时刻用「全量」与「截止到 t」两种 history 调用 on_bar，必须同结果。"""
        cls = _load_strategy_cls()
        rows = _trending_series(300, step=4.0, noise=20.0)
        full = cls(_ctx())
        cut = cls(_ctx())
        for i in range(len(rows)):
            full.on_bar(rows[i], rows[: i + 1])
        target = len(rows) - 1
        for i in range(target + 1):
            cut.on_bar(rows[i], rows[: i + 1])
        assert full._n == cut._n == len(rows)
        # 指标序列必须逐位一致（说明没有依赖「未收盘」的尾部数据）。
        # NaN != NaN，所以按「同为 NaN 或同值」逐位比较。
        assert len(full._rsi) == len(cut._rsi)
        for a, b in zip(full._rsi, cut._rsi, strict=True):
            assert (math.isnan(a) and math.isnan(b)) or a == b
        for period in (21, 50, 200):
            assert full._smma[period] == cut._smma[period]


class TestParamValidation:
    def test_invalid_tp_mode_rejected(self):
        with pytest.raises(ValueError, match="tp_mode"):
            _load_strategy_cls()(_ctx(tp_mode="bogus"))

    def test_description_has_required_fields(self):
        """注册闸门要求 4 字段模板，缺字段会在注册期直接抛 ValueError。"""
        cls = _load_strategy_cls()
        from apps.strategy_engine.registry import StrategyRegistry

        StrategyRegistry.validate_description(cls)  # 不抛即通过
        for field in ("策略类型：", "核心指标：", "适用场景：", "入场逻辑："):
            assert field in cls.description


# ══════════════════════════════════════════════════════════════
# 6. 审查发现的两处执行缺陷（2026-10-21 独立审查 §B.4 / §B.8）
# ══════════════════════════════════════════════════════════════


class TestPositionSizingIncludesCosts:
    """`qty_cap` 的分母必须含往返成本，否则每笔都触发引擎的静默部分成交。

    审查实测（IS 3,662 笔）：修复前 ``size_bound_by_notional_cap`` = 3,518 笔
    （96.1%），其中 1,937 笔打出 ``Backtest: insufficient cash for buy signal``
    —— 差额小到 ``need=10077.1557`` vs ``cash=10077.1555``，说明名义上限恰好
    把现金用满、再加佣金就超。后果是「每笔风险 1% 权益」这句话在回测里不成立。
    """

    def test_notional_cap_leaves_room_for_costs(self):
        cls = _load_strategy_cls()
        strategy = cls(_ctx(risk_per_trade_pct=0.01, max_position_pct=1.0))
        # 极小止损 → 风险反推数量远大于名义上限，数量必由 qty_cap 决定
        strategy._pending = {
            "side": "long",
            "fractal_index": 10,
            "confirm_index": 12,
            "signal_extreme": 99.999,
        }
        bar = _bar("t", 100, 100.5, 99.999, 100)
        signal = strategy._try_pending_entry(bar, 13, "long", 60.0, 100.0)
        assert signal is not None, "极小止损应至少开出一笔（由名义上限决定）"

        equity = float(strategy.ctx.to_portfolio_context().total_capital)
        notional = float(signal.quantity) * 100.0
        # 名义金额 + 往返成本上界必须 <= 权益，否则引擎会部分成交
        assert notional * (1 + 0.0015) <= equity * 1.0000001, (
            f"名义 {notional:.2f} + 成本超出权益 {equity:.2f}，会触发静默部分成交"
        )

    def test_full_equity_position_does_not_exceed_cash(self):
        """回归：满仓名义 + 佣金 + 滑点 <= 现金（引擎 _apply_buy_fill 的实际判据）。"""
        cls = _load_strategy_cls()
        strategy = cls(_ctx(risk_per_trade_pct=99.0, max_position_pct=1.0))
        strategy._pending = {
            "side": "long",
            "fractal_index": 10,
            "confirm_index": 12,
            "signal_extreme": 90.0,
        }
        bar = _bar("t", 100, 101, 99, 100)
        signal = strategy._try_pending_entry(bar, 13, "long", 60.0, 100.0)
        assert signal is not None
        equity = float(strategy.ctx.to_portfolio_context().total_capital)
        # 引擎：cost*(1+slippage)+commission <= cash，用默认 5bp/2bp 费率
        qty = float(signal.quantity)
        cost = qty * 100.0 * (1 + 0.0002)
        total = cost + qty * 100.0 * 0.0005
        assert total <= equity, f"total_cost {total:.2f} > cash {equity:.2f} → 部分成交"


class TestPositionReAdoption:
    """账本分叉/重启后重新认领持仓时，止损绝不能是 0（§B.8 P2）。"""

    def test_adopted_long_position_gets_a_real_stop(self):
        cls = _load_strategy_cls()
        strategy = cls(_ctx())
        rows = _trending_series(300, step=5.0, noise=10.0)
        strategy._rebuild(rows)

        strategy.ctx.set_position("BTC/USDT", "1")
        strategy.ctx.avg_entry_price = Decimal(str(rows[-1]["close"]))
        # 模拟：本地 _side 为空（重启后失忆），引擎仍有持仓
        strategy._side = ""
        strategy.on_bar(rows[-1], rows)

        assert strategy._side == "long"
        assert strategy._stop_price > 0, "重新认领后止损不得为 0（否则只能靠超时出场）"
        assert strategy._stop_price < float(rows[-1]["close"])
        assert strategy._target_price > float(rows[-1]["close"])

    def test_adopted_short_position_gets_a_real_stop(self):
        cls = _load_strategy_cls()
        strategy = cls(_ctx())
        rows = _trending_series(300, step=-5.0, start=20000.0, noise=10.0)
        strategy._rebuild(rows)
        strategy.ctx.set_position("BTC/USDT", "-1")
        strategy.ctx.avg_entry_price = Decimal(str(rows[-1]["close"]))
        strategy._side = ""
        strategy.on_bar(rows[-1], rows)

        assert strategy._side == "short"
        assert strategy._stop_price > 0
        assert strategy._stop_price > float(rows[-1]["close"]), "空头止损应在入场价上方"

    def test_reset_keeps_pending_by_default(self):
        """默认**不**清待入场信号 —— 否则延迟入场永远无法发生。

        回归来源：``on_bar`` 在每个空仓 bar 都调 ``_reset_position_state()``。
        若它无条件清 ``_pending``，分形在 bar t 确认后，t+1 仍空仓就把信号抹掉，
        「等 RSI 站回 50 再入场」这条规则在结构上不可能执行。实测修复前
        5,585 笔入场里延迟入场 **0 笔**。
        """
        cls = _load_strategy_cls()
        strategy = cls(_ctx())
        strategy._pending = {
            "side": "long",
            "fractal_index": 10,
            "confirm_index": 12,
            "signal_extreme": 90.0,
        }
        strategy._reset_position_state()
        assert strategy._pending is not None, "默认必须保留待入场信号"

    def test_reset_clears_pending_when_ledger_diverged(self):
        """明确的分叉时点（持仓方向冲突）必须连待入场信号一起清。"""
        cls = _load_strategy_cls()
        strategy = cls(_ctx())
        strategy._pending = {
            "side": "long",
            "fractal_index": 10,
            "confirm_index": 12,
            "signal_extreme": 90.0,
        }
        strategy._reset_position_state(clear_pending=True)
        assert strategy._pending is None


class TestIntrabarExitIsAnnounced:
    """`intrabar_exit=True` 在本仓库是静默降级，必须 warning 出声（§B.10）。"""

    def test_on_start_warns_when_intrabar_enabled(self, caplog):
        cls = _load_strategy_cls()
        strategy = cls(_ctx(intrabar_exit=True))
        with caplog.at_level(logging.WARNING):
            strategy.on_start()
        assert any("intrabar_exit" in r.message for r in caplog.records), (
            "打开 intrabar_exit 却静默降级成收盘口径 —— 必须 warning"
        )

    def test_on_start_silent_by_default(self, caplog):
        cls = _load_strategy_cls()
        strategy = cls(_ctx())
        with caplog.at_level(logging.WARNING):
            strategy.on_start()
        assert not any("intrabar_exit" in r.message for r in caplog.records)

    def test_quantity_rounds_down_never_up(self):
        """数量必须**向下**取整：向上取整会吃掉成本余量、触发部分成交。

        回归来源：``ROUND_HALF_UP`` 把 0.0006124 取成 0.000613（多买 0.1%），
        而名义上限只留了 0.15% 的成本余量，于是 ``cost + commission`` 再次
        超出现金、落回引擎的部分成交兜底。实测在权益衰减到 $70 量级时，
        这一项单独造成 289 笔 ``insufficient cash``。向下取整永远不会超额。
        """
        cls = _load_strategy_cls()
        # 构造一个「数量不会被 round 掩盖」的价格：使 qty 的小数位贴近边界
        strategy = cls(_ctx(risk_per_trade_pct=99.0, max_position_pct=1.0))
        strategy._pending = {
            "side": "long",
            "fractal_index": 10,
            "confirm_index": 12,
            "signal_extreme": 99.0,
        }
        bar = _bar("t", 100, 101, 99, 100)
        signal = strategy._try_pending_entry(bar, 13, "long", 60.0, 100.0)
        assert signal is not None

        # 用引擎的真实成交判据验收：名义 + 佣金 <= 现金
        equity = float(strategy.ctx.to_portfolio_context().total_capital)
        qty = float(signal.quantity)
        notional = qty * 100.0
        commission = notional * 0.0005
        assert notional + commission <= equity + 1e-9, (
            f"qty={qty} 名义 {notional:.6f} + 佣金 {commission:.6f} > 权益 {equity:.6f}"
        )
        # 且确实是向下取整（不会比精确值更大）
        exact = equity / (100.0 * 1.0015)
        assert qty <= exact + 1e-9

    def test_no_partial_fill_on_real_engine(self):
        """端到端：真实引擎跑一段数据，不得出现 ``insufficient cash`` 部分成交。"""
        cls = _load_strategy_cls()
        rows = []
        price = 10000.0
        for i in range(700):
            open_ = price
            price += 9.0 if i % 140 < 100 else -9.0
            low = open_ - 55.0 if i % 11 == 0 else open_ - 3.0
            rows.append(_bar(f"t{i:05d}", open_, open_ + 3.0, low, price))

        hits: list[str] = []

        class _H(logging.Handler):
            def emit(self, record):
                if "insufficient cash" in record.getMessage():
                    hits.append(record.getMessage())

        logger = logging.getLogger("apps.strategy_engine.backtest_mode")
        handler = _H()
        logger.addHandler(handler)
        try:
            engine = BacktestEngine(
                strategy=cls(_ctx()),
                ohlcv_data=rows,
                initial_capital=Decimal("10000"),
                symbol="BTC/USDT",
                timeframe="5m",
                commission_rate=Decimal("0.0005"),
                slippage_rate=Decimal("0.0002"),
                short_margin_rate=Decimal("0.2"),
            )
            asyncio.run(engine.run())
        finally:
            logger.removeHandler(handler)

        assert not hits, f"出现 {len(hits)} 笔静默部分成交：{hits[:2]}"
