---
name: dst-testing
description: L2 确定性仿真测试，使用 BUGGIFY 故障注入捕获隐蔽的并发、时序、网络漏洞
when_to_use: 当修改 Celery 任务、Redis Stream 操作或涉及并发的代码时激活
references:
  - docs/DST_TESTING.md
  - backend/utils/testing/buggify.py
---

# 确定性仿真测试（DST）流程

## 概述

确定性仿真测试（Deterministic Simulation Testing）是验证金字塔 L2 层的核心方法：
- 使用 BUGGIFY 技术人为扩大并发干扰窗口
- 在抽象的时间轴上运行数千个确定性随机种子
- 捕获普通单元测试无法发现的隐蔽漏洞

## 使用方法

### 1. 在代码中插入 BUGGIFY 点
```python
from utils.testing.buggify import buggify

@shared_task
def process_order(order_id):
    order = Order.objects.get(id=order_id)

    # 故障注入点：模拟读取订单和锁定资金之间的延迟
    if buggify():
        pass  # DST 将在此注入延迟

    lock_funds(order.user)
```

### 2. 运行 DST 测试
```bash
cd backend
SIMULATION_MODE=True DJANGO_SETTINGS_MODULE=core.settings.dev pytest apps/trading/tests/dst/ -v --seed=12345
```

### 3. 多种子批量测试
```bash
# 运行 1000 个独立种子
for seed in {1..1000}; do
    SIMULATION_MODE=True BUGGIFY_SEED=$seed pytest apps/trading/tests/dst/ -q
done
```

## BUGGIFY 故障类型

| 类型 | 描述 | 模拟场景 |
|------|------|----------|
| `delay` | 微秒级延迟 | 竞态条件 |
| `network_partition` | 网络分区 | Redis 连接中断 |
| `disk_latency` | 磁盘延迟 | WAL 漏洞 |
| `redis_timeout` | Redis 超时 | 消息队列阻塞 |
| `db_deadlock` | 数据库死锁 | 事务冲突 |

## 成功标准

- ✅ 1000 个种子全部通过（无失败）
- ✅ 所有 BUGGIFY 注入点被触发测试
- ✅ 任务幂等性验证通过（可安全重试）

## 测试编写指南

### DST 测试文件结构
```
apps/trading/tests/dst/
├── conftest.py           # DST 配置
├── test_order_tasks.py   # 订单任务 DST
├── test_matching.py      # 撮合逻辑 DST
└── seeds_config.json     # 种子配置
```

### 测试示例
```python
import pytest
from utils.testing.buggify import BuggifyConfig

@pytest.mark.parametrize("seed", range(100))
def test_order_processing_dst(seed):
    """DST: 订单处理在不同种子下的行为"""
    BuggifyConfig.set_seed(seed)
    BuggifyConfig.SIMULATION_MODE = True

    # 执行订单处理
    result = process_order_task(order_id)

    # 验证不变量
    assert result.status in ["completed", "failed"]
    assert result.retry_count <= 3

    BuggifyConfig.reset_seed()
```