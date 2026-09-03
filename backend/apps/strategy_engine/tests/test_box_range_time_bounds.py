"""Tests for box time-extent (left/right) fields in detect_box_range.

语义（与 indicators.py 中 _box_time_stats 一致）：
- start_index = 第一根 close 落在 [lower×(1-tol), upper×(1+tol)] 内的 K 线
- end_index   = 最后一根 close 仍在箱内的 K 线
- duration_bars = start → end 的跨度（中间离箱再回来算一整段）
- started_in_box = 窗口开头就已处于箱体（真实进入点早于窗口）
"""

from __future__ import annotations

import pytest

from apps.agent.tools.box_range import DetectBoxRangeTool
from apps.strategy_engine.indicators import _box_time_stats, detect_box_range


def _klines(
    closes: list[float],
    timestamps: list | None = None,
) -> list[dict]:
    """构造箱体 K 线：high/low 形态与被识别为箱体的典型模式一致（箱体约 [100, 110]），
    close 由调用方指定以控制"在箱/离箱"时间点。"""
    n = len(closes)
    highs, lows = [], []
    for i in range(n):
        cycle = i % 5
        if cycle < 3:  # 上半部
            highs.append(110)
            lows.append(105)
        elif cycle == 3:  # 跌出
            highs.append(107)
            lows.append(103)
        else:  # 贴下
            highs.append(106)
            lows.append(100)
    return [
        {
            **({"timestamp": timestamps[i]} if timestamps is not None else {}),
            "open": (high + low) / 2,
            "high": high,
            "low": low,
            "close": closes[i],
            "volume": 1000,
        }
        for i, (high, low) in enumerate(zip(highs, lows))
    ]


def _run(closes, timestamps: list | None = None, **kwargs):
    result = detect_box_range(
        klines=_klines(closes, timestamps),
        max_width_pct=0.15,
        pivot_window=2,
        atr_period=3,
        **kwargs,
    )
    assert result["is_ranging"] is True, result.get("reason")
    return result["box"]


# 离箱边界：close <= 98 或 >= 112（无论 tolerance_pct 取 [0.001, 0.01] 中何值，
# 在箱区间均包含 99..111.1，绝不包含 98/112）
_OUT_HIGH = 112.0
_OUT_LOW = 98.0
_IN = 106.0


def test_entry_exit_reentry_span():
    """已知进出点：前 3 根在箱外 → 13 根在箱内 → 3 根离箱 → 6 根回箱 → 尾部离箱。

    结束取最后一根在箱内的 K 线，时长算整段跨度（含中间离箱间隙）。
    """
    closes = (
        [_OUT_HIGH] * 3
        + [_IN] * 13  # index 3..15
        + [_OUT_LOW] * 3  # index 16..18
        + [_IN] * 6  # index 19..24
        + [_OUT_HIGH] * 5  # index 25..29
    )
    timestamps = [f"2026-01-01T00:{i:02d}:00Z" for i in range(len(closes))]
    box = _run(closes, timestamps=timestamps)

    assert box["start_index"] == 3
    assert box["end_index"] == 24
    assert box["started_in_box"] is False
    assert box["duration_bars"] == 22  # 3 → 24 共 22 根，含 16..18 的离箱间隙
    assert box["bars_in_box"] == 19  # 13 + 6
    assert box["start_timestamp"] == "2026-01-01T00:03:00Z"
    assert box["end_timestamp"] == "2026-01-01T00:24:00Z"
    assert box["duration_seconds"] == 21 * 60  # 21 根 × 1 分钟


def test_started_inside_box():
    """窗口开头就在箱内：start=0 且 started_in_box=True，Box 覆盖整个窗口。"""
    closes = [_IN] * 30
    timestamps = [f"2026-01-01T00:{i:02d}:00Z" for i in range(len(closes))]
    box = _run(closes, timestamps=timestamps)

    assert box["start_index"] == 0
    assert box["end_index"] == 29
    assert box["started_in_box"] is True
    assert box["duration_bars"] == 30
    assert box["bars_in_box"] == 30
    assert box["start_timestamp"] == "2026-01-01T00:00:00Z"
    assert box["end_timestamp"] == "2026-01-01T00:29:00Z"


