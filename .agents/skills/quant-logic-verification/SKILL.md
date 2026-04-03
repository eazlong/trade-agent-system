---
name: quant-logic-verification
description: 验证交易信号生成逻辑、风控阈值及订单撮合状态的一致性
when_to_use: 当修改 apps/trading/ 下的策略算法或 Celery 撮合任务时激活
references:
  - .agents/references/risk_invariants.md
  - .agents/references/order_lifecycle.md
---

# 量化逻辑验证流程

## 验证步骤

### 1. 数学不变量检查
```bash
cd backend
DJANGO_SETTINGS_MODULE=core.settings.dev python scripts/check_invariants.py
```

### 2. 影子评估（L1 语义比对）
```bash
# 使用影子评估器比对信号生成逻辑
python -c "
from utils.testing.shadow_evaluator import get_evaluator
evaluator = get_evaluator()
result = evaluator.evaluate('signal_generation', actual_output, input_data)
if result.mismatch:
    raise AssertionError(f'语义差异: {result.diff}')
"
```

### 3. DST 仿真测试（L2 确定性仿真）
```bash
SIMULATION_MODE=True DJANGO_SETTINGS_MODULE=core.settings.dev pytest apps/trading/tests/dst/ -v
```

### 4. 集成测试
```bash
DJANGO_SETTINGS_MODULE=core.settings.dev pytest apps/trading/tests/integration/ -v
```

## 成功标准 (Done Criteria)

- ✅ 所有 pytest 案例通过
- ✅ 影子状态比较误差 < 0.001%
- ✅ DST 仿真 1000 个种子全部通过
- ✅ 内存占用回测期间不超过 512MB
- ✅ 订单撮合状态一致性验证通过

## 关键不变量

### 金融不变量
- `balance ≥ 0`: 账户余额非负
- `order.quantity ≥ 0`: 订单数量非负
- `price ≥ 0`: 价格非负
- `locked_funds ≤ available_funds`: 资金守恒

### 并发不变量
- `offset(t+1) ≥ offset(t)`: Redis Stream 偏移量单调递增
- `task.retry_count ≤ max_retries`: 任务重试次数限制

## 测试数据

测试使用隔离的沙箱环境：
- SQLite 内存数据库
- Mock Redis 客户端
- Mock 交易所 API