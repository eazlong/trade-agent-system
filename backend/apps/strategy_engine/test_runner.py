"""
策略测试运行器 — MVP 精简版

轻量级策略验证器，用于验证新创建的策略代码是否能正确加载、实例化和运行。
"""

from __future__ import annotations

import importlib.util
import logging
import random
import re
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _generate_mock_klines(n: int = 100) -> list[dict]:
    """生成模拟 K 线数据（所有价格/成交量均为 Decimal 类型）"""
    random.seed(42)
    base_price = Decimal("50000.00")
    klines = []
    for i in range(n):
        change = Decimal(str(random.uniform(-0.02, 0.02)))
        open_price = base_price * (Decimal("1") + change)
        close_price = base_price * (Decimal("1") + change * Decimal("0.5"))
        high_price = max(open_price, close_price) * (Decimal("1") + abs(Decimal(str(random.uniform(0, 0.01)))))
        low_price = min(open_price, close_price) * (Decimal("1") - abs(Decimal(str(random.uniform(0, 0.01)))))
        klines.append({
            "timestamp": f"2024-01-{(i // 24) + 1:02d}T{i % 24:02d}:00:00",
            "open": open_price.quantize(Decimal("0.01")),
            "high": high_price.quantize(Decimal("0.01")),
            "low": low_price.quantize(Decimal("0.01")),
            "close": close_price.quantize(Decimal("0.01")),
            "volume": Decimal(str(round(random.uniform(100, 1000), 2))),
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

        # 1) 语法检查
        syntax_ok, syntax_error = self._check_syntax(path)
        if not syntax_ok:
            return {"success": False, "strategy_name": None, "errors": [f"语法错误: {syntax_error}"], "stats": None, "warnings": []}

        # 1.5) 静态分析 — 扫描 Decimal 类型误用
        type_warnings = self._scan_type_misuse(path)

        strategy_cls, load_error = self._load_strategy_module(path)
        if load_error:
            return {"success": False, "strategy_name": None, "errors": [f"加载失败: {load_error}"], "stats": None, "warnings": type_warnings}

        validate_errors = self._validate_strategy_class(strategy_cls)
        if validate_errors:
            return {"success": False, "strategy_name": getattr(strategy_cls, "name", None), "errors": validate_errors, "stats": None, "warnings": type_warnings}

        strategy_name = getattr(strategy_cls, "name", "unnamed")

        # 检查策略注册
        reg_warning = self._check_registration(strategy_cls)

        run_result = self._run_mock_backtest(strategy_cls)
        all_warnings = run_result.get("warnings", [])
        if reg_warning:
            all_warnings.insert(0, reg_warning)

        if run_result["errors"]:
            return {"success": False, "strategy_name": strategy_name, "errors": run_result["errors"], "stats": run_result.get("stats"), "warnings": type_warnings + all_warnings}

        return {"success": True, "strategy_name": strategy_name, "errors": [], "stats": run_result["stats"], "warnings": type_warnings + all_warnings}

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

    def _scan_type_misuse(self, path: Path) -> list[str]:
        """静态扫描策略源码中的 Decimal 类型误用风险"""
        warnings: list[str] = []
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()

        # 检查是否导入了 Decimal
        uses_decimal = any(
            re.search(r"from\s+decimal\s+import", line) or re.search(r"import\s+decimal", line)
            for line in lines
        )
        if not uses_decimal:
            return warnings  # 没有 Decimal 用法，不扫描

        for i, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            # Pattern 1: Decimal 与 float 字面量直接运算（缺少 str 包裹）
            # 匹配: Decimal(...) * 0.XX 或 0.XX * Decimal(...)
            float_literal_ops = re.findall(
                r"Decimal\([^)]*\)\s*([+\-*/])\s*(\d+\.\d+)"
                r"|"
                r"(\d+\.\d+)\s*([+\-*/])\s*Decimal\([^)]*\)",
                stripped,
            )
            for m in float_literal_ops:
                float_val = m[1] or m[2]
                op = m[0] or m[3]
                warnings.append(
                    f"第 {i} 行: Decimal 与 float 字面量 {float_val} 直接{op}运算，"
                    f"将抛出 TypeError。应使用 Decimal(\"{float_val}\") 包裹。"
                )

            # Pattern 2: kline 字段（已知为 Decimal）与 float 字面量运算
            # 匹配: kline["close"] * 0.XX 或 0.XX * kline["close"]
            kline_float_ops = re.findall(
                r'kline\[[\'"](open|high|low|close|volume)[\'"]\]\s*([+\-*/])\s*(\d+\.\d+)'
                r'|'
                r'(\d+\.\d+)\s*([+\-*/])\s*kline\[[\'"](open|high|low|close|volume)[\'"]\]',
                stripped,
            )
            for m in kline_float_ops:
                if m[0]:  # kline["field"] op float
                    field, op, float_val = m[0], m[1], m[2]
                else:  # float op kline["field"]
                    float_val, op, field = m[3], m[4], m[5]
                warnings.append(
                    f"第 {i} 行: kline[\"{field}\"]（Decimal 类型）与 float 字面量 {float_val} 直接{op}运算，"
                    f"将抛出 TypeError。应使用 Decimal(\"{float_val}\") 包裹。"
                )

        return warnings

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

    def _check_registration(self, strategy_cls: type) -> str | None:
        """检查策略是否使用了 @register_strategy 装饰器"""
        from apps.strategy_engine.registry import StrategyRegistry

        name = getattr(strategy_cls, "name", "")
        if not name:
            return None
        # 如果策略路径已设置，尝试 discover
        if StrategyRegistry._strategy_path:
            StrategyRegistry.discover()
        if name not in StrategyRegistry.list_registered():
            return (
                f"策略未注册到 StrategyRegistry（缺少 @register_strategy() 装饰器）。"
                f"name='{name}'，已注册: {StrategyRegistry.list_registered()}"
            )
        return None

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
                err_msg = str(e)
                enriched = err_msg
                # 自动识别 Decimal 类型混用错误
                if "TypeError" in type(e).__name__:
                    if "Decimal" in err_msg or "unsupported operand" in err_msg.lower():
                        enriched = (
                            f"{err_msg}\n"
                            f"  → 可能是 Decimal 与 float/str 混用导致的类型错误。"
                            f"确保所有数值运算都使用 Decimal 类型（float 需用 Decimal(str(value)) 转换）。"
                        )
                errors.append(f"on_bar 第 {i + 1} 根 K 线时出错: {enriched}")
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

        # 零交易检测 — on_bar 从未返回有效信号
        if signals_count == 0:
            errors.append(
                "零交易：on_bar 在所有 K 线上均返回 None。"
                "请确保 on_bar 在满足条件时返回 ctx.buy() / ctx.sell() / ctx.close_position() 的返回值，"
                "而不是仅调用这些方法后返回 None。"
            )
            return {"stats": None, "errors": errors, "warnings": warnings}

        # 有信号但未成交（买入资金不足或卖出无持仓）
        if buy_count == 0 and sell_count == 0:
            warnings.append(
                f"策略生成了 {signals_count} 个信号，但无实际成交。"
                "检查资金是否充足、信号 side 是否正确。"
            )

        final_equity = balance + position * Decimal(str(klines[-1]["close"]))
        total_return = float(((final_equity - self.initial_capital) / self.initial_capital) * 100)
        stats = {
            "total_bars": len(klines), "signals_count": signals_count,
            "buy_count": buy_count, "sell_count": sell_count,
            "final_balance": float(balance), "final_position": float(position),
            "final_equity": float(final_equity), "total_return_pct": round(total_return, 2),
        }
        return {"stats": stats, "errors": [], "warnings": warnings}
