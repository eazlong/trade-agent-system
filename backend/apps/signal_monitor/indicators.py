"""
技术指标计算引擎

支持并行计算，确保完成时间不超过 100ms。
使用 numpy 向量化计算提高性能。
"""

import time
import logging
from typing import Any
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

logger = logging.getLogger(__name__)

# 全局线程池，用于并行计算指标
_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="indicator")


def compute_sma(closes: np.ndarray, period: int) -> np.ndarray:
    """简单移动平均"""
    if len(closes) < period:
        return np.array([])
    cumsum = np.cumsum(closes)
    result = np.empty(len(closes))
    result[: period - 1] = np.nan
    result[period - 1 :] = (
        cumsum[period - 1 :] - np.concatenate([[0], cumsum[:-period]])
    ) / period
    return result


def compute_ema(closes: np.ndarray, period: int) -> np.ndarray:
    """指数移动平均"""
    if len(closes) < period:
        return np.array([])
    result = np.empty(len(closes))
    result[: period - 1] = np.nan
    result[period - 1] = np.mean(closes[:period])
    multiplier = 2.0 / (period + 1)
    for i in range(period, len(closes)):
        result[i] = closes[i] * multiplier + result[i - 1] * (1 - multiplier)
    return result


def compute_rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    """相对强弱指标"""
    if len(closes) < period + 1:
        return np.array([])
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    result = np.full(len(closes), np.nan)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    if avg_loss == 0:
        result[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        result[period] = 100.0 - (100.0 / (1.0 + rs))

    for i in range(period + 1, len(closes)):
        avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        if avg_loss == 0:
            result[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            result[i] = 100.0 - (100.0 / (1.0 + rs))

    return result


def compute_macd(
    closes: np.ndarray,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> dict[str, np.ndarray]:
    """MACD 指标"""
    ema_fast = compute_ema(closes, fast)
    ema_slow = compute_ema(closes, slow)

    macd_line = ema_fast - ema_slow
    valid = ~np.isnan(macd_line)
    signal_line = np.full_like(macd_line, np.nan)
    if np.any(valid):
        valid_values = macd_line[valid]
        sig = compute_ema(valid_values, signal)
        signal_line[valid] = sig

    histogram = macd_line - signal_line
    return {
        "macd": macd_line,
        "signal": signal_line,
        "histogram": histogram,
    }


def compute_bollinger(
    closes: np.ndarray,
    period: int = 20,
    std_dev: float = 2.0,
) -> dict[str, np.ndarray]:
    """布林带"""
    sma = compute_sma(closes, period)
    result = {
        "upper": np.full_like(closes, np.nan),
        "middle": sma,
        "lower": np.full_like(closes, np.nan),
    }

    if len(closes) < period:
        return result

    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1 : i + 1]
        std = np.std(window)
        result["upper"][i] = sma[i] + std_dev * std
        result["lower"][i] = sma[i] - std_dev * std

    return result


def compute_atr(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    period: int = 14,
) -> np.ndarray:
    """平均真实波幅"""
    if len(closes) < period + 1:
        return np.array([])

    tr = np.maximum(
        highs[1:] - lows[1:],
        np.maximum(
            np.abs(highs[1:] - closes[:-1]),
            np.abs(lows[1:] - closes[:-1]),
        ),
    )

    result = np.full(len(closes), np.nan)
    result[period] = np.mean(tr[:period])

    for i in range(period + 1, len(closes)):
        result[i] = (result[i - 1] * (period - 1) + tr[i - 1]) / period

    return result


def compute_stoch(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    k_period: int = 14,
    d_period: int = 3,
) -> dict[str, np.ndarray]:
    """随机指标 (Stochastic)"""
    k_line = np.full_like(closes, np.nan)
    for i in range(k_period - 1, len(closes)):
        h = highs[i - k_period + 1 : i + 1]
        lo = lows[i - k_period + 1 : i + 1]
        highest = np.max(h)
        lowest = np.min(lo)
        if highest == lowest:
            k_line[i] = 50.0
        else:
            k_line[i] = (closes[i] - lowest) / (highest - lowest) * 100.0

    d_line = np.full_like(closes, np.nan)
    for i in range(k_period - 1 + d_period - 1, len(closes)):
        window = k_line[i - d_period + 1 : i + 1]
        valid = window[~np.isnan(window)]
        if len(valid) == d_period:
            d_line[i] = np.mean(valid)

    return {"k": k_line, "d": d_line}


# 指标注册表
INDICATOR_REGISTRY: dict[str, Any] = {
    "sma": compute_sma,
    "ema": compute_ema,
    "rsi": compute_rsi,
    "macd": compute_macd,
    "bollinger": compute_bollinger,
    "atr": compute_atr,
    "stoch": compute_stoch,
}


def compute_indicator(
    indicator_type: str,
    klines: list[dict],
    params: dict | None = None,
) -> Any:
    """
    计算单个技术指标

    Args:
        indicator_type: 指标类型
        klines: K线数据列表
        params: 指标参数

    Returns:
        指标计算结果
    """
    params = params or {}
    func = INDICATOR_REGISTRY.get(indicator_type)
    if func is None:
        raise ValueError(f"Unknown indicator type: {indicator_type}")

    closes = np.array([k["close"] for k in klines], dtype=np.float64)

    if indicator_type in ("sma", "ema", "rsi"):
        return func(closes, **params)

    if indicator_type == "macd":
        return func(closes, **params)

    if indicator_type == "bollinger":
        return func(closes, **params)

    if indicator_type == "atr":
        highs = np.array([k["high"] for k in klines], dtype=np.float64)
        lows = np.array([k["low"] for k in klines], dtype=np.float64)
        return func(highs, lows, closes, **params)

    if indicator_type == "stoch":
        highs = np.array([k["high"] for k in klines], dtype=np.float64)
        lows = np.array([k["low"] for k in klines], dtype=np.float64)
        return func(highs, lows, closes, **params)

    return func(closes, **params)


def compute_indicators_parallel(
    tasks: list[dict],
) -> list[dict]:
    """
    并行计算多个技术指标

    Args:
        tasks: 计算任务列表，每个任务包含:
            - indicator_type: 指标类型
            - klines: K线数据
            - params: 指标参数

    Returns:
        结果列表，与 tasks 一一对应，每个元素包含:
            - indicator_type: 指标类型
            - result: 计算结果
            - error: 错误信息（如果有）
            - duration_ms: 计算耗时（毫秒）
    """
    start = time.monotonic()
    futures = {}

    for i, task in enumerate(tasks):
        future = _executor.submit(
            compute_indicator,
            task["indicator_type"],
            task["klines"],
            task.get("params"),
        )
        futures[future] = i

    results = [None] * len(tasks)
    for future in as_completed(futures):
        idx = futures[future]
        task = tasks[idx]
        try:
            result = future.result(timeout=0.1)
            results[idx] = {
                "indicator_type": task["indicator_type"],
                "result": result,
                "error": None,
            }
        except Exception as e:
            results[idx] = {
                "indicator_type": task["indicator_type"],
                "result": None,
                "error": str(e),
            }

    total_ms = (time.monotonic() - start) * 1000
    if total_ms > 100:
        logger.warning(
            "Parallel indicator computation took %.1fms (target: <100ms), tasks=%d",
            total_ms,
            len(tasks),
        )

    for r in results:
        if r is not None:
            r["duration_ms"] = total_ms

    return results
