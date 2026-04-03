# 订单生命周期

## 状态机

```
pending → submitted → filled → completed
    ↓         ↓          ↓
  cancelled  failed    cancelled
```

## 状态定义

| 状态 | 描述 | 可转换状态 |
|------|------|------------|
| `pending` | 订单创建，等待提交 | submitted, cancelled |
| `submitted` | 已提交到交易所 | filled, failed, cancelled |
| `filled` | 已成交 | completed, cancelled |
| `completed` | 订单完成 | - |
| `failed` | 订单失败 | - |
| `cancelled` | 订单取消 | - |

## 订单不变量

### 状态转换合法性
```
valid_transitions = {
    'pending': ['submitted', 'cancelled'],
    'submitted': ['filled', 'failed', 'cancelled'],
    'filled': ['completed', 'cancelled'],
    'completed': [],
    'failed': [],
    'cancelled': [],
}
```

### 幂等性保证
```
order_id + operation_id → 唯一结果
```
- 相同的 `order_id` + `operation_id` 组合必须产生相同结果
- 支持安全重试

### 时间不变量
```
created_at ≤ submitted_at ≤ filled_at ≤ completed_at
```

## 订单处理流程

```
1. 验证订单参数
2. 检查风控约束
3. 锁定资金
4. 提交到交易所
5. 更新订单状态
6. 处理成交结果
7. 更新持仓
8. 释放/扣除资金
```

## 失败处理

| 失败阶段 | 处理方式 |
|----------|----------|
| 参数验证 | 返回错误，不创建订单 |
| 风控检查 | 返回错误，不创建订单 |
| 资金锁定 | 返回错误，不创建订单 |
| 交易所提交 | 标记失败，释放资金 |
| 成交处理 | 记录错误，人工介入 |