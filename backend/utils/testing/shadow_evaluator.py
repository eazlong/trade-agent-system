"""
影子评估器（Shadow Evaluator）

用于 L1 语义先知层验证，将智能体代码的响应与一个逻辑简单但绝对正确的
"参考版本"（如简单的 HashMap）进行实时比对，捕获基础逻辑错误。

使用方法:
    from utils.testing.shadow_evaluator import ShadowEvaluator

    evaluator = ShadowEvaluator()

    # 注册参考实现
    evaluator.register_reference("order_matching", simple_order_matching_ref)

    # 评估实际输出
    result = evaluator.evaluate("order_matching", actual_output)

    if result.mismatch:
        print(f"语义差异: {result.diff}")
"""

import hashlib
import json
from typing import Any, Dict, List, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class EvaluationResult:
    """评估结果"""
    match: bool
    mismatch: bool
    diff: Optional[str] = None
    expected_hash: Optional[str] = None
    actual_hash: Optional[str] = None
    error_threshold: float = 0.0
    timestamp: datetime = field(default_factory=datetime.utcnow)


class ShadowEvaluator:
    """
    影子评估器：用于 L1 语义比对验证

    将实际输出与参考实现输出进行哈希比对，确保语义一致性。
    """

    # 误差阈值（百分比）
    DEFAULT_ERROR_THRESHOLD = 0.001  # 0.001%

    def __init__(self, error_threshold: float = DEFAULT_ERROR_THRESHOLD):
        self.error_threshold = error_threshold
        self._reference_implementations: Dict[str, Callable] = {}
        self._evaluation_history: List[EvaluationResult] = []

    def register_reference(
        self,
        name: str,
        reference_func: Callable,
        description: str = ""
    ) -> None:
        """
        注册参考实现

        Args:
            name: 实现名称（如 "order_matching", "signal_generation"）
            reference_func: 参考实现函数（简单但绝对正确的版本）
            description: 实现描述
        """
        self._reference_implementations[name] = reference_func

    def compute_hash(self, data: Any) -> str:
        """
        计算数据哈希值

        Args:
            data: 任意可序列化数据

        Returns:
            str: SHA256 哈希值
        """
        if isinstance(data, (dict, list)):
            serialized = json.dumps(data, sort_keys=True, default=str)
        else:
            serialized = str(data)

        return hashlib.sha256(serialized.encode()).hexdigest()

    def evaluate(
        self,
        name: str,
        actual_output: Any,
        input_data: Optional[Any] = None,
        tolerance: Optional[float] = None
    ) -> EvaluationResult:
        """
        评估实际输出与参考实现的差异

        Args:
            name: 实现名称
            actual_output: 实际输出
            input_data: 输入数据（用于运行参考实现）
            tolerance: 误差容忍度（可选，覆盖默认值）

        Returns:
            EvaluationResult: 评估结果
        """
        tolerance = tolerance or self.error_threshold
        reference_func = self._reference_implementations.get(name)

        if not reference_func:
            raise ValueError(f"未注册参考实现: {name}")

        # 运行参考实现获取期望输出
        expected_output = reference_func(input_data) if input_data else None

        # 计算哈希
        expected_hash = self.compute_hash(expected_output) if expected_output else None
        actual_hash = self.compute_hash(actual_output)

        # 比对哈希
        match = expected_hash == actual_hash if expected_hash else False

        # 数值误差检查（用于浮点数比对）
        if not match and expected_output and isinstance(actual_output, (dict, list)):
            diff = self._compute_diff(expected_output, actual_output, tolerance)
            mismatch = diff["error_rate"] > tolerance
        else:
            mismatch = not match

        result = EvaluationResult(
            match=match,
            mismatch=mismatch,
            diff=json.dumps(diff) if mismatch else None,
            expected_hash=expected_hash,
            actual_hash=actual_hash,
            error_threshold=tolerance,
        )

        self._evaluation_history.append(result)
        return result

    def _compute_diff(
        self,
        expected: Any,
        actual: Any,
        tolerance: float
    ) -> Dict[str, Any]:
        """
        计算详细差异

        Args:
            expected: 期望输出
            actual: 实际输出
            tolerance: 误差容忍度

        Returns:
            Dict: 差异详情
        """
        diff = {
            "error_rate": 0.0,
            "missing_keys": [],
            "extra_keys": [],
            "value_diffs": [],
        }

        if isinstance(expected, dict) and isinstance(actual, dict):
            # 字典比对
            expected_keys = set(expected.keys())
            actual_keys = set(actual.keys())

            diff["missing_keys"] = list(expected_keys - actual_keys)
            diff["extra_keys"] = list(actual_keys - expected_keys)

            common_keys = expected_keys & actual_keys
            total_values = len(common_keys)
            error_count = 0

            for key in common_keys:
                exp_val = expected[key]
                act_val = actual[key]

                if isinstance(exp_val, (int, float)) and isinstance(act_val, (int, float)):
                    # 数值误差计算
                    if exp_val != 0:
                        error_pct = abs(act_val - exp_val) / abs(exp_val)
                        if error_pct > tolerance:
                            diff["value_diffs"].append({
                                "key": key,
                                "expected": exp_val,
                                "actual": act_val,
                                "error_pct": error_pct,
                            })
                            error_count += 1
                elif exp_val != act_val:
                    diff["value_diffs"].append({
                        "key": key,
                        "expected": exp_val,
                        "actual": act_val,
                    })
                    error_count += 1

            diff["error_rate"] = error_count / total_values if total_values > 0 else 0.0

        elif isinstance(expected, list) and isinstance(actual, list):
            # 列表比对
            if len(expected) != len(actual):
                diff["value_diffs"].append({
                    "type": "length_mismatch",
                    "expected_len": len(expected),
                    "actual_len": len(actual),
                })
                diff["error_rate"] = abs(len(expected) - len(actual)) / len(expected) if expected else 1.0
            else:
                error_count = sum(1 for e, a in zip(expected, actual) if e != a)
                diff["error_rate"] = error_count / len(expected) if expected else 0.0

        return diff

    def get_history(self, limit: int = 50) -> List[EvaluationResult]:
        """
        获取评估历史记录

        Args:
            limit: 返回记录数量限制

        Returns:
            List[EvaluationResult]: 评估历史
        """
        return self._evaluation_history[-limit:]

    def clear_history(self) -> None:
        """清空评估历史"""
        self._evaluation_history.clear()


