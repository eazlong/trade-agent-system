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


def _extract_closes(history: list[dict]) -> np.ndarray:
    """从 K 线历史中提取收盘价数组"""
    return np.array([k["close"] for k in history], dtype=np.float64)


def _extract_hl(history: list[dict]):
    """从 K 线历史中提取 high/low 数组"""
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


def atr(history: list[dict], period: int = 14) -> np.ndarray:
    """平均真实波幅"""
    highs, lows = _extract_hl(history)
    return _compute_atr(highs, lows, _extract_closes(history), period)


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
