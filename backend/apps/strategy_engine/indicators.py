"""
技术指标库 — 策略引擎专用

提供简化的指标函数接口，供策略代码直接调用。
底层复用 signal_monitor.indicators 的 numpy 实现。
"""

from __future__ import annotations


import numpy as np

from apps.signal_monitor.indicators import (
    compute_sma as _compute_sma,
    compute_ema as _compute_ema,
    compute_rsi as _compute_rsi,
    compute_macd as _compute_macd,
    compute_bollinger as _compute_bollinger,
    compute_atr as _compute_atr,
    compute_stoch as _compute_stoch,
)


def _extract_closes(history: list[dict] | np.ndarray | list[float]) -> np.ndarray:
    """从 K 线历史中提取收盘价数组，兼容多种输入格式"""
    if isinstance(history, np.ndarray):
        return history.astype(np.float64)
    if isinstance(history, list) and len(history) > 0:
        # 如果已经是数字列表（如 [42345.67, 42400.12, ...]），直接转数组
        if isinstance(history[0], (int, float)):
            return np.array(history, dtype=np.float64)
    return np.array([k["close"] for k in history], dtype=np.float64)


def _extract_hl(history: list[dict]):
    """从 K 线历史中提取 high/low 数组"""
    if isinstance(history, np.ndarray):
        raise TypeError(
            "此指标函数需要 K 线 dict 列表（含 high/low 字段），"
            "不能直接传 np.ndarray。如需传入收盘价数组，请使用对应独立函数。"
        )
    highs = np.array([k["high"] for k in history], dtype=np.float64)
    lows = np.array([k["low"] for k in history], dtype=np.float64)
    return highs, lows


def sma(history: list[dict], period: int = 20) -> np.ndarray:
    """简单移动平均

    Args:
        history: K 线数据列表 [{close, ...}, ...]
        period: 周期

    Returns:
        numpy 数组，长度与 history 相同
    """
    return _compute_sma(_extract_closes(history), period)


def ema(history: list[dict], period: int = 12) -> np.ndarray:
    """指数移动平均"""
    return _compute_ema(_extract_closes(history), period)


def rsi(history: list[dict], period: int = 14) -> np.ndarray:
    """相对强弱指标"""
    return _compute_rsi(_extract_closes(history), period)


def macd(
    history: list[dict],
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> dict[str, np.ndarray]:
    """MACD 指标

    Returns:
        {"macd": ..., "signal": ..., "histogram": ...}
    """
    return _compute_macd(_extract_closes(history), fast, slow, signal_period)


def bollinger(
    history: list[dict],
    period: int = 20,
    std_dev: float = 2.0,
) -> dict[str, np.ndarray]:
    """布林带

    Returns:
        {"upper": ..., "middle": ..., "lower": ...}
    """
    return _compute_bollinger(_extract_closes(history), period, std_dev)


def atr(
    highs_or_history: list[dict] | np.ndarray,
    period_or_lows: int | np.ndarray = 14,
    closes: np.ndarray | None = None,
    period: int = 14,
) -> np.ndarray:
    """平均真实波幅

    支持两种调用方式：
    1. atr(history, period) — history 为 K 线 dict 列表
    2. atr(highs, lows, closes, period=N) — 直接传入 high/low/close 数组
    """
    # 判断是 K 线 dict 列表还是数组形式
    # 如果是 np.ndarray，或 list 且元素为数字（非 dict），则为数组/三数组形式
    is_array_form = isinstance(highs_or_history, np.ndarray) or (
        isinstance(highs_or_history, list)
        and len(highs_or_history) > 0
        and isinstance(highs_or_history[0], (int, float))
    )

    if is_array_form:
        if closes is not None:
            # 三数组形式：atr(highs, lows, closes, period=N)
            # period 始终来自 keyword 参数
            h = (
                highs_or_history
                if isinstance(highs_or_history, np.ndarray)
                else np.array(highs_or_history, dtype=np.float64)
            )
            lo = (
                period_or_lows
                if isinstance(period_or_lows, np.ndarray)
                else np.array(period_or_lows, dtype=np.float64)
            )
            c = (
                closes
                if isinstance(closes, np.ndarray)
                else np.array(closes, dtype=np.float64)
            )
            return _compute_atr(h, lo, c, period)
        # 两数组形式：atr(highs_array, lows_array)
        p = period_or_lows if isinstance(period_or_lows, int) else period
        empty = np.array([])
        h = (
            highs_or_history
            if isinstance(highs_or_history, np.ndarray)
            else np.array(highs_or_history, dtype=np.float64)
        )
        lo = period_or_lows if isinstance(period_or_lows, np.ndarray) else empty
        return _compute_atr(h, lo, empty, p)

    # 默认 atr(history, period)
    if closes is not None:
        raise TypeError("当第一个参数为 K 线 dict 列表时，不能传入 closes 作为位置参数")
    h, lo = _extract_hl(highs_or_history)
    p = period_or_lows if isinstance(period_or_lows, int) else period
    return _compute_atr(h, lo, _extract_closes(highs_or_history), p)


def stoch(
    history: list[dict],
    k_period: int = 14,
    d_period: int = 3,
) -> dict[str, np.ndarray]:
    """随机指标

    Returns:
        {"k": ..., "d": ...}
    """
    highs, lows = _extract_hl(history)
    return _compute_stoch(highs, lows, _extract_closes(history), k_period, d_period)
