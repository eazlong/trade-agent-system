"""
策略测试运行器 — MVP 精简版

轻量级策略验证器，用于验证新创建的策略代码是否能正确加载、实例化和运行。
"""

from __future__ import annotations

import importlib.util
import logging
import random
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _generate_mock_klines(n: int = 100) -> list[dict]:
    """生成模拟 K 线数据"""
    random.seed(42)
    base_price = 50000.0
    klines = []
    for i in range(n):
        change = random.uniform(-0.02, 0.02)
        open_price = base_price * (1 + change)
        close_price = base_price * (1 + change * 0.5)
        high_price = max(open_price, close_price) * (1 + abs(random.uniform(0, 0.01)))
        low_price = min(open_price, close_price) * (1 - abs(random.uniform(0, 0.01)))
        klines.append({
            "timestamp": f"2024-01-{(i // 24) + 1:02d}T{i % 24:02d}:00:00",
            "open": round(open_price, 2), "high": round(high_price, 2),
            "low": round(low_price, 2), "close": round(close_price, 2),
            "volume": round(random.uniform(100, 1000), 2),
        })
        base_price = close_price
    return klines


class StrategyTester:
    """策略验证器：加载策略文件 → 验证语法 → 运行模拟回测"""

    def __init__(self, kline_count: int = 200, initial_capital: Decimal = Decimal("10000")):
        self.kline_count = kline_count
        self.initial_capital = initial_capital

    def test_strategy_file(self, file_path: str) -> dict[str, Any]:
        """测试单个策略文件"""
        path = Path(file_path)
        if not path.exists():
            return {"success": False, "strategy_name": None, "errors": [f"策略文件不存在: {file_path}"], "stats": None, "warnings": []}

        syntax_ok, syntax_error = self._check_syntax(path)
        if not syntax_ok:
            return {"success": False, "strategy_name": None, "errors": [f"语法错误: {syntax_error}"], "stats": None, "warnings": []}

        strategy_cls, load_error = self._load_strategy_module(path)
        if load_error:
            return {"success": False, "strategy_name": None, "errors": [f"加载失败: {load_error}"], "stats": None, "warnings": []}

        validate_errors = self._validate_strategy_class(strategy_cls)
        if validate_errors:
            return {"success": False, "strategy_name": getattr(strategy_cls, "name", None), "errors": validate_errors, "stats": None, "warnings": []}

        strategy_name = getattr(strategy_cls, "name", "unnamed")
        run_result = self._run_mock_backtest(strategy_cls)
        if run_result["errors"]:
            return {"success": False, "strategy_name": strategy_name, "errors": run_result["errors"], "stats": run_result.get("stats"), "warnings": run_result.get("warnings", [])}

        return {"success": True, "strategy_name": strategy_name, "errors": [], "stats": run_result["stats"], "warnings": run_result.get("warnings", [])}

    def _check_syntax(self, path: Path) -> tuple[bool, str | None]:
        """验证 Python 语法"""
        import py_compile
        try:
            with tempfile.NamedTemporaryFile(suffix=".pyc", delete=False) as f:
                py_compile.compile(str(path), f.name, doraise=True)
            return True, None
        except py_compile.PyCompileError as e:
            return False, str(e)
        except Exception as e:
            return False, f"语法检查失败: {e}"

    def _load_strategy_module(self, path: Path) -> tuple[type | None, str | None]:
        """动态加载策略模块"""
        try:
            from apps.strategy_engine.base import BaseStrategy
            module_name = path.stem
            spec = importlib.util.spec_from_file_location(module_name, str(path))
            if spec is None or spec.loader is None:
                return None, "无法创建模块 spec"
            module = importlib.util.module_from_spec(spec)
            for mod_name in ["apps.strategy_engine.base", "apps.strategy_engine.indicators"]:
                if mod_name not in sys.modules:
                    try:
                        __import__(mod_name)
                    except ImportError:
                        pass
            spec.loader.exec_module(module)
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if isinstance(attr, type) and attr is not BaseStrategy and issubclass(attr, BaseStrategy):
                    return attr, None
            return None, "未找到继承自 BaseStrategy 的策略类"
        except Exception as e:
            return None, f"模块加载失败: {e}"

    def _validate_strategy_class(self, strategy_cls: type) -> list[str]:
        """验证策略类的必需属性和方法"""
        errors = []
        if not hasattr(strategy_cls, "name") or not getattr(strategy_cls, "name"):
            errors.append("策略类缺少 'name' 属性或为空")
        if not hasattr(strategy_cls, "on_bar"):
            errors.append("策略类缺少 'on_bar' 方法")
        return errors

    def _run_mock_backtest(self, strategy_cls: type) -> dict[str, Any]:
        """用模拟 K 线数据运行策略"""
        from apps.strategy_engine.base import OrderSignal, StrategyContext
        errors: list[str] = []
        warnings: list[str] = []
        schema: dict = getattr(strategy_cls, "params_schema", {})
        params = {key: val.get("default") if isinstance(val, dict) else val for key, val in schema.items()}
        context = StrategyContext(symbol="BTC/USDT", timeframe="1h", mode="backtest", params=params, balance=self.initial_capital, position=Decimal("0"))
        try:
            strategy = strategy_cls(context)
        except Exception as e:
            return {"stats": None, "errors": [f"策略实例化失败: {e}"], "warnings": []}

        klines = _generate_mock_klines(self.kline_count)
        signals_count = buy_count = sell_count = 0
        position = Decimal("0")
        balance = self.initial_capital
        try:
            strategy.on_start()
        except Exception as e:
            errors.append(f"on_start 调用失败: {e}")

        for i, kline in enumerate(klines):
            history = klines[: i + 1]
            try:
                signal = strategy.on_bar(kline, history)
            except Exception as e:
                errors.append(f"on_bar 第 {i + 1} 根 K 线时出错: {e}")
                break
            if signal is None:
                continue
            if not isinstance(signal, OrderSignal):
                errors.append(f"on_bar 返回了非 OrderSignal 类型: {type(signal).__name__}")
                break
            signals_count += 1
            if signal.side == "buy":
                cost = signal.quantity * Decimal(str(kline["close"]))
                commission = cost * Decimal("0.001")
                if cost + commission <= balance:
                    balance -= cost + commission
                    position += signal.quantity
                buy_count += 1
            elif signal.side == "sell":
                sell_qty = min(signal.quantity, position)
                balance += sell_qty * Decimal(str(kline["close"])) * Decimal("0.999")
                position -= sell_qty
                sell_count += 1

        try:
            strategy.on_stop()
        except Exception as e:
            warnings.append(f"on_stop 调用失败: {e}")

        if errors:
            return {"stats": None, "errors": errors, "warnings": warnings}

        final_equity = balance + position * Decimal(str(klines[-1]["close"]))
        total_return = float(((final_equity - self.initial_capital) / self.initial_capital) * 100)
        stats = {
            "total_bars": len(klines), "signals_count": signals_count,
            "buy_count": buy_count, "sell_count": sell_count,
            "final_balance": float(balance), "final_position": float(position),
            "final_equity": float(final_equity), "total_return_pct": round(total_return, 2),
        }
        return {"stats": stats, "errors": [], "warnings": warnings}
