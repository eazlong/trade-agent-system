# 第5轮：实施路线图与工程规范

> 基于v3综合架构 + v4数据库Schema
> 将架构设计转化为可执行的开发计划

---

## 一、项目目录结构

```
trade_agent_sys/
├── backend/                          # Django主服务
│   ├── config/
│   │   ├── settings/
│   │   │   ├── base.py
│   │   │   ├── development.py
│   │   │   └── production.py
│   │   ├── urls.py
│   │   └── asgi.py                   # Django Channels入口
│   ├── apps/
│   │   ├── users/                    # 用户认证与权限
│   │   ├── accounts/                 # 交易所账户管理
│   │   ├── strategies/               # 策略管理
│   │   ├── trading/                  # 交易计划与订单
│   │   ├── backtest/                 # 回测任务管理
│   │   ├── notifications/            # 通知系统
│   │   └── audit/                    # 审计日志查询
│   ├── agents/                       # Agent系统
│   │   ├── base/
│   │   │   ├── agent.py              # BaseAgent抽象类
│   │   │   ├── memory.py             # 分层记忆管理
│   │   │   └── budget.py             # LLM Token预算
│   │   ├── supervisor/
│   │   │   └── supervisor_agent.py
│   │   ├── analyst/
│   │   │   └── analyst_agent.py
│   │   ├── quant/
│   │   │   └── quant_engineer_agent.py
│   │   ├── coach/
│   │   │   └── coach_agent.py
│   │   └── prompts/
│   │       ├── v1/
│   │       │   ├── analyst.py
│   │       │   ├── quant.py
│   │       │   └── coach.py
│   │       └── v2/                   # 新版本Prompt
│   ├── risk_guard/                   # 风控独立服务
│   │   ├── service.py                # gRPC服务主入口
│   │   ├── hard_limits.py            # 硬限制（不可配置）
│   │   ├── soft_limits.py            # 软限制（数据库配置）
│   │   ├── circuit_breaker.py        # 熔断逻辑
│   │   └── heartbeat.py              # 心跳维护
│   ├── trading_engine/               # 交易执行引擎
│   │   ├── consumer.py               # Redis Stream消费者
│   │   ├── router.py                 # 交易所路由
│   │   ├── adapters/
│   │   │   ├── base.py
│   │   │   ├── binance.py
│   │   │   └── okx.py
│   │   └── reconciler.py             # 持仓对账
│   ├── backtest_engine/              # 回测引擎
│   │   ├── runner.py                 # 回测主流程
│   │   ├── data_cursor.py            # 防前视偏差游标
│   │   ├── metrics.py                # 绩效指标计算
│   │   └── worker.py                 # Celery Worker
│   └── common/
│       ├── redis_client.py
│       ├── encryption.py             # API Key加密
│       └── exceptions.py
├── frontend/                         # React前端（后续阶段）
├── infra/
│   ├── docker-compose.yml
│   ├── docker-compose.prod.yml
│   └── k8s/
│       ├── deployments/
│       └── services/
├── scripts/
│   ├── init_db.sql                   # DB初始化脚本
│   └── create_partitions.py          # 创建审计日志月分区
└── tests/
    ├── unit/
    ├── integration/
    └── e2e/
```

---

## 二、Phase 1 详细任务分解（MVP，4周）

### Week 1：基础设施

```
[ ] Docker Compose配置
    - PostgreSQL 16 + pgvector
    - TimescaleDB
    - Redis 7（开启AOF）
    - Django开发服务器

[ ] Django基础配置
    - settings分环境配置
    - Django Channels + ASGI配置
    - JWT认证（djangorestframework-simplejwt）
    - CORS配置

[ ] 数据库迁移
    - 执行init_db.sql（扩展+超表）
    - Django migrations（用户、账户、风控配置表）

[ ] 用户认证API
    - POST /api/auth/register/
    - POST /api/auth/login/
    - POST /api/auth/refresh/
    - GET  /api/auth/me/
```

### Week 2：RiskGuard + 交易所账户

```
[ ] 交易所账户管理API
    - CRUD /api/accounts/
    - POST /api/accounts/{id}/verify/  （验证API Key有效性）
    - API Key加密存储（Fernet）

[ ] RiskGuard独立服务
    - hard_limits.py（硬编码，代码审查必须）
    - 心跳机制（10s写Redis）
    - 熔断状态管理
    - 基础校验：余额、仓位上限、冻结状态

[ ] 风控配置API
    - GET/PUT /api/risk/config/{account_id}/
    - 权限检查：只有trader/admin可修改
```

### Week 3：交易执行引擎

```
[ ] CCXT Binance适配器
    - 下单（market/limit）
    - 查询持仓
    - 查询订单状态
    -    - 取消订单

[ ] Redis Stream订单消费者
    - OrderConsumer（按priority消费）
    - 幂等性检查
    - 订单状态轮询（WebSocket优先，REST降级）
    - 部分成交处理（cancel_remainder策略）

[ ] 持仓缓存管理
    - 下单成功后更新Redis持仓
    - 定时对账（每5分钟与交易所同步）

[ ] 订单API
    - POST /api/orders/         （创建订单，入Redis Stream）
    - GET  /api/orders/         （订单历史）
    - GET  /api/orders/{id}/    （订单详情）
    - DELETE /api/orders/{id}/  （取消订单）
```

### Week 4：AnalystAgent + Telegram通知 + MVP集成

