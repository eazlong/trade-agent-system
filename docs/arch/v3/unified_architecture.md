# 第3轮：综合改进架构设计

> 基于v1三份初稿 + v2专家评审结论
> 本文档为系统整体架构的权威设计，覆盖所有子系统及其交互

---

## 一、系统总体架构图

```
┌─────────────────────────────────────────────────────────────────────┐
│                         前端 / 客户端层                              │
│   React SPA  ←──WebSocket──→  Django Channels  ←──REST──→  Django   │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │ Redis Stream / PubSub
┌─────────────────────────────────▼───────────────────────────────────┐
│                         消息总线层（Redis）                           │
│   Stream: agent:tasks  │  Stream: trading:orders  │  PubSub: events  │
└────────┬──────────────────────────┬──────────────────────┬──────────┘
         │                          │                      │
┌────────▼──────────┐  ┌────────────▼───────────┐  ┌──────▼──────────┐
│   Agent服务集群    │  │  交易执行服务（独立）    │  │  RiskGuard服务  │
│                   │  │                        │  │  （最高优先级）  │
│ SupervisorAgent   │  │  OrderConsumer         │  │                 │
│ AnalystAgent      │  │  ExchangeRouter        │  │ 独立部署        │
│ QuantEngineerAgent│  │  CCXT适配器            │  │ 独立心跳监控    │
│ RiskAdvisorAgent  │  │  持仓对账Reconciler    │  │ 崩溃→全局熔断  │
│ CoachAgent        │  │                        │  │                 │
└────────┬──────────┘  └────────────┬───────────┘  └──────┬──────────┘
         │                          │                      │
┌────────▼──────────────────────────▼──────────────────────▼──────────┐
│                         数据层                                        │
│  PostgreSQL（主库）│ Redis（缓存/队列）│ pgvector（向量记忆）           │
│  TimescaleDB（行情）│ MinIO（回测数据）│                               │
└─────────────────────────────────────────────────────────────────────┘
         │
┌────────▼──────────────────────────────────────────────────────────┐
│                   外部服务层                                        │
│  Binance / OKX / Bybit（CCXT）│ OpenAI API │ Telegram Bot API     │
└───────────────────────────────────────────────────────────────────┘
```

---

## 二、核心设计原则（v2评审结论落地）

| 原则 | 实现方式 |
|------|----------|
| RiskGuard独立性 | 独立进程/容器，独立DB连接，不依赖Agent服务 |
| 防御性执行 | 任何服务异常状态下禁止开仓，优先保护已有仓位 |
| 数据一致性 | Redis为缓存，交易所API为真实来源，每5分钟对账 |
| 成本控制 | 规则引擎处理80%场景，LLM仅用于复杂分析 |
| 幂等性 | 所有订单操作以request_id做幂等键 |
| 可审计性 | 所有Agent决策链路完整记录至PostgreSQL |

---

## 三、Agent系统改进设计

### 3.1 框架选型调整

**决策：自研轻量级Agent基类 + LangGraph工作流**

```python
class BaseAgent(ABC):
    """所有Agent的基类，基于asyncio，不依赖重型框架"""

    def __init__(self, agent_id: str, llm_client: LLMClient,
                 memory: MemoryService, redis: Redis):
        self.agent_id = agent_id
        self.llm = llm_client
        self.memory = memory
        self.redis = redis
        self._budget_tracker = TokenBudgetTracker(agent_id)  # 成本控制

    @abstractmethod
    async def process_task(self, task: AgentTask) -> AgentResult: ...

    async def call_llm(self, messages: list, schema: dict) -> dict:
        """所有LLM调用必须经过此方法（预算控制 + 结构化输出）"""
        await self._budget_tracker.check_budget()  # 超出预算则降级到规则引擎
        response = await self.llm.chat(
            messages=messages,
            response_format={"type": "json_schema", "schema": schema}
        )
        return self._validate_output(response, schema)
```

### 3.2 分层记忆设计

```
记忆层级：

 L1 - 会话记忆（Redis，TTL=2h）
      存储：当前对话上下文、临时计算结果
      访问：O(1)，最近5轮对话

 L2 - 交易记忆（Redis，TTL=7d）
      存储：近期交易记录、策略表现摘要、用户偏好
      访问：O(1)，按user_id+account_id索引

 L3 - 长期记忆（PostgreSQL + pgvector）
      存储：历史策略分析、市场规律、用户复盘笔记
      访问：向量相似度检索，topK=5，embedding=text-embedding-3-small
      写入时机：每次交易会话结束后异步写入
```

### 3.3 Prompt管理

