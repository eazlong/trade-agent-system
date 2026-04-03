# 交易执行系统架构设计 v1

> 架构师C：交易执行层专家
> 版本：v1 | 日期：2026-03-26

---

## 1. 系统定位

交易执行系统是整个平台的「最后一公里」，负责将经过RiskGuard校验的OrderRequest转化为真实的交易所订单，并管理订单生命周期。

**核心原则**
- 幂等性：同一OrderRequest无论重试多少次，只执行一次
- 原子性：仓位变更与数据库记录必须原子提交
- 优先级：风控指令（平仓/熔断）优先级永远高于开仓指令
- 可观测：每笔订单全生命周期均有日志

---

## 2. 整体架构

```
┌─────────────────────────────────────────────────────────┐
│                  交易执行系统边界                          │
│                                                          │
│  Redis Stream: trading:orders:inbox                      │
│         │                                                │
│         ▼                                                │
│  ┌─────────────────┐    ┌──────────────────┐            │
│  │  OrderConsumer  │───▶│   RiskGuard      │            │
│  │  (优先级队列)    │    │  (最终校验层)     │            │
│  └─────────────────┘    └────────┬─────────┘            │
│                                  │ 通过                  │
│                                  ▼                       │
│  ┌───────────────────────────────────────────────────┐   │
│  │              ExchangeRouter                        │   │
│  │  ┌──────────────┐  ┌──────────────┐               │   │
│  │  │ BinanceAdpter│  │  OKXAdapter  │  ...          │   │
│  │  │  (CCXT)      │  │  (CCXT)      │               │   │
│  │  └──────────────┘  └──────────────┘               │   │
│  └───────────────────────────────────────────────────┘   │
│                          │                               │
│                          ▼                               │
│  ┌────────────────────────────────────────────────────┐  │
│  │            OrderLifecycleManager                    │  │
│  │  PENDING → SUBMITTED → PARTIAL → FILLED / FAILED   │  │
│  └────────────────────────────────────────────────────┘  │
│                          │                               │
│          ┌───────────────┼───────────────┐               │
│          ▼               ▼               ▼               │
│    PostgreSQL       Redis Cache      WebSocket           │
│    (持久化)         (实时仓位)       (前端推送)           │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

---

## 3. 核心数据模型

### 3.1 OrderRequest（入队格式）

```python
@dataclass
class OrderRequest:
    request_id: str           # UUID，用于幂等控制
    user_id: str
    account_id: str
    symbol: str               # 统一格式：BTC/USDT
    side: Literal['buy','sell']
    order_type: Literal['market','limit','stop_market','stop_limit']
    quantity: float           # 数量（基础货币）
    price: Optional[float]    # limit单必填
    stop_price: Optional[float]  # stop单必填
    time_in_force: str        # GTC/IOC/FOK
    reduce_only: bool         # True=只减仓
    source: str               # agent_id or 'manual'
    priority: int             # 0=普通, 1=止损, 2=风控熔断
    created_at: datetime
```

### 3.2 PostgreSQL订单表

```sql
CREATE TABLE orders (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id      UUID UNIQUE NOT NULL,  -- 幂等键
    user_id         VARCHAR(50) NOT NULL,
    account_id      VARCHAR(50) NOT NULL,
    exchange        VARCHAR(20) NOT NULL,
    symbol          VARCHAR(20) NOT NULL,
    side            VARCHAR(4)  NOT NULL,
    order_type      VARCHAR(15) NOT NULL,
    quantity        NUMERIC(20,8) NOT NULL,
    price           NUMERIC(20,8),
    stop_price      NUMERIC(20,8),
    status          VARCHAR(10) NOT NULL DEFAULT 'pending',
    exchange_order_id VARCHAR(50),
    filled_quantity NUMERIC(20,8) DEFAULT 0,
    avg_fill_price  NUMERIC(20,8),
    commission      NUMERIC(20,8),
    source          VARCHAR(50),
    priority        SMALLINT DEFAULT 0,
    error_msg       TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    submitted_at    TIMESTAMPTZ,
    filled_at       TIMESTAMPTZ
);

CREATE INDEX idx_orders_user_status ON orders(user_id, status);
CREATE INDEX idx_orders_created ON orders(created_at DESC);
```

### 3.3 仓位快照（Redis）

```
Key: position:{user_id}:{account_id}:{symbol}
Type: Hash
Fields:
    side: long/short/none
    quantity: 0.05
    avg_entry_price: 42350.00
    unrealized_pnl:    unrealized_pnl: -120.50
    leverage: 10
    liquidation_price: 38000.00