```
[ ] AnalystAgent
    - 接入GPT-4o（OpenAI API）
    - 基础对话：分析行情、建立交易计划
    - 输出结构化TradingPlan（JSON Schema校验）
    - 分层记忆L1（Redis会话记忆）
    - Token预算控制

[ ] SupervisorAgent
    - 接收Django WebSocket消息
    - 路由到AnalystAgent
    - 返回结果推送WebSocket

[ ] Telegram通知
    - P0告警：风控熔断、紧急平仓
    - P1告警：订单成交确认、风控预警
    - Bot指令：/status /freeze /unfreeze

[ ] MVP集成测试
    - 端到端：用户登录 → 配置账户 → 对话建立计划 → 下单 → 确认成交
    - RiskGuard：触发日亏损熔断场景
    - 通知：Telegram接收成交通知
```

---

## 三、Phase 2 任务概览（核心功能，4周）

```
Week 5-6：回测系统
  [ ] 历史行情数据入库（Binance API → TimescaleDB）
  [ ] DataCursor（防前视偏差）
  [ ] BacktestRunner（事件驱动回测）
  [ ] 绩效指标计算（Sharpe/Sortino/最大回撤等）
  [ ] Celery Worker（异步回测任务）
  [ ] QuantEngineerAgent（对话驱动回测参数）
  [ ] 回测结果可视化API

Week 7-8：多账户隔离 + 对账机制
  [ ] 多账户权限隔离（数据库行级安全）
  [ ] PostgreSQL RLS（Row Level Security）
  [ ] Reconciler定时对账（每5分钟）
  [ ] 差异告警（Telegram P1）
  [ ] OKX适配器
  [ ] 策略版本管理（快照 + 回滚API）
```

---

## 四、工程规范

### 4.1 API设计规范

```
基础URL：/api/v1/
认证：Bearer Token（JWT）
响应格式：
  成功：{"status": "ok", "data": {...}}
  错误：{"status": "error", "code": "RISK_FROZEN", "message": "账户已被风控冻结"}

错误码约定：
  AUTH_*     认证/授权错误
  RISK_*     风控拒绝
  ORDER_*    订单相关错误
  EXCHANGE_* 交易所API错误
  AGENT_*    Agent处理错误
```

### 4.2 测试要求

```
覆盖率要求：
  - risk_guard/：≥ 95%（风控核心，必须高覆盖）
  - trading_engine/：≥ 85%
  - agents/：≥ 70%（LLM调用需Mock）
  - 其余：≥ 60%

必须测试的场景：
  - RiskGuard硬限制不可绕过
  - 订单幂等性（同一request_id只执行一次）
  - 断网场景（交易所API超时）
  - 熔断场景（日亏损触发）
  - 前视偏差防护（回测DataCursor）
```

### 4.3 代码审查要求

```
必须双人审查的文件：
  - risk_guard/hard_limits.py
  - trading_engine/adapters/*.py
  - common/encryption.py

PR规范：
  - 不得直接推送main分支
  - 所有测试通过后才能合并
  - 风控相关变更需要额外标注 [RISK-CRITICAL]
```

### 4.4 部署规范

```yaml
# docker-compose.yml 核心服务
services:
  django:
    build: ./backend
    environment:
      - VAULT_MASTER_KEY=${VAULT_MASTER_KEY}
      - DATABASE_URL=${DATABASE_URL}
      - REDIS_URL=${REDIS_URL}
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
    depends_on: [postgres, redis]

  risk_guard:
    build: ./backend
    command: python manage.py run_risk_guard
    restart: always          # 必须always重启
    healthcheck:
      test: ["CMD", "python", "-c", "import redis; r=redis.Redis(); r.get('riskguard:heartbeat')"]
      interval: 15s
      timeout: 5s
      retries: 3

  order_consumer:
    build: ./backend
    command: python manage.py run_order_consumer
    restart: always

  celery_worker:
    build: ./backend
    command: celery -A config worker -Q backtest,notifications -c 4
    restart: on-failure

  postgres:
    image: timescale/timescaledb-ha:pg16-latest
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./scripts/init_db.sql:/docker-entrypoint-initdb.d/init.sql

  redis:
    image: redis:7-alpine
    command: redis-server --appendonly yes
    volumes:
      - redis_data:/data
```

---

## 五、关键决策记录（ADR）

### ADR-001：RiskGuard独立部署
- **决策**：RiskGuard作为独立进程，通过gRPC与主服务通信
- **原因**：Agent系统崩溃不影响风控能力，防止资金损失
- **后果**：增加一跳网络延迟（<1ms，可接受）

### ADR-002：交易所持仓数据为唯一真实来源
- **决策**：Redis持仓缓存仅用于加速读取，定时从交易所同步
- **原因**：网络闪断可能导致Redis与交易所数据不一致
- **后果**：每5分钟对账，差异立即告警并以交易所数据覆盖Redis

### ADR-003：LLM降级策略
- **决策**：LLM调用失败或超时时，降级到规则引擎（不终止交易流程）
- **原因**：OpenAI API不可用不应导致已有交易计划无法执行
- **后果**：规则引擎覆盖约80%常规场景，复杂分析场景需等待LLM恢复

### ADR-004：Prompt版本化管理
- **决策**：Prompt存储在代码文件中（prompts/v1/），变更必须新建版本
- **原因**：Prompt变更直接影响Agent行为，需要可追溯、可回滚
- **后果**：无法热更新Prompt，需要重新部署（可接受，换取稳定性）

---

## 六、风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| 交易所API限流 | 高 | 订单延迟 | 指数退避重试，限流监控告警 |
| LLM API不可用 | 中 | 功能降级 | 降级到规则引擎，备用模型（Claude）|
| Redis宕机 | 低 | 持仓数据丢失 | AOF持久化 + 从交易所恢复持仓 |
| DB连接池耗尽 | 中 | 请求超时 | 连接池监控，超时快速失败 |
| API Key泄露 | 低 | 资金损失 | Fernet加密 + 最小权限IP白名单 |

---

*文档版本：v5 | 实施路线图 | 完整工程规范*