```python
# prompts/v1/analyst_agent.py
ANALYST_SYSTEM_PROMPT = """
你是一位专业的加密货币技术分析师。
当前时间：{current_time}
用户账户：{account_id}

你的职责：
1. 分析用户提供的K线图和技术指标
2. 识别关键支撑/阻力位
3. 给出明确的多空倾向（必须是JSON格式）

输出格式严格遵循以下JSON Schema（不得输出任何其他内容）：
{output_schema}
"""

# Prompt版本通过文件路径管理，变更需要新建版本文件
# prompts/v2/analyst_agent.py  ← 新版本
```

### 3.4 LLM成本控制

```python
class TokenBudgetTracker:
    DAILY_LIMIT_PER_USER = 100_000   # tokens/天
    MONTHLY_LIMIT_PER_USER = 2_000_000

    async def check_budget(self):
        daily_used = await self.redis.get(f"llm:budget:{self.user_id}:daily")
        if int(daily_used or 0) >= self.DAILY_LIMIT_PER_USER:
            raise BudgetExceededError("日LLM预算已用尽，降级到规则引擎")
```

---

## 四、RiskGuard独立服务设计

### 4.1 部署架构

```
独立进程：risk_guard_service.py
独立端口：内部gRPC 50051（不对外暴露）
独立DB连接：只读访问用户配置，可写访问风控状态表
独立心跳：每10秒向Redis写入 riskguard:heartbeat，超过30s未更新触发全局熔断
```

### 4.2 硬限制（代码层，不可配置）

```python
class HardLimits:
    """这些值只能通过代码发布变更，不可通过数据库配置绕过"""
    MAX_SINGLE_TRADE_LOSS_PCT = 0.05      # 单笔最大亏损5%账户净值
    MAX_TOTAL_LEVERAGE = 20               # 总杠杆上限20x
    MAX_DRAWDOWN_FORCE_CLOSE = 0.50       # 总回撤50%强制清仓
    MAX_POSITION_PCT_PER_SYMBOL = 0.30    # 单标的最大仓位30%
```

### 4.3 软限制（数据库配置，管理员可调整）

```sql
CREATE TABLE risk_config (
    user_id         UUID NOT NULL,
    account_id      UUID NOT NULL,
    daily_loss_alert_pct    DECIMAL(5,4) DEFAULT 0.02,   -- 日亏损预警2%
    consecutive_loss_alert  INT DEFAULT 3,               -- 连续亏损3次预警
    position_warn_pct       DECIMAL(5,4) DEFAULT 0.20,   -- 仓位预警20%
    updated_by      UUID,   -- 审计：谁修改了配置
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (user_id, account_id)
);
```

### 4.4 断网/宕机预案（完整版）

| 故障场景 | 检测方式 | 自动处理 | 人工处理 |
|---------|---------|---------|----------|
| 交易所API断线 | WebSocket心跳丢失 | 禁止新开仓；依赖交易所端预置OCO止损单 | 手动检查仓位 |
| Django服务崩溃 | K8s健康检查 | 自动重启；RiskGuard继续独立运行 | 检查重启原因 |
| Agent服务崩溃 | Redis心跳超时 | Supervisor重启；任务重新入队 | 检查LLM调用日志 |
| RiskGuard崩溃 | 独立心跳监控 | **立即触发全局熔断（交易所适配器硬开关）** | 优先恢复RiskGuard |
| Redis宕机 | 连接超时 | 降级模式：从交易所实时查询持仓；禁止新开仓 | 恢复Redis |
| PostgreSQL宕机 | 连接超时 | 只读缓存模式；禁止配置变更；继续执行已有止损 | 恢复DB |
| 服务器断电 | — | 交易所端OCO订单生效 | 重启服务后对账 |

---

## 五、持仓对账机制（Reconciliation）

```python
class PositionReconciler:
    """每5分钟从交易所同步真实持仓，修正Redis缓存"""

    async def reconcile(self, user_id: str, account_id: str):
        # 1. 从交易所获取真实持仓
        real_positions = await self.exchange.fetch_positions()

        # 2. 从Redis读取缓存持仓
        cached_positions = await self.redis.hgetall(f"position:{user_id}:{account_id}:*")

        # 3. 对比差异
        discrepancies = self._compare(real_positions, cached_positions)

        # 4. 修正Redis缓存
        for symbol, diff in discrepancies.items():
            await self.redis.hset(f"position:{user_id}:{account_id}:{symbol}", diff)
            await self._log_discrepancy(user_id, account_id, symbol, diff)

        # 5. 差异超阈值时触发告警
        if len(discrepancies) > 0:
            await self.notifier.send_alert(f"持仓对账发现{len(discrepancies)}个差异，已自动修正")
```

---

## 六、回测系统改进

### 6.1 前视偏差防护