# ==============================
# 预定义的参考实现（交易系统核心）
# ==============================

def simple_order_matching_ref(input_data: Dict) -> Dict:
    """
    简单订单撮合参考实现（用于 L1 语义比对）

    使用简单的 HashMap 逻辑，不依赖复杂的外部系统。
    用于验证 Celery 撮合任务的核心逻辑正确性。
    """
    orders = input_data.get("orders", [])
    matched = []
    unmatched = []

    # 简单 FIFO 匹配逻辑
    buy_orders = [o for o in orders if o.get("side") == "buy"]
    sell_orders = [o for o in orders if o.get("side") == "sell"]

    for buy in buy_orders:
        for sell in sell_orders:
            if buy.get("price") >= sell.get("price") and sell.get("quantity") > 0:
                match_qty = min(buy.get("quantity", 0), sell.get("quantity", 0))
                matched.append({
                    "buy_order_id": buy.get("id"),
                    "sell_order_id": sell.get("id"),
                    "price": sell.get("price"),
                    "quantity": match_qty,
                })
                buy["quantity"] -= match_qty
                sell["quantity"] -= match_qty

    unmatched.extend([o for o in buy_orders if o.get("quantity") > 0])
    unmatched.extend([o for o in sell_orders if o.get("quantity") > 0])

    return {
        "matched": matched,
        "unmatched": unmatched,
        "total_matched_value": sum(m["price"] * m["quantity"] for m in matched),
    }


def simple_signal_generation_ref(input_data: Dict) -> Dict:
    """
    简单信号生成参考实现

    用于验证 Agent 生成的交易信号逻辑正确性。
    """
    market_data = input_data.get("market_data", {})
    strategy_params = input_data.get("strategy_params", {})

    signals = []

    for symbol, data in market_data.items():
        current_price = data.get("price", 0)
        prev_price = data.get("prev_price", current_price)

        # 简单阈值触发逻辑
        threshold = strategy_params.get("threshold", 0.01)
        change_pct = (current_price - prev_price) / prev_price if prev_price > 0 else 0

        if change_pct > threshold:
            signals.append({
                "symbol": symbol,
                "action": "buy",
                "confidence": min(1.0, change_pct / threshold),
                "reason": "price_increase",
            })
        elif change_pct < -threshold:
            signals.append({
                "symbol": symbol,
                "action": "sell",
                "confidence": min(1.0, abs(change_pct) / threshold),
                "reason": "price_decrease",
            })

    return {
        "signals": signals,
        "generated_at": datetime.utcnow().isoformat(),
    }


def simple_risk_check_ref(input_data: Dict) -> Dict:
    """
    简单风控检查参考实现

    用于验证风控引擎的核心逻辑。
    """
    order = input_data.get("order", {})
    account = input_data.get("account", {})
    risk_limits = input_data.get("risk_limits", {})

    violations = []
    passed = True

    # 检查仓位限制
    max_position = risk_limits.get("max_position_pct", 0.1)
    order_value = order.get("quantity", 0) * order.get("price", 0)
    account_balance = account.get("balance", 0)

    if account_balance > 0:
        position_pct = order_value / account_balance
        if position_pct > max_position:
            violations.append({
                "type": "position_limit_exceeded",
                "value": position_pct,
                "limit": max_position,
            })
            passed = False

    # 检查止损限制
    max_loss_pct = risk_limits.get("max_loss_pct", 0.05)
    current_loss_pct = account.get("loss_pct", 0)

    if current_loss_pct > max_loss_pct:
        violations.append({
            "type": "loss_limit_exceeded",
            "value": current_loss_pct,
            "limit": max_loss_pct,
        })
        passed = False

    return {
        "passed": passed,
        "violations": violations,
        "checked_at": datetime.utcnow().isoformat(),
    }


# ==============================
# 全局评估器实例
# ==============================

_global_evaluator: Optional[ShadowEvaluator] = None


def get_evaluator(error_threshold: float = 0.001) -> ShadowEvaluator:
    """
    获取全局评估器实例

    Args:
        error_threshold: 误差阈值（百分比）

    Returns:
        ShadowEvaluator: 全局评估器
    """
    global _global_evaluator

    if _global_evaluator is None:
        _global_evaluator = ShadowEvaluator(error_threshold=error_threshold)
        # 注册预定义参考实现
        _global_evaluator.register_reference("order_matching", simple_order_matching_ref)
        _global_evaluator.register_reference("signal_generation", simple_signal_generation_ref)
        _global_evaluator.register_reference("risk_check", simple_risk_check_ref)

    return _global_evaluator