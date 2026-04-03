# 第4轮：数据库Schema设计

> 基于v3综合架构
> 覆盖所有PostgreSQL表、Redis数据结构、TimescaleDB时序表

---

## 一、PostgreSQL 核心表设计

### 1.1 用户与权限

```sql
-- 用户表
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           VARCHAR(255) UNIQUE NOT NULL,
    username        VARCHAR(64) UNIQUE NOT NULL,
    password_hash   VARCHAR(255) NOT NULL,  -- bcrypt
    role            VARCHAR(16) NOT NULL DEFAULT 'trader'
                    CHECK (role IN ('observer', 'trader', 'admin')),
    is_active       BOOLEAN NOT NULL DEFAULT true,
    is_frozen       BOOLEAN NOT NULL DEFAULT false,  -- 风控冻结
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login_at   TIMESTAMPTZ
);

CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_role ON users(role);
```

### 1.2 交易所账户

```sql
-- 交易所账户（每个用户可有多个）
CREATE TABLE exchange_accounts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    exchange_name   VARCHAR(32) NOT NULL,   -- binance / okx / bybit
    account_label   VARCHAR(64),            -- 用户自定义名称
    api_key_encrypted   BYTEA NOT NULL,     -- Fernet加密
    api_secret_encrypted BYTEA NOT NULL,
    api_passphrase_encrypted BYTEA,         -- OKX需要
    is_testnet      BOOLEAN NOT NULL DEFAULT false,
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_verified_at TIMESTAMPTZ            -- 最后一次API Key验证时间
);

CREATE INDEX idx_exchange_accounts_user_id ON exchange_accounts(user_id);
-- 每个用户最多20个账户（应用层限制）
```

### 1.3 策略管理

```sql
-- 策略定义
CREATE TABLE strategies (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name            VARCHAR(128) NOT NULL,
    description     TEXT,
    strategy_type   VARCHAR(32) NOT NULL,   -- trend_following / mean_reversion / breakout
    current_version INT NOT NULL DEFAULT 1,
    is_active       BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 策略版本（Git式版本管理）
CREATE TABLE strategy_versions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy_id     UUID NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
    version         INT NOT NULL,
    code_snapshot   TEXT NOT NULL,          -- 策略代码快照
    config_snapshot JSONB NOT NULL,         -- 参数配置快照
    created_by      UUID NOT NULL REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    change_note     TEXT,                   -- 变更说明
    UNIQUE (strategy_id, version)
);

CREATE INDEX idx_strategy_versions_strategy_id ON strategy_versions(strategy_id);
```

### 1.4 交易计划

```sql
-- 交易计划（对话生成的结构化计划）
CREATE TABLE trading_plans (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id),
    account_id      UUID NOT NULL REFERENCES exchange_accounts(id),
    strategy_id     UUID REFERENCES strategies(id),
    symbol          VARCHAR(32) NOT NULL,   -- BTC/USDT
    direction       VARCHAR(8) NOT NULL CHECK (direction IN ('long', 'short')),
    entry_price     DECIMAL(20,8),
    stop_loss_price DECIMAL(20,8) NOT NULL, -- 必填
    take_profit_prices JSONB,               -- [{price: x, pct: 0.5}, ...]
    position_sizing_mode VARCHAR(32) NOT NULL DEFAULT 'fixed_ratio',
    position_size_pct   DECIMAL(5,4),       -- 账户百分比
    position_size_units DECIMAL(20,8),      -- 固定数量
    status          VARCHAR(16) NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft', 'active', 'completed', 'cancelled')),
    agent_reasoning TEXT,                   -- Agent给出计划的推理过程
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activated_at    TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ
);

CREATE INDEX idx_trading_plans_user_account ON trading_plans(user_id, account_id);
CREATE INDEX idx_trading_plans_status ON trading_plans(status);
```

### 1.5 订单记录

```sql
-- 订单记录
CREATE TABLE orders (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id          UUID UNIQUE NOT NULL,   -- 幂等键
    user_id             UUID NOT NULL REFERENCES users(id),
    account_id          UUID NOT NULL REFERENCES exchange_accounts(id),
    trading_plan_id     UUID REFERENCES trading_plans(id),
    exchange_order_id   VARCHAR(128),           -- 交易所返回的ID
    symbol              VARCHAR(32) NOT NULL,
    order_type          VARCHAR(16) NOT NULL,   -- market / limit / stop_market
    side                VARCHAR(8) NOT NULL,    -- buy / sell
    quantity            DECIMAL(20,8) NOT NULL,
    price               DECIMAL(20,8),          -- limit价格，market单为null
    filled_quantity     DECIMAL(20,8) DEFAULT 0,
    avg_fill_price      DECIMAL(20,8),
    commission          DECIMAL(20,8) DEFAULT 0,
    status              VARCHAR(16) NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'submitted', 'partial', 'filled', 'cancelled', 'failed')),
    partial_fill_policy VARCHAR(32) NOT NULL DEFAULT 'cancel_remainder',
    error_message       TEXT,
    submitted_at        TIMESTAMPTZ,
    filled_at           TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_orders_user_account ON orders(user_id, account_id);
CREATE INDEX idx_orders_status ON orders(status);
CREATE INDEX idx_orders_exchange_order_id ON orders(exchange_order_id);
CREATE INDEX idx_orders_created_at ON orders(created_at DESC);
```

