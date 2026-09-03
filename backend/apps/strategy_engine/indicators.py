"""
技术指标库 — 策略引擎专用

提供简化的指标函数接口，供策略代码直接调用。
底层复用 signal_monitor.indicators 的 numpy 实现。
"""

from __future__ import annotations

from collections import Counter

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


def _validate_key_level_params(
    klines: list[dict],
    current_price: float | None,
    atr_period: int,
    pivot_window: int,
    min_gap_bars: int,
    max_levels: int,
) -> None:
    if pivot_window < 1:
        raise ValueError("pivot_window 必须 >= 1")
    if atr_period < 1:
        raise ValueError("atr_period 必须 >= 1")
    if min_gap_bars < 1:
        raise ValueError("min_gap_bars 必须 >= 1")
    if max_levels < 1 or max_levels > 20:
        raise ValueError("max_levels 必须在 1 到 20 之间")
    if current_price is not None and current_price <= 0:
        raise ValueError("current_price 必须为正数")

    min_bars = 2 * pivot_window + atr_period + 1
    if len(klines) < min_bars:
        raise ValueError(f"K线数量不足，至少需要 {min_bars} 根")

    for idx, kline in enumerate(klines):
        for field in ("high", "low", "close"):
            try:
                value = float(kline[field])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"第 {idx} 根K线缺少有效 {field}") from exc
            if value <= 0 or not np.isfinite(value):
                raise ValueError(f"第 {idx} 根K线 {field} 必须为正数")


def _latest_tolerance_pct(klines: list[dict], atr_period: int) -> float:
    atr_values = atr(klines, atr_period)
    valid_atr = atr_values[np.isfinite(atr_values)]
    if len(valid_atr) == 0:
        raise ValueError("无法计算 ATR 容差")
    atr_pct = float(valid_atr[-1]) / float(klines[-1]["close"])
    return min(max(atr_pct * 0.5, 0.001), 0.01)


def _is_pivot(values: np.ndarray, index: int, window: int, mode: str) -> bool:
    left = values[index - window : index]
    right = values[index + 1 : index + window + 1]
    if mode == "low":
        return bool(values[index] <= left.min() and values[index] <= right.min())
    return bool(values[index] >= left.max() and values[index] >= right.max())


def _count_touches(
    klines: list[dict],
    price: float,
    tolerance_pct: float,
    min_gap_bars: int,
    source: str,
) -> tuple[int, int, str | None]:
    last_touch_index = -min_gap_bars - 1
    touches = 0
    band = price * tolerance_pct
    field = "low" if source == "low" else "high"

    for index, kline in enumerate(klines):
        if abs(float(kline[field]) - price) <= band:
            if index - last_touch_index >= min_gap_bars:
                touches += 1
            last_touch_index = index

    timestamp = klines[last_touch_index].get("timestamp") if last_touch_index >= 0 else None
    return touches, last_touch_index, timestamp


def horizontal_key_levels(
    klines: list[dict],
    current_price: float | None = None,
    atr_period: int = 14,
    pivot_window: int = 2,
    min_gap_bars: int = 3,
    max_levels: int = 5,
) -> dict:
    """计算水平关键价位。"""
    if not isinstance(klines, list):
        raise ValueError("klines 必须是 K线 dict 列表")
    current = float(current_price) if current_price is not None else None
    _validate_key_level_params(
        klines, current, atr_period, pivot_window, min_gap_bars, max_levels
    )

    highs = np.array([float(k["high"]) for k in klines], dtype=np.float64)
    lows = np.array([float(k["low"]) for k in klines], dtype=np.float64)
    tolerance_pct = _latest_tolerance_pct(klines, atr_period)

    candidates = []
    for index in range(pivot_window, len(klines) - pivot_window):
        if _is_pivot(lows, index, pivot_window, "low"):
            candidates.append({"price": float(lows[index]), "index": index, "source": "low"})
        if _is_pivot(highs, index, pivot_window, "high"):
            candidates.append({"price": float(highs[index]), "index": index, "source": "high"})

    clusters: list[list[dict]] = []
    for candidate in sorted(candidates, key=lambda item: item["price"]):
        for cluster in clusters:
            avg_price = sum(item["price"] for item in cluster) / len(cluster)
            if abs(candidate["price"] - avg_price) <= avg_price * tolerance_pct:
                cluster.append(candidate)
                break
        else:
            clusters.append([candidate])

    levels = []
    for cluster in clusters:
        price = sum(item["price"] for item in cluster) / len(cluster)
        source = Counter(item["source"] for item in cluster).most_common(1)[0][0]
        touches, last_index, timestamp = _count_touches(
            klines, price, tolerance_pct, min_gap_bars, source
        )
        if touches == 0:
            continue
        levels.append(
            {
                "price": price,
                "touches": touches,
                "tolerance_pct": tolerance_pct,
                "source": source,
                "last_touch_index": last_index,
                "last_touch_timestamp": timestamp,
                "score": touches,
            }
        )

    levels.sort(
        key=lambda level: (
            -level["touches"],
            abs(level["price"] - current) if current is not None else 0,
            -level["last_touch_index"],
        )
    )
    levels = levels[:max_levels]

    supports = []
    resistances = []
    if current is not None:
        supports = [level for level in levels if level["price"] < current]
        resistances = [level for level in levels if level["price"] > current]

    return {
        "levels": levels,
        "supports": supports,
        "resistances": resistances,
        "tolerance_pct": tolerance_pct,
    }



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


