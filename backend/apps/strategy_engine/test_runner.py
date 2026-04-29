"""
策略测试运行器

轻量级策略验证器，用于验证新创建的策略代码是否能正确加载、实例化和运行。
不需要 Django/Celery 环境，可直接在 Agent 工具调用中使用。
"""

from __future__ import annotations

import importlib.util
import logging
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _generate_mock_klines(n: int = 100) -> list[dict]:
    """生成模拟 K 线数据，带有真实的价格波动

    生成的数据包含完整的 open/high/low/close/volume/timestamp 字段，
    供策略代码调用 indicators 模块中的函数（如 atr、stoch 等）。
    """
    import random

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
            "open": round(open_price, 2),
            "high": round(high_price, 2),
            "low": round(low_price, 2),
            "close": round(close_price, 2),
            "volume": round(random.uniform(100, 1000), 2),
        })
        base_price = close_price
    return klines


class StrategyTester:
    """策略验证器：加载策略文件 → 验证语法 → 注册 → 用模拟数据运行 on_bar"""

    def __init__(self, kline_count: int = 200, initial_capital: Decimal = Decimal("10000")):
        self.kline_count = kline_count
        self.initial_capital = initial_capital

    def test_strategy_file(self, file_path: str) -> dict[str, Any]:
        """
        测试单个策略文件。

        Args:
            file_path: 策略文件路径（绝对路径或相对于策略目录的路径）

        Returns:
            {
                "success": bool,
                "strategy_name": str | None,
                "errors": list[str],
                "stats": dict | None,
                "warnings": list[str],
            }
        """
        errors: list[str] = []
        warnings: list[str] = []

        # 1. 检查文件是否存在
        path = Path(file_path)
        if not path.exists():
            return {
                "success": False,
                "strategy_name": None,
                "errors": [f"策略文件不存在: {file_path}"],
                "stats": None,
                "warnings": [],
            }

        # 2. 语法检查
        syntax_ok, syntax_error = self._check_syntax(path)
        if not syntax_ok:
            return {
                "success": False,
                "strategy_name": None,
                "errors": [f"语法错误: {syntax_error}"],
                "stats": None,
                "warnings": [],
            }

        # 2.5. 静态分析：检查指标 API 调用是否使用了正确的签名
        api_errors, api_warnings = self._validate_indicator_api(path)
        if api_errors:
            return {
                "success": False,
                "strategy_name": None,
                "errors": api_errors,
                "stats": None,
                "warnings": api_warnings,
            }

        # 3. 动态加载模块
        strategy_cls, load_error = self._load_strategy_module(path)
        if load_error:
            return {
                "success": False,
                "strategy_name": None,
                "errors": [f"加载失败: {load_error}"],
                "stats": None,
                "warnings": [],
            }

        # 4. 验证策略类
        validate_errors = self._validate_strategy_class(strategy_cls)
        if validate_errors:
            return {
                "success": False,
                "strategy_name": getattr(strategy_cls, "name", None),
                "errors": validate_errors,
                "stats": None,
                "warnings": [],
            }

        strategy_name = getattr(strategy_cls, "name", "unnamed")

        # 5. 实例化并模拟运行
        run_result = self._run_mock_backtest(strategy_cls)
        if run_result["errors"]:
            errors = run_result["errors"]
            warnings = run_result.get("warnings", [])
            return {
                "success": False,
                "strategy_name": strategy_name,
                "errors": errors,
                "stats": run_result.get("stats"),
                "warnings": warnings,
            }

        return {
            "success": True,
            "strategy_name": strategy_name,
            "errors": [],
            "stats": run_result["stats"],
            "warnings": run_result.get("warnings", []),
        }

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

    def _validate_indicator_api(self, path: Path) -> tuple[list[str], list[str]]:
        """
        静态分析策略代码，检查指标 API 调用是否使用了正确的签名。

        Returns:
            (errors, warnings) 元组
        """
        errors: list[str] = []
        warnings: list[str] = []

        try:
            source = path.read_text(encoding="utf-8")
        except Exception as e:
            return [f"读取策略文件失败: {e}"], []

        # ── 错误模式：会导致运行时 TypeError 的调用 ──

        # 1. atr() 错误调用：atr(highs, lows, closes, period=N)
        #    正确用法：atr(history, period=14) 或 atr(highs, lows, closes, period=N)（已修复支持）
        #    但仍然提醒使用推荐的方式
        import re

        # 检测手动提取 highs/lows/closes 再传给 atr 的模式
        # 例如：atr(np.array(...), np.array(...), np.array(...), period=14)
        # 或：atr(highs, lows, closes, period=self.atr_period)
        atr_array_pattern = re.compile(
            r'\batr\s*\(\s*[a-zA-Z_]\w*\s*,\s*[a-zA-Z_]\w*\s*,\s*[a-zA-Z_]\w*\s*[,)]'
        )
        if atr_array_pattern.search(source):
            warnings.append(
                "指标 API 用法提醒: atr() 检测到传入多个位置参数。"
                "推荐用法: atr(history, period=14)，其中 history 为 K 线 dict 列表。"
                "当前也支持 atr(highs, lows, closes, period=N)，但推荐优先使用 history 方式。"
            )

        # 2. 检测其他指标的错误调用：直接传 np.ndarray 而非 history
        #    例如：rsi(closes, period=14) — 正确应为 rsi(history, period=14)
        #    但如果策略从 history 中提取了 closes 数组再传，这是常见模式
        #    我们只检测明显错误的模式

        # 3. 检测错误的 import 路径
        wrong_imports = [
            "from apps.signal_monitor.indicators import",
            "from signal_monitor.indicators import",
        ]
        for wrong_imp in wrong_imports:
            if wrong_imp in source:
                errors.append(
                    f"错误的 import 路径: 检测到 '{wrong_imp}'。"
                    f"策略代码应使用 'from apps.strategy_engine.indicators import'。"
                )

        # 4. 检测 on_bar 中使用了 ctx.history() 调用（应避免）
        if "ctx.history(" in source or "self.ctx.history(" in source:
            errors.append(
                "API 误用: on_bar 中不应调用 ctx.history()。"
                "历史数据已通过 history 参数直接传入 on_bar。"
            )

        # 5. 检测 on_bar 签名是否正确（简单文本匹配）
        #    允许的类型注解变体
        on_bar_patterns = [
            r"def\s+on_bar\s*\(\s*self\s*,\s*kline\s*:\s*dict\s*,\s*history\s*:\s*list\s*\[\s*dict\s*\]\s*\)",
            r"def\s+on_bar\s*\(\s*self\s*,\s*kline\s*,\s*history\s*\)",
            r"def\s+on_bar\s*\(\s*self\s*,\s*kline\s*:\s*dict\s*,\s*history\s*\)",
        ]
        has_valid_signature = any(
            re.search(p, source) for p in on_bar_patterns
        )
        if not has_valid_signature:
            # 检查是否有 on_bar 定义但签名不匹配
            if re.search(r"def\s+on_bar\s*\(", source):
                errors.append(
                    "on_bar 签名不正确: 必须为 "
                    "def on_bar(self, kline: dict, history: list[dict]) -> OrderSignal | None"
                )

        return errors, warnings

    def _load_strategy_module(self, path: Path) -> tuple[type | None, str | None]:
        """
        动态加载策略模块，返回策略类。

        使用 importlib.util 从文件路径直接加载，不依赖 sys.path。
        """
        try:
            from apps.strategy_engine.base import BaseStrategy

            module_name = path.stem
            spec = importlib.util.spec_from_file_location(module_name, str(path))
            if spec is None or spec.loader is None:
                return None, "无法创建模块 spec"

            module = importlib.util.module_from_spec(spec)
            # 将策略引擎的模块预先注入 sys.modules，确保 import 能找到
            if "apps.strategy_engine.base" not in sys.modules:
                try:
                    import apps.strategy_engine.base  # noqa: F401
                except ImportError:
                    pass
            if "apps.strategy_engine.indicators" not in sys.modules:
                try:
                    import apps.strategy_engine.indicators  # noqa: F401
                except ImportError:
                    pass

            spec.loader.exec_module(module)

            # 查找继承自 BaseStrategy 的类
            strategy_cls = None
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and attr is not BaseStrategy
                    and issubclass(attr, BaseStrategy)
                ):
                    strategy_cls = attr
                    break

            if strategy_cls is None:
                return None, "未找到继承自 BaseStrategy 的策略类"

            return strategy_cls, None

        except Exception as e:
            return None, f"模块加载失败: {e}"

    def _validate_strategy_class(self, strategy_cls: type) -> list[str]:
        """验证策略类的必需属性和方法"""
        errors = []

        # 检查 name 属性
        if not hasattr(strategy_cls, "name") or not getattr(strategy_cls, "name"):
            errors.append("策略类缺少 'name' 属性或为空")

        # 检查 on_bar 方法
        if not hasattr(strategy_cls, "on_bar"):
            errors.append("策略类缺少 'on_bar' 方法")
        elif not callable(getattr(strategy_cls, "on_bar")):
            errors.append("'on_bar' 不是可调用的方法")

        # 检查 params_schema（可选，但如果存在则验证格式）
        if hasattr(strategy_cls, "params_schema"):
            schema = getattr(strategy_cls, "params_schema")
            if not isinstance(schema, dict):
                errors.append("'params_schema' 必须是字典")
            else:
                for key, val in schema.items():
                    if isinstance(val, dict):
                        if "default" not in val and "type" not in val:
                            # 宽松验证：至少有一个字段
                            pass

        return errors

    def _run_mock_backtest(self, strategy_cls: type) -> dict[str, Any]:
        """
        用模拟 K 线数据运行策略，验证 on_bar 行为。
        """
        from apps.strategy_engine.base import OrderSignal, StrategyContext

        errors: list[str] = []
        warnings: list[str] = []

        # 创建上下文
        schema: dict = getattr(strategy_cls, "params_schema", {})
        params = {
            key: val.get("default") if isinstance(val, dict) else val
            for key, val in schema.items()
        }

        context = StrategyContext(
            symbol="BTC/USDT",
            timeframe="1h",
            mode="backtest",
            params=params,
            balance=self.initial_capital,
            position=Decimal("0"),
        )

        # 实例化策略
        try:
            strategy = strategy_cls(context)
        except Exception as e:
            return {"stats": None, "errors": [f"策略实例化失败: {e}"], "warnings": []}

        # 生成模拟数据
        klines = _generate_mock_klines(self.kline_count)

        # 逐 bar 运行
        signals_count = 0
        buy_count = 0
        sell_count = 0
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

            # 验证信号类型
            if not isinstance(signal, OrderSignal):
                errors.append(
                    f"on_bar 返回了非 OrderSignal 类型: {type(signal).__name__}"
                )
                break

            signals_count += 1

            # 验证信号合法性
            if signal.side == "buy":
                if position > 0:
                    warnings.append(f"第 {i + 1} 根 K 线: 有持仓时又发出买入信号")
                buy_count += 1
                cost = signal.quantity * Decimal(str(kline["close"]))
                commission = cost * Decimal("0.001")
                if cost + commission <= balance:
                    balance -= cost + commission
                    position += signal.quantity
                else:
                    warnings.append(f"第 {i + 1} 根 K 线: 资金不足以买入")

            elif signal.side == "sell":
                if position <= 0:
                    warnings.append(f"第 {i + 1} 根 K 线: 无持仓时发出卖出信号")
                sell_count += 1
                sell_qty = min(signal.quantity, position)
                proceeds = sell_qty * Decimal(str(kline["close"]))
                commission = proceeds * Decimal("0.001")
                balance += proceeds - commission
                position -= sell_qty

        try:
            strategy.on_stop()
        except Exception as e:
            warnings.append(f"on_stop 调用失败: {e}")

        if errors:
            return {"stats": None, "errors": errors, "warnings": warnings}

        # 计算简单统计
        final_equity = balance + position * Decimal(str(klines[-1]["close"]))
        total_return = float(
            ((final_equity - self.initial_capital) / self.initial_capital) * 100
        )

        stats = {
            "total_bars": len(klines),
            "signals_count": signals_count,
            "buy_count": buy_count,
            "sell_count": sell_count,
            "final_balance": float(balance),
            "final_position": float(position),
            "final_equity": float(final_equity),
            "total_return_pct": round(total_return, 2),
        }

        return {"stats": stats, "errors": [], "warnings": warnings}