```python
class SafeDataCursor:
    """防止回测使用未来数据"""

    def __init__(self, data: pd.DataFrame):
        self._data = data
        self._current_index = 0

    def advance(self):
        self._current_index += 1

    def get_current_bar(self) -> pd.Series:
        return self._data.iloc[self._current_index]

    def get_history(self, lookback: int) -> pd.DataFrame:
        """只返回当前bar及之前的数据"""
        start = max(0, self._current_index - lookback)
        return self._data.iloc[start:self._current_index + 1]  # 不包含未来

    # 禁止直接访问原始DataFrame
    @property
    def raw_data(self):
        raise ForbiddenAccessError("禁止直接访问原始数据，使用get_history()方法")
```

### 6.2 策略版本管理

```
策略存储结构：
  strategies/
    {strategy_id}/
      v1/
        strategy.py       # 策略代码快照
        config.json       # 参数配置
        backtest_results/ # 回测结果（不可变）
      v2/
        ...
      current -> v2/      # 软链接指向当前版本
```

### 6.3 部分成交处理策略

```python
class PartialFillPolicy(Enum):
    CANCEL_REMAINDER = "cancel_remainder"   # 取消剩余（默认，控制滑点）
    WAIT_COMPLETE = "wait_complete"         # 等待完全成交（流动性差时风险高）
    MARKET_REMAINDER = "market_remainder"   # 超时后市价成交剩余

# 默认策略：CANCEL_REMAINDER，超时30秒
# 用户可在账户设置中配置，但WAIT_COMPLETE需要额外确认
```

---

## 七、用户权限与多账户隔离

```
权限模型（RBAC）：
  OBSERVER   - 只读：查看信号、持仓、历史
  TRADER     - 读写：执行交易、管理策略、配置风控软限制
  ADMIN      - 全权：管理用户、修改系统配置、查看所有审计日志

数据隔离：
  - 所有查询必须包含 WHERE user_id = :current_user_id
  - Django中间件：每个请求注入 request.trading_scope（user_id + 允许的account_ids）
  - Agent任务上下文：每个AgentTask携带 user_scope，Agent不可越界访问

API Key加密：
  - 主密钥：VAULT_MASTER_KEY（环境变量，不入库）
  - 每个用户的API Key用主密钥派生的子密钥加密（HKDF派生）
  - 数据库只存储加密后的字节串，不存储明文
```

---

## 八、通知系统设计

```
通知优先级与渠道：

 P0 - 紧急（立即发送，不可忽略）：
      渠道：Telegram Bot（主）+ 短信（备）
      场景：RiskGuard熔断、强制平仓、API Key失效

 P1 - 重要（5分钟内发送）：
      渠道：Telegram Bot
      场景：日亏损预警、连续亏损告警、持仓对账差异

 P2 - 信息（非紧急，可聚合）：
      渠道：Telegram Bot（每小时聚合推送）
      场景：入场信号、回测完成、策略更新

降级策略：
  Telegram失败 → 重试3次（指数退避）→ 写入DB待用户下次登录时展示
  短信失败 → 记录日志 → 人工处理
```

---

## 九、技术栈最终确认

| 组件 | 技术选型 | 版本要求 |
|------|---------|----------|
| Web框架 | Django + DRF + Channels | Django 5.x |
| Agent框架 | 自研BaseAgent + LangGraph | LangGraph 0.2+ |
| 任务队列 | Redis Stream + Celery | Redis 7.x |
| 数据库 | PostgreSQL + pgvector | PG 16+ |
| 时序数据 | TimescaleDB（PG扩展） | TS 2.x |
| 交易所接入 | CCXT | 最新版 |
| 向量检索 | pgvector | 0.7+ |
| 部署 | Docker Compose（开发）/ K8s（生产） | 最新版 |
| LLM | OpenAI GPT-4o（主）/ Claude 3.5 Sonnet（备）| API调用 |
| 向量Embedding | text-embedding-3-small | OpenAI API |
| 通知 | Telegram Bot API | python-telegram-bot |

---

## 十、开发阶段规划

```
Phase 1（MVP，4周）：
  - Django后端基础结构
  - RiskGuard独立服务（硬限制先上）
  - 单交易所（Binance）基础交易执行
  - AnalystAgent（对话分析）
  - Telegram通知（P0级别）

Phase 2（核心功能，4周）：
  - 回测系统（含前视偏差防护）
  - QuantEngineerAgent
  - 持仓对账Reconciler
  - 多账户隔离

Phase 3（智能化，4周）：
  - 分层记忆系统
  - CoachAgent（复盘分析）
  - 策略版本管理
  - LLM成本控制

Phase 4（生产就绪，2周）：
  - K8s部署配置
  - 完整审计日志
  - 压力测试
  - 安全审计
```

---

*文档版本：v3 | 综合架构 | 基于v1初稿 + v2专家评审*