### 1.6 风控配置

```sql
-- 风控软限制配置
CREATE TABLE risk_configs (
    user_id                 UUID NOT NULL REFERENCES users(id),
    account_id              UUID NOT NULL REFERENCES exchange_accounts(id),
    daily_loss_alert_pct    DECIMAL(5,4) NOT NULL DEFAULT 0.02,
    daily_loss_halt_pct     DECIMAL(5,4) NOT NULL DEFAULT 0.05,
    weekly_loss_halt_pct    DECIMAL(5,4) NOT NULL DEFAULT 0.10,
    max_consecutive_losses  INT NOT NULL DEFAULT 3,
    position_warn_pct       DECIMAL(5,4) NOT NULL DEFAULT 0.20,
    updated_by              UUID REFERENCES users(id),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, account_id)
);

-- 风控事件日志
CREATE TABLE risk_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id),
    account_id      UUID REFERENCES exchange_accounts(id),
    event_type      VARCHAR(32) NOT NULL,   -- daily_loss_alert / force_close / account_frozen
    severity        VARCHAR(8) NOT NULL CHECK (severity IN ('info', 'warn', 'critical')),
    detail          JSONB NOT NULL,
    triggered_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ
);

CREATE INDEX idx_risk_events_user_id ON risk_events(user_id, triggered_at DESC);
```

### 1.7 Agent决策审计日志

```sql
-- Agent决策完整链路（不可删除，只追加）
CREATE TABLE agent_audit_logs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id),
    session_id      UUID NOT NULL,          -- 一次对话会话
    agent_name      VARCHAR(64) NOT NULL,
    task_type       VARCHAR(64) NOT NULL,
    input_summary   TEXT,                   -- 输入摘要（不存原始大文本）
    reasoning       TEXT,                   -- 推理过程
    output          JSONB,                  -- 结构化输出
    llm_tokens_used INT,
    llm_model       VARCHAR(64),
    duration_ms     INT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
) PARTITION BY RANGE (created_at);         -- 按月分区

-- 创建当前月分区（需要定期创建下月分区）
CREATE TABLE agent_audit_logs_2025_01
    PARTITION OF agent_audit_logs
    FOR VALUES FROM ('2025-01-01') TO ('2025-02-01');

CREATE INDEX idx_audit_logs_user_session ON agent_audit_logs(user_id, session_id);
CREATE INDEX idx_audit_logs_created_at ON agent_audit_logs(created_at DESC);
```

### 1.8 回测结果

```sql
-- 回测任务
CREATE TABLE backtest_tasks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id),
    strategy_id     UUID NOT NULL REFERENCES strategies(id),
    strategy_version INT NOT NULL,
    symbol          VARCHAR(32) NOT NULL,
    timeframe       VARCHAR(8) NOT NULL,
    start_date      DATE NOT NULL,
    end_date        DATE NOT NULL,
    initial_capital DECIMAL(20,2) NOT NULL,
    status          VARCHAR(16) NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    progress_pct    INT DEFAULT 0,
    error_message   TEXT,
    submitted_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);

-- 回测结果（不可变）
CREATE TABLE backtest_results (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id         UUID UNIQUE NOT NULL REFERENCES backtest_tasks(id),
    total_return_pct    DECIMAL(10,4),
    annualized_return   DECIMAL(10,4),
    max_drawdown_pct    DECIMAL(10,4),
    sharpe_ratio        DECIMAL(10,4),
    sortino_ratio       DECIMAL(10,4),
    win_rate            DECIMAL(5,4),
    profit_factor       DECIMAL(10,4),
    total_trades        INT,
    avg_holding_hours   DECIMAL(10,2),
    trades_detail       JSONB,              -- 所有交易明细
    equity_curve        JSONB,              -- 资金曲线（压缩存储）
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### 1.9 长期记忆（向量）

```sql
-- 需要先启用 pgvector 扩展
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE agent_memories (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id),
    memory_type     VARCHAR(32) NOT NULL,   -- strategy_insight / market_pattern / user_preference
    content         TEXT NOT NULL,          -- 记忆原文
    embedding       VECTOR(1536),           -- text-embedding-3-small维度
    metadata        JSONB,                  -- {symbol, timeframe, tags, ...}
    importance      DECIMAL(3,2) DEFAULT 0.5,  -- 0.0-1.0，影响检索权重
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_accessed_at TIMESTAMPTZ
);

-- 向量索引（HNSW，适合高并发检索）
CREATE INDEX idx_memories_embedding ON agent_memories
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX idx_memories_user_type ON agent_memories(user_id, memory_type);
```

---

## 二、Redis 数据结构设计

```
# 会话记忆（L1）
Key:   session:{session_id}:messages
Type:  List（最近50条，LPUSH + LTRIM）
TTL:   2小时