# --- 箱体判定 ----------------------------------------------------------

def _cluster_pivot_price(
    prices: list[float],
    tolerance_pct: float,
) -> list[tuple[float, int]]:
    """把多个 pivot 价格按容差带聚成一组 cluster。

    Returns:
        所有 cluster 的列表，每项为 (聚合价, pivot 数量)。空输入返回 []。

    注意：之前版本只返回最大簇；那会让 detect_box_range 误把"局部紧簇"
    当成"全窗口箱体"。现在返回全部 cluster，让调用方按 (pivot_count, touches)
    综合评分挑选。
    """
    if not prices:
        return []
    sorted_prices = sorted(prices)
    clusters: list[list[float]] = []
    for price in sorted_prices:
        for cluster in clusters:
            avg = sum(cluster) / len(cluster)
            if abs(price - avg) <= avg * tolerance_pct:
                cluster.append(price)
                break
        else:
            clusters.append([price])
    return [
        (sum(cluster) / len(cluster), len(cluster)) for cluster in clusters
    ]


def _validate_box_range_params(
    klines: list[dict],
    max_width_abs: float | None,
    max_width_pct: float | None,
    pivot_window: int,
    min_gap_bars: int,
    min_pivots: int,
    min_touches: int,
    atr_period: int,
    upper_max_discard_pct: float,
    lower_max_discard_pct: float,
    min_width_abs: float | None,
    min_width_pct: float | None,
) -> None:
    if max_width_abs is None and max_width_pct is None:
        raise ValueError("必须传入 max_width_abs 或 max_width_pct 之一")
    if max_width_abs is not None and max_width_pct is not None:
        raise ValueError("max_width_abs 和 max_width_pct 只能二选一")
    if max_width_abs is not None and max_width_abs <= 0:
        raise ValueError("max_width_abs 必须为正数")
    if max_width_pct is not None and max_width_pct <= 0:
        raise ValueError("max_width_pct 必须为正数")
    if pivot_window < 1:
        raise ValueError("pivot_window 必须 >= 1")
    if min_gap_bars < 1:
        raise ValueError("min_gap_bars 必须 >= 1")
    if min_pivots < 1:
        raise ValueError("min_pivots 必须 >= 1")
    if min_touches < 1:
        raise ValueError("min_touches 必须 >= 1")
    if atr_period < 1:
        raise ValueError("atr_period 必须 >= 1")
    if not 0 <= upper_max_discard_pct < 1:
        raise ValueError("upper_max_discard_pct 必须在 [0, 1) 之间")
    if not 0 <= lower_max_discard_pct < 1:
        raise ValueError("lower_max_discard_pct 必须在 [0, 1) 之间")
    if min_width_abs is not None and min_width_abs < 0:
        raise ValueError("min_width_abs 必须 >= 0")
    if min_width_pct is not None and min_width_pct < 0:
        raise ValueError("min_width_pct 必须 >= 0")

    min_bars = 2 * pivot_window + 1
    if len(klines) < min_bars:
        raise ValueError(f"K线数量不足，至少需要 {min_bars} 根")

    for idx, kline in enumerate(klines):
        for field in ("high", "low", "close"):
            try:
                value = float(kline[field])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"第 {idx} 根K线缺少有效 {field}") from exc
            if value <= 0 or not np.isfinite(value):
                raise ValueError(f"第 {idx} 根K线 {field} 必须为正数")