def test_numeric_epoch_timestamps():
    """数值时间戳（毫秒 epoch）也能给出 duration_seconds。"""
    closes = (
        [_OUT_HIGH] * 3
        + [_IN] * 13  # 3..15
        + [_OUT_LOW] * 3
        + [_IN] * 6  # 19..24
        + [_OUT_HIGH] * 5
    )
    epoch_ms = [1_700_000_000_000 + i * 60_000 for i in range(len(closes))]
    box = detect_box_range(
        klines=_klines(closes, timestamps=epoch_ms),
        max_width_pct=0.15,
        pivot_window=2,
        atr_period=3,
    )["box"]

    assert box["start_index"] == 3
    assert box["end_index"] == 24
    assert box["start_timestamp"] == 1_700_000_180_000
    assert box["end_timestamp"] == 1_700_001_440_000
    assert box["duration_seconds"] == 21 * 60.0


def test_numeric_epoch_seconds():
    """数值时间戳（秒级 epoch）走 scale=1.0 分支，时长单位为秒。"""
    closes = [_OUT_HIGH] * 3 + [_IN] * 13 + [_OUT_LOW] * 3 + [_IN] * 6 + [_OUT_HIGH] * 5
    epoch_s = [1_700_000_000 + i * 60 for i in range(len(closes))]
    box = detect_box_range(
        klines=_klines(closes, timestamps=epoch_s),
        max_width_pct=0.15,
        pivot_window=2,
        atr_period=3,
    )["box"]

    assert box["start_timestamp"] == 1_700_000_180
    assert box["end_timestamp"] == 1_700_001_440
    assert box["duration_seconds"] == 21 * 60.0


def test_iso_naive_aware_mix():
    """ISO 时间戳 naive/aware 混用：naive 按 UTC 归一化后正常计算，不抛 TypeError。"""
    closes = [_OUT_HIGH] * 3 + [_IN] * 13 + [_OUT_LOW] * 3 + [_IN] * 6 + [_OUT_HIGH] * 5
    timestamps = [f"2026-01-01T00:{i:02d}:00Z" for i in range(len(closes))]
    timestamps[3] = "2026-01-01T00:03:00"  # naive（无时区）
    timestamps[24] = "2026-01-01T00:24:00+00:00"  # aware
    box = _run(closes, timestamps=timestamps)

    assert box["start_index"] == 3
    assert box["end_index"] == 24
    assert box["duration_seconds"] == 21 * 60


def test_malformed_iso_and_mixed_types_degrade():
    """不可解析的 ISO 字符串 / 数值与字符串混用：不抛异常、duration_seconds 为 None。"""
    closes = [_OUT_HIGH] * 3 + [_IN] * 13 + [_OUT_LOW] * 3 + [_IN] * 6 + [_OUT_HIGH] * 5
    timestamps = [f"2026-01-01T00:{i:02d}:00Z" for i in range(len(closes))]

    bad_iso = list(timestamps)
    bad_iso[24] = "not-a-date"
    box = _run(closes, timestamps=bad_iso)
    assert box["start_index"] == 3
    assert box["end_index"] == 24
    assert box["duration_seconds"] is None

    mixed = list(timestamps)
    mixed[24] = 1_700_001_440  # 数值 vs 字符串
    box = _run(closes, timestamps=mixed)
    assert box["start_index"] == 3
    assert box["end_index"] == 24
    assert box["duration_seconds"] is None

    mixed_units = [1_700_000_000 + i * 60 for i in range(len(closes))]
    mixed_units[24] = 1_700_001_440_000  # 秒 vs 毫秒
    box = _run(closes, timestamps=mixed_units)
    assert box["start_index"] == 3
    assert box["end_index"] == 24
    assert box["duration_seconds"] is None