TTL: 永久（手动清除）
```

---

## 4. 订单执行流程

### 4.1 正常开仓流程

```
OrderRequest入队（Redis Stream）
    │
    ▼
OrderConsumer拉取（按priority排序）
    │
    ▼
幂等检查（request_id是否已存在DB）
    │ 不存在
    ▼
RiskGuard最终校验
    ├── 账户余额是否充足
    ├── 仓位上限是否超标
    ├── 用户是否被冻结
    └── 当前是否处于熔断状态
    │ 通过
    ▼
ExchangeRouter选择交易所适配器
    │
    ▼
CCXT submit_order()
    │
    ├── 成功 → 更新DB状态=SUBMITTED，更新Redis仓位
    │         启动订单状态轮询（WebSocket优先）
    │
    └── 失败 → 记录error_msg，状态=FAILED
              发送告警通知
```

### 4.2 紧急平仓流程（priority=2）

```
RiskGuard发出 EMERGENCY_CLOSE_ALL
    │
    ▼
冻结用户账户（Redis: user:{id}:frozen=true）
    │
    ▼
查询所有持仓（Redis position keys）
    │
    ▼
并发提交市价平仓单（reduce_only=True）
    │
    ├── 全部成功 → 解冻账户，发送告警
    └── 部分失败 → 重试3次，仍失败则人工告警（保持冻结）
```

---

## 5. 交易所适配器设计

```python
class BaseExchangeAdapter(ABC):
    @abstractmethod
    async def submit_order(self, order: OrderRequest) -> ExchangeOrderId: ...

    @abstractmethod
    async def cancel_order(self, exchange_order_id: str, symbol: str) -> bool: ...

    @abstractmethod
    async def get_order_status(self, exchange_order_id: str) -> OrderStatus: ...

    @abstractmethod
    async def get_positions(self, account_id: str) -> list[Position]: ...

    @abstractmethod
    async def get_balance(self, account_id: str) -> AccountBalance: ...

class BinanceFuturesAdapter(BaseExchangeAdapter):
    def __init__(self, api_key: str, api_secret: str):
        self.exchange = ccxt.binanceusdm({
            'apiKey': api_key,
            'secret': api_secret,
            'options': {'defaultType': 'future'},
        })
```

---

## 6. 风控熔断规则（交易执行层实施）

| 规则 | 阈值 | 动作 |
|------|------|------|
| 单笔最大亏损 | 账户净值2% | 拒绝开仓 |
| 日最大亏损 | 账户净值5% | 熔断：停止开仓 |
| 周最大回撤 | 账户净值10% | 熔断：平所有仓位 |
| 单一标的最大仓位 | 账户净值20% | 拒绝加仓 |
| 总杠杆 | ≤10x | 拒绝开仓 |
| 连续亏损次数 | 5次 | 强制暂停24h |

所有熔断规则**不可被Agent覆盖**，在代码层面通过独立RiskGuard服务强制执行。

---

## 7. 通知系统集成

```python
class NotificationService:
    channels = ['telegram', 'email']  # 按优先级降级

    async def send_alert(self, level: str, message: str, user_id: str):
        for channel in self.channels:
            try:
                await self._send(channel, message, user_id)
                return  # 第一个成功即止
            except Exception:
                continue  # 降级到下一个渠道
```

**通知触发场景**
- 订单成交（级别：INFO）
- 止损触发（级别：WARNING）
- 风控熔断（级别：CRITICAL）
- 系统异常（级别：CRITICAL）
- 日收益报告（级别：INFO，每日UTC 0点）

---

## 8. API Key安全存储

```python
# 使用 cryptography.fernet 对称加密
from cryptography.fernet import Fernet

class APIKeyVault:
    def __init__(self, master_key: bytes):
        self.fernet = Fernet(master_key)  # master_key来自环境变量

    def encrypt(self, api_key: str) -> bytes:
        return self.fernet.encrypt(api_key.encode())

    def decrypt(self, encrypted: bytes) -> str:
        return self.fernet.decrypt(encrypted).decode()
```

数据库存储：`exchange_accounts.api_key_encrypted` (bytea)
master_key：仅存在于环境变量 `VAULT_MASTER_KEY`，不入库

---

## 9. 性能要求

| 指标 | 目标 |
|------|------|
| 订单提交延迟 | < 500ms（含网络）|
| 紧急平仓响应 | < 2秒完成所有仓位 |
| 并发订单处理 | 50单/秒 |
| 订单状态同步延迟 | < 1秒 |

---

*文档版本：v1 | 架构师C | 待第2轮专家评审*