# 交易记忆（L2）
Key:   trading_memory:{user_id}:{account_id}
Type:  Hash
TTL:   7天
Fields:
  recent_trades: JSON（最近10笔交易摘要）
  strategy_performance: JSON（各策略胜率统计）
  user_preferences: JSON（偏好设置缓存）

# 持仓缓存
Key:   position:{user_id}:{account_id}:{symbol}
Type:  Hash
TTL:   永久（手动管理）
Fields:
  side, quantity, avg_entry_price,
  unrealized_pnl, leverage, liquidation_price,
  last_synced_at

# 账户冻结状态
Key:   account:frozen:{user_id}:{account_id}
Type:  String（"1"）
TTL:   永久（手动解冻）

# RiskGuard心跳
Key:   riskguard:heartbeat
Type:  String（Unix timestamp）
TTL:   60秒（每10秒刷新）

# LLM预算追踪
Key:   llm:budget:{user_id}:daily
Type:  String（token计数）
TTL:   到当天23:59:59

# 订单队列
Key:   trading:orders:inbox
Type:  Stream（Redis Stream）
Group: order_consumers

# Agent任务队列
Key:   agent:tasks:{priority}  （priority: high/normal/low）
Type:  Stream（Redis Stream）
Group: agent_workers

# 回测任务队列
Key:   backtest:tasks
Type:  Stream（Redis Stream）
Group: backtest_workers

# 通知队列
Key:   notifications:{priority}  （p0/p1/p2）
Type:  Stream（Redis Stream）
Group: notification_workers
```

---

## 三、TimescaleDB 行情数据表

```sql
-- 启用 TimescaleDB 扩展
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- K线数据（超表）
CREATE TABLE klines (
    time        TIMESTAMPTZ NOT NULL,
    symbol      VARCHAR(32) NOT NULL,
    exchange    VARCHAR(8) NOT NULL,    -- 1m/5m/1h/1d
    open        DECIMAL(20,8) NOT NULL,
    high        DECIMAL(20,8) NOT NULL,
    low         DECIMAL(20,8) NOT NULL,
    close       DECIMAL(20,8) NOT NULL,
    volume      DECIMAL(20,8) NOT NULL,
    quote_volume DECIMAL(20,8),
    PRIMARY KEY (time, symbol, exchange)
);

-- 转换为超表（按时间自动分区）
SELECT create_hypertable('klines', 'time',
    chunk_time_interval => INTERVAL '1 week');

-- 创建索引
CREATE INDEX idx_klines_symbol_exchange ON klines(symbol, exchange, time DESC);

-- 开启压缩（超过7天的数据自动压缩）
ALTER TABLE klines SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'symbol,exchange'
);

SELECT add_compression_policy('klines', INTERVAL '7 days');

-- 数据保留策略（保留2年）
SELECT add_retention_policy('klines', INTERVAL '2 years');

-- 连续聚合视图（1m -> 1h，自动物化）
CREATE MATERIALIZED VIEW klines_1h
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', time) AS bucket,
    symbol, exchange,
    FIRST(open, time)   AS open,
    MAX(high)           AS high,
    MIN(low)            AS low,
    LAST(close, time)   AS close,
    SUM(volume)         AS volume
FROM klines
WHERE exchange = 'binance'
GROUP BY bucket, symbol, exchange
WITH NO DATA;

SELECT add_continuous_aggregate_policy('klines_1h',
    start_offset => INTERVAL '3 hours',
    end_offset   => INTERVAL '1 minute',
    schedule_interval => INTERVAL '1 minute'
);
```

---

## 四、数据库迁移策略

```
迁移工具：Django Migrations（应用层表）+ 手动SQL（TimescaleDB超表）

迁移规则：
1. 所有变更通过迁移文件管理，不可直接ALTER生产库
2. 新增列必须有DEFAULT值或允许NULL（零停机迁移）
3. 删除列分两步：①先停止写入，②下一个版本删除列
4. 索引使用 CREATE INDEX CONCURRENTLY（不锁表）
5. agent_audit_logs分区表：每月提前创建下月分区（Celery定时任务）

初始化顺序：
  1. 启用扩展（pgvector, timescaledb）
  2. 创建用户权限表
  3. 创建交易所账户表
  4. 创建策略相关表
  5. 创建订单表
  6. 创建风控表
  7. 创建审计日志表（含分区）
  8. 创建回测表
  9. 创建记忆表（向量）
  10. 创建超表（klines）
```

---

## 五、数据安全规范

```
加密：
  - API Key/Secret：Fernet对称加密，主密钥仅存环境变量
  - 用户密码：bcrypt（cost=12）
  - 传输：TLS 1.3强制

访问控制：
  - 每个微服务使用独立DB用户，最小权限原则
  - agent_worker：只能SELECT/INSERT特定表
  - risk_guard：只能READ risk_configs，WRITE risk_events/risk_configs
  - backtest_worker：只能READ klines，WRITE backtest_tasks/backtest_results

备份策略：
  - PostgreSQL：每日全量备份 + WAL连续归档（PITR）
  - Redis：RDB + AOF双持久化
  - 保留30天
```

---

*文档版本：v4 | 数据库Schema设计 | 基于v3综合架构*