def test_inbox_boundary_equality():
    """在箱判定含边界：close 恰为 lower×(1-tol) / upper×(1+tol) 算在箱内。"""
    stats = _box_time_stats(
        [
            {"close": 99.0},  # == 100 × 0.99 → 在箱
            {"close": 111.1},  # == 110 × 1.01 → 在箱
            {"close": 98.99},  # < 下沿 → 离箱
            {"close": 111.11},  # > 上沿 → 离箱
        ],
        lower=100.0,
        upper=110.0,
        tolerance_pct=0.01,
    )

    assert stats["start_index"] == 0
    assert stats["end_index"] == 1
    assert stats["bars_in_box"] == 2
    assert stats["duration_bars"] == 2


def test_started_inside_then_exits():
    """窗口开头在箱内，中途离箱后不再回：started_in_box=True 且 end < len-1。"""
    closes = [_IN] * 12 + [_OUT_LOW] * 18  # 0..11 在箱，12..29 离箱
    timestamps = [f"2026-01-01T00:{i:02d}:00Z" for i in range(len(closes))]
    box = _run(closes, timestamps=timestamps)

    assert box["start_index"] == 0
    assert box["end_index"] == 11
    assert box["started_in_box"] is True
    assert box["duration_bars"] == 12
    assert box["bars_in_box"] == 12
    assert box["duration_seconds"] == 11 * 60


def test_approaching_stage_in_expansion_band():
    """容差扩展带语义：close 尚未触及 lower（略低于 100）但 ≥ lower×(1-tol) 即算在箱。"""
    tol = _run([_IN] * 30)["tolerance_pct"]
    close_in_band = 100.0 * (1 - tol / 2)  # 位于 (lower×(1-tol), lower) 之间
    assert close_in_band < 100.0  # 确实还没到 lower
    closes = [_OUT_HIGH] * 3 + [close_in_band] * 15 + [_OUT_HIGH] * 12  # 3..17 在箱
    box = _run(closes)

    assert box["start_index"] == 3
    assert box["end_index"] == 17
    assert box["bars_in_box"] == 15


def test_single_bar_in_box():
    """退化输入：只有一根 close 在箱 → duration_bars == 1。"""
    closes = [_OUT_HIGH] * 5 + [_IN] + [_OUT_LOW] * 14  # index 5 唯一在箱
    box = _run(closes)

    assert box["start_index"] == 5
    assert box["end_index"] == 5
    assert box["duration_bars"] == 1
    assert box["bars_in_box"] == 1
    assert box["started_in_box"] is False


def test_missing_timestamps():
    """K 线无 timestamp 时不报错：时间戳相关字段为 None，index/bar 统计仍可用。"""
    closes = [_OUT_HIGH] * 3 + [_IN] * 13 + [_OUT_LOW] * 3 + [_IN] * 6 + [_OUT_HIGH] * 5
    box = _run(closes)

    assert box["start_index"] == 3
    assert box["end_index"] == 24
    assert box["duration_bars"] == 22
    assert box["start_timestamp"] is None
    assert box["end_timestamp"] is None
    assert box["duration_seconds"] is None


def test_close_never_enters_box():
    """close 全程在箱外（只靠影线构成箱体）：时间字段优雅降级为 None（bars_in_box=0）。"""
    closes = [_OUT_HIGH] * 15 + [_OUT_LOW] * 15
    box = _run(closes)

    assert box["upper"] > box["lower"]
    assert box["start_index"] is None
    assert box["end_index"] is None
    assert box["duration_bars"] is None
    assert box["bars_in_box"] == 0
    assert box["started_in_box"] is False
    assert box["duration_seconds"] is None


@pytest.mark.asyncio
async def test_tool_passthrough_time_fields():
    """Agent 工具入口同样返回时间起止字段。"""
    closes = [_OUT_HIGH] * 3 + [_IN] * 13 + [_OUT_LOW] * 3 + [_IN] * 6 + [_OUT_HIGH] * 5
    result = await DetectBoxRangeTool().execute(
        klines=_klines(closes),
        max_width_pct=0.15,
        pivot_window=2,
        atr_period=3,
    )

    assert result.success is True
    box = result.data["box"]
    assert box["start_index"] == 3
    assert box["end_index"] == 24
    assert box["duration_bars"] == 22
    assert box["started_in_box"] is False
