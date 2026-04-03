# L1 语义比对协议 (Shadow Evaluation)

## 概述

语义比对是验证金字塔 L1 层的核心方法，用于捕获基础逻辑错误。通过将智能体代码的响应与一个逻辑简单但绝对正确的"参考版本"进行实时比对，验证核心业务逻辑的正确性。

## 核心原理

```
实际输出 ──────┐
              ├──> 哈希比对 ──> 结果
参考输出 ──────┘
```

参考实现特点：
- 逻辑简单（通常 < 50 行）
- 绝对正确（无外部依赖）
- 确定性输出（相同输入 → 相同输出）

## 验证范围

| 模块 | 参考实现 | 验证目标 |
|------|----------|----------|
| 订单撮合 | `simple_order_matching_ref` | 撮合逻辑正确性 |
| 信号生成 | `simple_signal_generation_ref` | 信号触发逻辑 |
| 风控检查 | `simple_risk_check_ref` | 风控规则正确性 |

## 使用方法

### Python API

```python
from utils.testing.shadow_evaluator import get_evaluator

# 获取评估器
evaluator = get_evaluator(error_threshold=0.001)

# 评估订单撮合逻辑
result = evaluator.evaluate(
    "order_matching",
    actual_output={
        "matched": [...],
        "unmatched": [...],
        "total_matched_value": 10000.0,
    },
    input_data={
        "orders": [
            {"id": 1, "side": "buy", "price": 100, "quantity": 10},
            {"id": 2, "side": "sell", "price": 95, "quantity": 5},
        ]
    }
)

if result.mismatch:
    print(f"语义差异: {result.diff}")
    print(f"期望哈希: {result.expected_hash}")
    print(f"实际哈希: {result.actual_hash}")
```

### CLI 调用

```bash
cd backend
python -c "
from utils.testing.shadow_evaluator import get_evaluator

evaluator = get_evaluator()
result = evaluator.evaluate('order_matching', actual_output, input_data)
assert not result.mismatch, f'语义差异: {result.diff}'
"
```

## 成功标准

| 指标 | 阈值 |
|------|------|
| 哈希匹配 | 100%（精确验证） |
| 误差率 | < 0.001%（数值容忍） |
| 缺失键 | 0 |
| 多余键 | 0 |

## 扩展指南

### 新增参考实现

```python
def my_reference_impl(input_data: Dict) -> Dict:
    """
    参考实现要求：
    1. 逻辑简单（< 50 行）
    2. 无外部依赖（纯 Python）
    3. 确定性输出
    """
    # 简单、确定、绝对正确的实现
    result = {}
    for item in input_data.get("items", []):
        result[item["id"]] = process_simple(item)
    return result

# 注册参考实现
evaluator.register_reference("my_function", my_reference_impl)
```

### 自定义误差阈值

```python
evaluator = ShadowEvaluator(error_threshold=0.01)  # 1% 误差容忍
```

## 与其他验证层的配合

- **L1 → L2**: L1 通过后，进入 DST 仿真测试
- **L1 失败**: 立即反馈智能体进行修复，无需继续验证

## 常见问题

### Q: 为什么不直接比对 JSON？
A: 哈希比对更高效，且能捕获结构差异。

### Q: 数值误差如何处理？
A: 使用 `error_threshold` 参数设置容忍度，对浮点数进行近似比对。

### Q: 参考实现如何保证正确性？
A: 参考实现逻辑简单，可人工审查验证。