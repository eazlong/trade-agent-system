"""
信号条件评估测试

验证各种触发条件的正确性。
"""

import unittest

import numpy as np

from apps.signal_monitor.conditions import evaluate_condition


class TestComparisonConditions(unittest.TestCase):
    """简单比较条件测试"""

    def test_gt_true(self):
        condition = {
            "operator": "gt",
            "left": {"value": 100},
            "right": {"value": 50},
        }
        self.assertTrue(evaluate_condition(condition, None))

    def test_gt_false(self):
        condition = {
            "operator": "gt",
            "left": {"value": 50},
            "right": {"value": 100},
        }
        self.assertFalse(evaluate_condition(condition, None))

    def test_lt_true(self):
        condition = {
            "operator": "lt",
            "left": {"value": 30},
            "right": {"value": 50},
        }
        self.assertTrue(evaluate_condition(condition, None))

    def test_lte_true(self):
        condition = {
            "operator": "lte",
            "left": {"value": 50},
            "right": {"value": 50},
        }
        self.assertTrue(evaluate_condition(condition, None))

    def test_gte_true(self):
        condition = {
            "operator": "gte",
            "left": {"value": 50},
            "right": {"value": 50},
        }
        self.assertTrue(evaluate_condition(condition, None))

    def test_eq_true(self):
        condition = {
            "operator": "eq",
            "left": {"value": 42},
            "right": {"value": 42},
        }
        self.assertTrue(evaluate_condition(condition, None))

    def test_ne_true(self):
        condition = {
            "operator": "ne",
            "left": {"value": 42},
            "right": {"value": 43},
        }
        self.assertTrue(evaluate_condition(condition, None))

    def test_field_based_comparison(self):
        """从指标结果中提取字段进行比较"""
        indicator_result = {"rsi": np.array([50.0, 60.0, 25.0])}
        condition = {
            "operator": "lt",
            "left": {"field": "rsi"},
            "right": {"value": 30},
        }
        self.assertTrue(evaluate_condition(condition, indicator_result))

    def test_field_vs_field_comparison(self):
        """两个字段之间的比较"""
        indicator_result = {
            "ema_12": np.array([100.0, 105.0, 110.0]),
            "ema_26": np.array([100.0, 103.0, 108.0]),
        }
        condition = {
            "operator": "gt",
            "left": {"field": "ema_12"},
            "right": {"field": "ema_26"},
        }
        self.assertTrue(evaluate_condition(condition, indicator_result))

    def test_none_result_returns_false(self):
        condition = {
            "operator": "gt",
            "left": {"field": "rsi"},
            "right": {"value": 30},
        }
        self.assertFalse(evaluate_condition(condition, None))


class TestCrossConditions(unittest.TestCase):
    """交叉条件测试"""

    def test_cross_above_true(self):
        """EMA 从下方穿越固定值"""
        prev_result = {"ema_12": np.array([100.0, 95.0])}
        current_result = {"ema_12": np.array([100.0, 95.0, 105.0])}
        condition = {
            "operator": "cross_above",
            "left": {"field": "ema_12"},
            "right": {"value": 100},
        }
        self.assertTrue(evaluate_condition(condition, current_result, prev_result))

    def test_cross_above_false_no_cross(self):
        """没有发生穿越"""
        prev_result = {"ema_12": np.array([100.0, 105.0])}
        current_result = {"ema_12": np.array([100.0, 105.0, 110.0])}
        condition = {
            "operator": "cross_above",
            "left": {"field": "ema_12"},
            "right": {"value": 100},
        }
        self.assertFalse(evaluate_condition(condition, current_result, prev_result))

    def test_cross_below_true(self):
        """RSI 从上方穿越固定值"""
        prev_result = {"rsi": np.array([50.0, 75.0])}
        current_result = {"rsi": np.array([50.0, 75.0, 25.0])}
        condition = {
            "operator": "cross_below",
            "left": {"field": "rsi"},
            "right": {"value": 30},
        }
        self.assertTrue(evaluate_condition(condition, current_result, prev_result))

    def test_cross_requires_prev_result(self):
        """没有前一期数据时交叉条件不触发"""
        current_result = {"ema_12": np.array([100.0, 105.0])}
        condition = {
            "operator": "cross_above",
            "left": {"field": "ema_12"},
            "right": {"value": 100},
        }
        self.assertFalse(evaluate_condition(condition, current_result, None))

    def test_cross_above_field_vs_field(self):
        """MACD 穿越信号线"""
        prev_result = {
            "macd": np.array([1.0, -1.0]),
            "signal": np.array([1.0, 0.5]),
        }
        current_result = {
            "macd": np.array([1.0, -1.0, 1.0]),
            "signal": np.array([1.0, 0.5, 0.3]),
        }
        condition = {
            "operator": "cross_above",
            "left": {"field": "macd"},
            "right": {"field": "signal"},
        }
        self.assertTrue(evaluate_condition(condition, current_result, prev_result))

    def test_unknown_operator_returns_false(self):
        condition = {
            "operator": "invalid_op",
            "left": {"value": 1},
            "right": {"value": 2},
        }
        self.assertFalse(evaluate_condition(condition, None))


if __name__ == "__main__":
    unittest.main()
