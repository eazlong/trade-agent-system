"""
信号条件评估器

评估技术指标是否满足触发条件。

条件格式示例：
{
    "operator": "cross_above",    // 交叉上穿
    "left": {"field": "ema_12"},  // 左操作数（当前指标值）
    "right": {"value": 50000}     // 右操作数（固定值）
}

{
    "operator": "lt",             // 小于
    "left": {"field": "rsi"},
    "right": {"value": 30}
}

{
    "operator": "cross_above",
    "left": {"field": "macd"},
    "right": {"field": "signal"}  // 另一个指标字段
}
"""

from __future__ import annotations

import operator
import logging
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# 比较运算符映射
_COMPARATORS = {
    "gt": operator.gt,
    "gte": operator.ge,
    "lt": operator.lt,
    "lte": operator.le,
    "eq": operator.eq,
    "ne": operator.ne,
}


def _resolve_operand(
    op: dict, indicator_result: Any, prev_result: Any | None = None
) -> float | None:
    """
    解析操作数的值。

    支持三种格式：
    - {"value": 50000} — 固定值
    - {"field": "rsi"} — 指标结果中的字段
    - {"field": "price"} — 当前价格（用于 price_watch 场景）
    """
    if "value" in op:
        return float(op["value"])

    field = op.get("field", "")
    # 从指标结果或价格字段中提取最新有效值
    val = _extract_latest(indicator_result, field)
    return val


def _extract_latest(result: Any, field: str) -> float | None:
    """从指标结果中提取最新的非 NaN 值"""
    if result is None:
        return None

    if isinstance(result, dict):
        # 复合指标（如 macd, bollinger）或 price_watch
        arr = result.get(field)
        if arr is None:
            return None
        # price_watch 返回的是标量 float
        if isinstance(arr, (int, float)):
            return float(arr)
        return _last_valid(arr)

    if isinstance(result, np.ndarray):
        if field:
            return None
        return _last_valid(result)

    return None


def _last_valid(arr: Any) -> float | None:
    """获取数组中最后一个非 NaN 值"""
    if arr is None:
        return None
    arr = np.asarray(arr)
    valid = arr[~np.isnan(arr)]
    if len(valid) == 0:
        return None
    return float(valid[-1])


def evaluate_condition(
    condition: dict,
    indicator_result: Any,
    prev_indicator_result: Any | None = None,
) -> bool:
    """
    评估条件是否满足。

    Args:
        condition: 条件定义
        indicator_result: 当前指标计算结果
        prev_indicator_result: 上一期指标结果（用于交叉检测）

    Returns:
        条件是否满足
    """
    cond_operator = condition.get("operator", "")
    left_op = condition.get("left", {})
    right_op = condition.get("right", {})

    # 交叉类型需要前后两期数据
    if cond_operator in ("cross_above", "cross_below"):
        return _evaluate_cross(
            cond_operator,
            left_op,
            right_op,
            indicator_result,
            prev_indicator_result,
        )

    # 简单比较
    comp = _COMPARATORS.get(cond_operator)
    if comp is None:
        logger.warning("Unknown condition operator: %s", cond_operator)
        return False

    left_val = _resolve_operand(left_op, indicator_result)
    right_val = _resolve_operand(right_op, indicator_result)

    if left_val is None or right_val is None:
        return False

    return comp(left_val, right_val)


def _evaluate_cross(
    cross_type: str,
    left_op: dict,
    right_op: dict,
    current_result: Any,
    prev_result: Any | None,
) -> bool:
    """
    评估交叉条件。

    cross_above: 左值从下方穿越右值（前一期 left < right，当前期 left > right）
    cross_below: 左值从上方穿越右值（前一期 left > right，当前期 left < right）
    """
    if prev_result is None:
        return False

    # 当前期
    curr_left = _resolve_operand(left_op, current_result)
    curr_right = _resolve_operand(right_op, current_result)

    # 前一期
    prev_left = _resolve_operand(left_op, prev_result)
    prev_right = _resolve_operand(right_op, prev_result)

    if any(v is None for v in [curr_left, curr_right, prev_left, prev_right]):
        return False

    if cross_type == "cross_above":
        return prev_left <= prev_right and curr_left > curr_right
    elif cross_type == "cross_below":
        return prev_left >= prev_right and curr_left < curr_right

    return False