def detect_box_range(
    klines: list[dict],
    max_width_abs: float | None = None,
    max_width_pct: float | None = None,
    pivot_window: int = 2,
    min_gap_bars: int = 3,
    min_pivots: int = 2,
    min_touches: int = 2,
    atr_period: int = 14,
    upper_max_discard_pct: float = 0.15,
    lower_max_discard_pct: float = 0.15,
    min_width_abs: float | None = None,
    min_width_pct: float | None = None,
) -> dict:
    """判断 K 线窗口是否处于箱体震荡，并返回上下边界。

    两次验证：上下边界必须同时满足
        pivot_count >= min_pivots  AND  touches >= min_touches

    窗口粗筛（防止"局部紧簇冒充箱体"）：
        选出的 upper 必须 ≥ window_high × (1 - upper_max_discard_pct)
        选出的 lower 必须 ≤ window_low  × (1 + lower_max_discard_pct)
        即"丢掉"窗口极值的一部分。默认 0.15 意味着上沿至少够到窗口高点 85%，
        下沿最多只到窗口低点 115%。

    评分挑选：在通过 pivot_count + touches + 粗筛的所有 (upper, lower) 组合中，
    选 (pivot_count × touches) 乘积最大的；若都通不过校验，依次给出失败原因。
    """
    if not isinstance(klines, list):
        raise ValueError("klines 必须是 K线 dict 列表")
    _validate_box_range_params(
        klines, max_width_abs, max_width_pct,
        pivot_window, min_gap_bars, min_pivots, min_touches, atr_period,
        upper_max_discard_pct, lower_max_discard_pct,
        min_width_abs, min_width_pct,
    )

    abs_threshold = float(max_width_abs) if max_width_abs is not None else None
    pct_threshold = float(max_width_pct) if max_width_pct is not None else None
    min_abs_floor = float(min_width_abs) if min_width_abs is not None else None
    min_pct_floor = float(min_width_pct) if min_width_pct is not None else None

    highs = np.array([float(k["high"]) for k in klines], dtype=np.float64)
    lows = np.array([float(k["low"]) for k in klines], dtype=np.float64)
    tolerance_pct = _latest_tolerance_pct(klines, atr_period)

    window_high = float(highs.max())
    window_low = float(lows.min())
    upper_floor = window_high * (1 - upper_max_discard_pct)
    lower_ceiling = window_low * (1 + lower_max_discard_pct)

    high_pivots: list[float] = []
    low_pivots: list[float] = []
    for index in range(pivot_window, len(klines) - pivot_window):
        if _is_pivot(highs, index, pivot_window, "high"):
            high_pivots.append(float(highs[index]))
        if _is_pivot(lows, index, pivot_window, "low"):
            low_pivots.append(float(lows[index]))

    upper_clusters = _cluster_pivot_price(high_pivots, tolerance_pct)
    lower_clusters = _cluster_pivot_price(low_pivots, tolerance_pct)

    params = {
        "max_width_abs": abs_threshold,
        "max_width_pct": pct_threshold,
        "pivot_window": pivot_window,
        "min_pivots": min_pivots,
        "min_touches": min_touches,
        "tolerance_pct": tolerance_pct,
        "upper_max_discard_pct": upper_max_discard_pct,
        "lower_max_discard_pct": lower_max_discard_pct,
        "min_width_abs": min_abs_floor,
        "min_width_pct": min_pct_floor,
    }

    if not upper_clusters or not lower_clusters:
        return {
            "is_ranging": False,
            "box": None,
            "reason": "未找到任何局部高/低 pivot",
            "params": params,
        }

    def _passes_coarse(price: float, is_upper: bool) -> bool:
        if is_upper:
            return price >= upper_floor
        return price <= lower_ceiling

    # 先在每个 cluster 上算 touches，挑出"够格"的 cluster
    upper_candidates: list[tuple[float, int, int]] = []  # (mean, pivot_count, touches)
    for mean, count in upper_clusters:
        if not _passes_coarse(mean, is_upper=True):
            continue
        if count < min_pivots:
            continue
        touches, _, _ = _count_touches(
            klines, mean, tolerance_pct, min_gap_bars, "high"
        )
        if touches < min_touches:
            continue
        upper_candidates.append((mean, count, touches))

    lower_candidates: list[tuple[float, int, int]] = []
    for mean, count in lower_clusters:
        if not _passes_coarse(mean, is_upper=False):
            continue
        if count < min_pivots:
            continue
        touches, _, _ = _count_touches(
            klines, mean, tolerance_pct, min_gap_bars, "low"
        )
        if touches < min_touches:
            continue
        lower_candidates.append((mean, count, touches))

    def _fail(reason: str) -> dict:
        return {"is_ranging": False, "box": None, "reason": reason, "params": params}

    if not upper_candidates:
        return _fail(
            f"上沿候选 cluster 不满足粗筛/pivot/触碰条件"
            f"（window_high={window_high:.2f}, upper_floor={upper_floor:.2f}）"
        )
    if not lower_candidates:
        return _fail(
            f"下沿候选 cluster 不满足粗筛/pivot/触碰条件"
            f"（window_low={window_low:.2f}, lower_ceiling={lower_ceiling:.2f}）"
        )

    # 在 (upper, lower) 组合中选 (pivot_count × touches) 乘积最大
    best: tuple[float, float, int, int, int, int] | None = None
    for u_mean, u_count, u_touches in upper_candidates:
        for l_mean, l_count, l_touches in lower_candidates:
            if u_mean <= l_mean:
                continue
            width = u_mean - l_mean
            mid = (u_mean + l_mean) / 2
            width_pct = width / mid if mid > 0 else 0.0
            if abs_threshold is not None and width > abs_threshold:
                continue
            if pct_threshold is not None and width_pct > pct_threshold:
                continue
            if min_abs_floor is not None and width < min_abs_floor:
                continue
            if min_pct_floor is not None and width_pct < min_pct_floor:
                continue
            score = u_count * u_touches * l_count * l_touches
            if best is None or score > best[0]:
                best = (score, u_mean, u_count, u_touches, l_mean, l_count)
                # unpack-friendly
    if best is None:
        # 给出更精确的原因：先看 width，再看 floor
        sample_w = upper_candidates[0][0] - lower_candidates[0][0]
        if min_abs_floor is not None and sample_w < min_abs_floor:
            return _fail(f"箱体宽度低于下限（{sample_w:.2f} < {min_abs_floor}）")
        if min_pct_floor is not None:
            mid = (upper_candidates[0][0] + lower_candidates[0][0]) / 2
            wp = sample_w / mid if mid > 0 else 0.0
            if wp < min_pct_floor:
                return _fail(f"箱体相对宽度低于下限（{wp:.4%} < {min_pct_floor:.4%}）")
        if abs_threshold is not None:
            return _fail(f"震荡区间绝对宽度超限（{sample_w:.2f} > {abs_threshold}）")
        if pct_threshold is not None:
            mid = (upper_candidates[0][0] + lower_candidates[0][0]) / 2
            wp = sample_w / mid if mid > 0 else 0.0
            return _fail(f"震荡区间相对宽度超限（{wp:.4%} > {pct_threshold:.4%}）")
        return _fail("未找到满足宽度约束的 (upper, lower) 组合")

    _score, upper, upper_pivot_count, upper_touches, lower, lower_pivot_count = best
    lower_touches = next(
        touches
        for mean, count, touches in lower_candidates
        if mean == lower and count == lower_pivot_count
    )
    width = upper - lower
    mid = (upper + lower) / 2
    width_pct = width / mid if mid > 0 else 0.0

    return {
        "is_ranging": True,
        "box": {
            "upper": upper,
            "lower": lower,
            "mid": mid,
            "width": width,
            "width_pct": width_pct,
            "upper_pivot_count": upper_pivot_count,
            "lower_pivot_count": lower_pivot_count,
            "upper_touches": upper_touches,
            "lower_touches": lower_touches,
            "pivot_window": pivot_window,
            "tolerance_pct": tolerance_pct,
        },
        "reason": "",
        "params": params,
    }
