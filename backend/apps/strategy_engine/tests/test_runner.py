"""Tests for StrategyTester."""

from __future__ import annotations

import tempfile
from pathlib import Path

from django.test import TestCase

from apps.strategy_engine.test_runner import StrategyTester


class TestStrategyTester(TestCase):
    """测试策略验证器"""

    def _write_strategy_file(self, content: str) -> str:
        """写入临时策略文件并返回路径"""
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        )
        tmp.write(content)
        tmp.close()
        return tmp.name

    def test_valid_strategy(self):
        """测试有效的策略文件"""
        content = """
from decimal import Decimal
from apps.strategy_engine.base import BaseStrategy, StrategyContext

class TestStrategy(BaseStrategy):
    name = "test_valid"
    description = "A valid test strategy"
    params_schema = {"quantity": {"type": "number", "default": 0.01}}

    def __init__(self, context: StrategyContext):
        super().__init__(context)
        self.quantity = Decimal(str(context.params.get("quantity", 0.01)))

    def on_bar(self, kline: dict, history: list[dict]):
        if len(history) < 20:
            return None
        if self.ctx.position == 0:
            return self.ctx.buy(quantity=self.quantity, signal_name="entry")
        return None
"""
        path = self._write_strategy_file(content)
        try:
            tester = StrategyTester(kline_count=50)
            result = tester.test_strategy_file(path)
            self.assertTrue(result["success"])
            self.assertEqual(result["strategy_name"], "test_valid")
            self.assertEqual(result["errors"], [])
            self.assertIsNotNone(result["stats"])
            self.assertGreater(result["stats"]["total_bars"], 0)
        finally:
            Path(path).unlink()

    def test_syntax_error(self):
        """测试语法错误"""
        content = """
def broken(
    # missing closing paren
"""
        path = self._write_strategy_file(content)
        try:
            tester = StrategyTester()
            result = tester.test_strategy_file(path)
            self.assertFalse(result["success"])
            self.assertTrue(any("语法" in e for e in result["errors"]))
        finally:
            Path(path).unlink()

    def test_no_base_strategy_subclass(self):
        """测试没有继承 BaseStrategy 的类"""
        content = """
class NotAStrategy:
    name = "not_a_strategy"

    def on_bar(self, kline, history):
        return None
"""
        path = self._write_strategy_file(content)
        try:
            tester = StrategyTester()
            result = tester.test_strategy_file(path)
            self.assertFalse(result["success"])
            self.assertTrue(any("BaseStrategy" in e for e in result["errors"]))
        finally:
            Path(path).unlink()

    def test_missing_name(self):
        """测试 name 属性为空字符串"""
        content = """
from apps.strategy_engine.base import BaseStrategy, StrategyContext

class NamelessStrategy(BaseStrategy):
    name = ""

    def on_bar(self, kline, history):
        return None
"""
        path = self._write_strategy_file(content)
        try:
            tester = StrategyTester()
            result = tester.test_strategy_file(path)
            self.assertFalse(result["success"])
            self.assertTrue(any("name" in e.lower() for e in result["errors"]))
        finally:
            Path(path).unlink()

    def test_runtime_error_in_on_bar(self):
        """测试 on_bar 中抛出运行时错误"""
        content = """
from decimal import Decimal
from apps.strategy_engine.base import BaseStrategy, StrategyContext

class BrokenStrategy(BaseStrategy):
    name = "broken"

    def on_bar(self, kline, history):
        raise ValueError("intentional error")
"""
        path = self._write_strategy_file(content)
        try:
            tester = StrategyTester()
            result = tester.test_strategy_file(path)
            self.assertFalse(result["success"])
            self.assertTrue(any("error" in e.lower() for e in result["errors"]))
        finally:
            Path(path).unlink()

    def test_file_not_exists(self):
        """测试文件不存在"""
        tester = StrategyTester()
        result = tester.test_strategy_file("/nonexistent/path/strategy.py")
        self.assertFalse(result["success"])
        self.assertTrue(any("不存在" in e for e in result["errors"]))

    def test_invalid_return_type(self):
        """测试 on_bar 返回非 OrderSignal 类型"""
        content = """
from apps.strategy_engine.base import BaseStrategy, StrategyContext

class BadReturnStrategy(BaseStrategy):
    name = "bad_return"

    def on_bar(self, kline, history):
        return "not a signal"
"""
        path = self._write_strategy_file(content)
        try:
            tester = StrategyTester(kline_count=50)
            result = tester.test_strategy_file(path)
            self.assertFalse(result["success"])
            self.assertTrue(
                any("OrderSignal" in e or "类型" in e for e in result["errors"])
            )
        finally:
            Path(path).unlink()

    def test_decimal_float_type_warning_static(self):
        """测试静态扫描：检测到 Decimal 与 float 字面量直接运算"""
        content = """
from decimal import Decimal
from apps.strategy_engine.base import BaseStrategy, StrategyContext

class DecimalFloatStrategy(BaseStrategy):
    name = "decimal_float"

    def on_bar(self, kline, history):
        price = kline["close"] * 0.95  # Decimal × float — should be flagged
        return self.ctx.buy(quantity=Decimal("0.01"), signal_name="entry")
"""
        path = self._write_strategy_file(content)
        try:
            tester = StrategyTester()
            result = tester.test_strategy_file(path)
            # 静态扫描应产出 warning
            self.assertTrue(
                any("Decimal" in w and "float" in w for w in result["warnings"]),
                f"未检测到 Decimal × float 误用，warnings: {result['warnings']}",
            )
        finally:
            Path(path).unlink()

    def test_decimal_float_type_warning_runtime(self):
        """测试运行时：on_bar 中 Decimal 与 float 混合运算抛出 TypeError 时错误信息被增强"""
        content = """
from decimal import Decimal
from apps.strategy_engine.base import BaseStrategy, StrategyContext

class DecimalTypeErrorStrategy(BaseStrategy):
    name = "decimal_type_error"
    params_schema = {"quantity": {"type": "number", "default": 0.01}}

    def __init__(self, context: StrategyContext):
        super().__init__(context)
        self.quantity = Decimal(str(context.params.get("quantity", 0.01)))

    def on_bar(self, kline, history):
        if len(history) < 10:
            return None
        # Decimal × float — 在 kline 值为 Decimal 时触发 TypeError
        price = kline["close"] * 0.5  # type: ignore
        return self.ctx.buy(quantity=self.quantity, signal_name="entry")
"""
        path = self._write_strategy_file(content)
        try:
            tester = StrategyTester(kline_count=20)
            result = tester.test_strategy_file(path)
            self.assertFalse(result["success"])
            # 运行时错误信息应包含 Decimal 混用提示
            self.assertTrue(
                any(
                    "Decimal" in e and ("float" in e.lower() or "TypeError" in e or "类型" in e)
                    for e in result["errors"]
                ),
                f"错误信息未包含 Decimal 混用提示: {result['errors']}",
            )
        finally:
            Path(path).unlink()
