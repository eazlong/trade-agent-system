# L3 形式化规范协议 (Formal Specifications)

## 概述

形式化规范是验证金字塔 L3 层的核心方法，用于消除架构歧义。通过 TLA+ 或类似工具定义系统"真理"，验证系统行为是否符合规范。

## 核心概念

### 不变量 (Invariants)

系统状态必须始终满足的约束条件：

```
// 账户余额非负
BalanceNonNegative == ∀ a ∈ Account: balance[a] ≥ 0

// 资金守恒
FundsConservation == ∀ a ∈ Account: locked[a] ≤ balance[a]

// 订单状态合法性
ValidOrderStatus == ∀ o ∈ Order: status[o] ∈ {"pending", "submitted", "filled", "completed", "failed", "cancelled"}
```

### 状态转换 (State Transitions)

定义合法的状态转换：

```
// 订单状态转换
SubmitOrder(o) ==
    /\ status[o] = "pending"
    /\ status' = [status EXCEPT ![o] = "submitted"]
    /\ UNCHANGED <<balance, locked>>

FillOrder(o) ==
    /\ status[o] = "submitted"
    /\ status' = [status EXCEPT ![o] = "filled"]
    /\ UNCHANGED <<balance, locked>>
```

## 不变量清单

### 架构不变量

| ID | 不变量 | 描述 |
|----|--------|------|
| I1 | `DependencyHierarchy` | 依赖层级正确（Types → Config → Repo → Service → API） |
| I2 | `LazyLoading` | Agent 懒加载 |
| I3 | `RegistryIntegrity` | 注册中心完整 |

### 金融不变量

| ID | 不变量 | 描述 |
|----|--------|------|
| I4 | `BalanceNonNegative` | 账户余额 ≥ 0 |
| I5 | `OrderQuantityNonNegative` | 订单数量 ≥ 0 |
| I6 | `PriceNonNegative` | 价格 ≥ 0 |
| I7 | `FundsConservation` | 锁定资金 ≤ 可用资金 |

### 数据不变量

| ID | 不变量 | 描述 |
|----|--------|------|
| I8 | `UniquenessConstraints` | ID 唯一 |
| I9 | `DataIntegrity` | 外键关系正确 |

### 并发不变量

| ID | 不变量 | 描述 |
|----|--------|------|
| I10 | `StreamOffsetMonotonic` | Redis Stream 偏移量单调递增 |
| I11 | `TaskIdempotency` | Celery 任务幂等 |

### 安全不变量

| ID | 不变量 | 描述 |
|----|--------|------|
| I12 | `ApiKeyEncryption` | API Key 加密存储 |
| I13 | `NoHardcodedSecrets` | 无硬编码密钥 |

## 使用方法

### 1. 定义规范

```python
# backend/utils/testing/invariants.py

INVARIANTS = {
    "balance_non_negative": {
        "category": "financial",
        "spec": "∀ a ∈ Account: balance[a] ≥ 0",
        "check": lambda accounts: all(a.balance >= 0 for a in accounts),
    },
    "funds_conservation": {
        "category": "financial",
        "spec": "∀ a ∈ Account: locked[a] ≤ balance[a]",
        "check": lambda accounts: all(a.locked <= a.balance for a in accounts),
    },
}
```

### 2. 检查不变量

```python
from utils.testing.check_invariants import InvariantsChecker

checker = InvariantsChecker()
result = checker.check_all()

if result["failed"] > 0:
    for r in result["results"]:
        if not r["passed"]:
            print(f"❌ {r['name']}: {r['message']}")
```

### 3. CI 集成

```yaml
# .github/workflows/verify.yml
- name: Check Invariants
  run: |
    cd backend
    DJANGO_SETTINGS_MODULE=core.settings.dev python scripts/check_invariants.py
```

## 成功标准

| 指标 | 要求 |
|------|------|
| 不变量检查 | 100% 通过 |
| 架构违规 | 0 |
| 金融不变量违规 | 0 |

## TLA+ 规范示例

```
---- MODULE TradingSystem ----
EXTENDS Integers, Sequences

CONSTANTS Account, Order, MAX_BALANCE

VARIABLES balance, locked, status

TypeOK ==
    /\ balance ∈ [Account → Int]
    /\ locked ∈ [Account → Int]
    /\ status ∈ [Order → {"pending", "submitted", "filled", "completed", "failed", "cancelled"}]

BalanceNonNegative ==
    ∀ a ∈ Account: balance[a] ≥ 0

FundsConservation ==
    ∀ a ∈ Account: locked[a] ≤ balance[a]

Init ==
    /\ balance = [a ∈ Account |→ 0]
    /\ locked = [a ∈ Account |→ 0]
    /\ status = [o ∈ Order |→ "pending"]

Deposit(a, amount) ==
    /\ balance' = [balance EXCEPT ![a] = @ + amount]
    /\ UNCHANGED <<locked, status>>

Withdraw(a, amount) ==
    /\ balance[a] ≥ amount
    /\ balance' = [balance EXCEPT ![a] = @ - amount]
    /\ UNCHANGED <<locked, status>>

Next ==
    ∃ a ∈ Account, amount ∈ Int:
        Deposit(a, amount) \/ Withdraw(a, amount)

Spec == Init /\ [][Next]_<<balance, locked, status>>

THEOREM Spec => []TypeOK /\ []BalanceNonNegative /\ []FundsConservation
====
```

## 与其他验证层的配合

- **L2 → L3**: DST 通过后，进入形式化规范验证
- **L3 → L4**: 形式化规范通过后，进入数学证明层
- **L3 失败**: 标识架构或逻辑缺陷，反馈智能体修复