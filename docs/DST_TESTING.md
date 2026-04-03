# L2 确定性仿真测试协议 (DST Testing)

## 概述

确定性仿真测试（Deterministic Simulation Testing，DST）是验证金字塔 L2 层的核心方法，用于捕获隐蔽的并发、时序、网络漏洞。通过 BUGGIFY 技术人为扩大并发干扰窗口，在抽象的时间轴上运行数千个确定性随机种子，发现普通测试无法发现的 Bug。

## 核心原理

```
确定性种子 ──> 故障注入 ──> 执行测试 ──> 验证不变量
     ↑                                    ↓
     └──────── 失败时可复现 ──────────────┘
```

关键特性：
- **确定性**: 相同种子 → 相同执行路径 → 相同结果
- **可复现**: 失败时可精确定位到触发条件
- **高覆盖**: 数千个种子覆盖大量边界情况

## BUGGIFY 技术

### 故障注入原理

BUGGIFY 在关键代码路径中预留故障触发点，在仿真模式下故意注入故障：

```python
from utils.testing.buggify import buggify

@shared_task
def process_order(order_id):
    order = Order.objects.get(id=order_id)

    # 故障注入点：模拟读取订单和锁定资金之间的延迟
    if buggify():
        pass  # DST 将在此注入 10-100ms 延迟

    lock_funds(order.user)  # DST 验证是否出现重复扣款
```

### 故障类型

| 类型 | 函数 | 模拟场景 |
|------|------|----------|
| `delay` | `buggify()` | 竞态条件 |
| `network_partition` | `BuggifyContext.network_partition()` | Redis 连接中断 |
| `disk_latency` | `BuggifyContext.disk_latency()` | WAL 漏洞 |
| `redis_timeout` | `inject_fault("redis_timeout")` | 消息队列阻塞 |
| `db_deadlock` | `inject_fault("db_deadlock")` | 事务冲突 |

## 使用方法

### 1. 配置仿真环境

```bash
export SIMULATION_MODE=True
export BUGGIFY_PROBABILITY=0.25  # 故障触发概率
export BUGGIFY_DELAY_MIN=0.01   # 最小延迟（秒）
export BUGGIFY_DELAY_MAX=0.1    # 最大延迟（秒）
```

### 2. 编写 DST 测试

```python
import pytest
from utils.testing.buggify import BuggifyConfig

@pytest.mark.parametrize("seed", range(100))
def test_order_processing_dst(seed, db):
    """DST: 订单处理在不同种子下的行为"""
    # 设置确定性种子
    BuggifyConfig.set_seed(seed)
    BuggifyConfig.SIMULATION_MODE = True

    try:
        # 执行订单处理
        result = process_order_task(test_order_id)

        # 验证不变量
        assert result["status"] in ["completed", "failed", "retry"]
        assert result["retry_count"] <= 3

        # 验证资金一致性
        account = Account.objects.get(user_id=test_user_id)
        assert account.balance >= 0
        assert account.locked_funds <= account.balance

    finally:
        BuggifyConfig.reset_seed()
```

### 3. 运行 DST 测试

```bash
# 单次测试
SIMULATION_MODE=True pytest apps/trading/tests/dst/test_order_tasks.py -v

# 多种子批量测试
for seed in {1..1000}; do
    SIMULATION_MODE=True BUGGIFY_SEED=$seed pytest apps/trading/tests/dst/ -q
done

# 使用 pytest-xdist 并行
SIMULATION_MODE=True pytest apps/trading/tests/dst/ -v --dist=each --worker-count=4
```

## 测试策略

### 1. 并发竞态测试

测试多任务并发执行时的行为：

```python
@pytest.mark.parametrize("seed", range(100))
def test_concurrent_order_dst(seed):
    """测试并发订单处理的竞态条件"""
    BuggifyConfig.set_seed(seed)
    BuggifyConfig.SIMULATION_MODE = True

    # 同时处理多个订单
    results = parallel_execute([
        lambda: process_order(order_id_1),
        lambda: process_order(order_id_2),
        lambda: process_order(order_id_3),
    ])

    # 验证资金守恒
    total_deducted = sum(r["deducted"] for r in results)
    assert total_deducted <= account.balance
```

### 2. 网络分区测试

测试网络中断时的行为：

```python
from utils.testing.buggify import BuggifyContext

def test_network_partition_dst():
    """测试网络分区时的订单处理"""
    with BuggifyContext.network_partition(duration=0.5):
        result = process_order_task(order_id)
        assert result["status"] == "retry" or result["status"] == "failed"
```

### 3. 数据库死锁测试

测试事务冲突时的行为：

```python
def test_db_deadlock_dst():
    """测试数据库死锁时的处理"""
    with BuggifyContext.force_fault():
        result = update_position_task(position_id)
        assert result["status"] in ["success", "retry", "failed"]
```

## 成功标准

| 指标 | 要求 |
|------|------|
| 种子通过率 | 100% |
| 最小种子数 | 500 |
| 最大重试次数 | 3 |
| 不变量检查 | 全部通过 |

## 最佳实践

### 1. 故障注入点位置

在关键路径插入故障注入点：
- 资金操作前后
- 外部 API 调用前后
- 状态转换前后
- 锁获取前后

### 2. 不变量验证

每次测试后验证核心不变量：
- 账户余额 ≥ 0
- 锁定资金 ≤ 可用资金
- 订单状态转换合法
- 消息偏移量单调递增

### 3. 种子管理

记录失败种子，用于回归测试：
```bash
# 记录失败种子
echo "Failed seed: $seed" >> dst_failures.log

# 回归测试
SIMULATION_MODE=True BUGGIFY_SEED=12345 pytest apps/trading/tests/dst/
```

## 与其他验证层的配合

- **L1 → L2**: L1 语义比对通过后，进入 DST 测试
- **L2 → L3**: DST 通过后，进入形式化规范验证
- **L2 失败**: 记录失败种子，反馈智能体修复