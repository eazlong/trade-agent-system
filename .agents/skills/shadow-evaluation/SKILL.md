---
name: shadow-evaluation
description: L1 语义先知层验证，使用影子状态评估器比对实际输出与参考实现
when_to_use: 当需要验证核心业务逻辑正确性时激活
references:
  - docs/SHADOW_EVAL.md
---

# 影子评估流程

## 概述

影子评估（Shadow Evaluation）是验证金字塔 L1 层的核心方法：
- 将智能体代码的响应与一个逻辑简单但绝对正确的"参考版本"进行实时比对
- 捕获基础逻辑错误，无需复杂的测试基础设施

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
    },
    input_data={
        "orders": [...]
    }
)

if result.mismatch:
    print(f"语义差异: {result.diff}")
```

### CLI 调用
```bash
cd backend
python -c "from utils.testing.shadow_evaluator import get_evaluator; ..."
```

## 预定义参考实现

| 名称 | 描述 | 用途 |
|------|------|------|
| `order_matching` | 简单 FIFO 撮合 | 验证订单撮合逻辑 |
| `signal_generation` | 简单阈值触发 | 验证信号生成逻辑 |
| `risk_check` | 简单风控规则 | 验证风控引擎 |

## 成功标准

- ✅ 哈希值完全匹配（精确验证）
- ✅ 误差率 < 0.001%（数值容忍）
- ✅ 无缺失/多余键值

## 扩展指南

### 新增参考实现
```python
def my_reference_impl(input_data: Dict) -> Dict:
    # 简单、确定、绝对正确的实现
    return {"result": ...}

evaluator.register_reference("my_function", my_reference_impl)
```