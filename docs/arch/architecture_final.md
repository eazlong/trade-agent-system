# Trade Agent System — 最终架构文档

> 版本：Final-R1 | 综合v1~v5全部设计与评审结论，并根据需求文档修正4处架构偏差
> 生成方式：三位架构师并行设计 → 专家交叉评审 → 综合改进 → DB Schema → 实施路线图 → 需求对齐修正

> **R1修正说明（基于需求.md §架构要求）：**
> 1. Agent框架为主框架，交易/辅助/回测框架均为Agent的服务（非平行微服务）
> 2. 交易框架与辅助框架预置但懒加载，Agent按需启动（不常驻）
> 3. RiskGuard仅在实盘/订单监控时按需启动，非常驻独立进程
> 4. 所有流程定义为Skill，Agent加载Skill执行；策略代码由QuantEngineerAgent的Skill生成并被交易框架动态加载

---

## 目录

1. [系统总体架构](#一系统总体架构)
2. [核心设计原则](#二核心设计原则)
3. [Agent系统](#三agent系统)
4. [RiskGuard独立服务](#四riskguard独立服务)
5. [交易执行系统](#五交易执行系统)
6. [回测系统](#六回测系统)
7. [分层记忆系统](#七分层记忆系统)
8. [数据库Schema](#八数据库schema)
9. [消息总线设计](#九消息总线设计)
10. [通知与监控](#十通知与监控)
11. [技术栈](#十一技术栈)
12. [项目目录结构](#十二项目目录结构)
13. [实施路线图](#十三实施路线图)
14. [关键决策记录（ADR）](#十四关键决策记录adr)
15. [风险与缓解矩阵](#十五风险与缓解矩阵)

---

## 一、系统总体架构

> **核心设计**：Agent框架是唯一主框架，所有其他框架（交易/辅助/回测）均为Agent的服务。
> 交易框架与辅助框架预置在系统中但初始化不启动，由Agent按需启动。

```
┌─────────────────────────────────────────────────────────────────────┐
│                         前端 / 客户端层                              │
│   React SPA  ←──WebSocket──→  Django Channels  ←──REST──→  Django   │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │
┌─────────────────────────────────▼───────────────────────────────────┐
│               ★ 主框架：Agent管理框架（常驻）                         │
│                                                                     │
│  SupervisorAgent（主控）                                             │
│    ├── 用户意图解析 → 路由到子Agent                                   │
│    ├── Skill加载器（动态加载/卸载Skill）                               │
│    └── 框架生命周期管理（按需启动/停止子框架）                           │
│                                                                     │
│  子Agent（按需实例化，执行完可销毁）：                                  │
│    AnalystAgent      → 加载 AnalysisSkill / SignalSkill             │
│    QuantEngineerAgent → 加载 StrategyGenSkill / BacktestSkill       │
│    CoachAgent        → 加载 PlanSkill / ReviewSkill / SummarySkill  │
│    RiskAdvisorAgent  → 加载 RiskAssessSkill（咨询，非执行）           │
└──────────────────┬──────────────────────────────────────────────────┘
                   │ Agent按需调用（启动/停止）
    ┌──────────────┼──────────────────┐
    │              │                  │
┌───▼────────┐ ┌───▼────────┐ ┌──────▼──────────────────────┐
│ 交易框架    │ │ 辅助框架    │ │ 回测框架                     │
│（懒加载）   │ │（懒加载）   │ │（懒加载）                    │
│            │ │            │ │                             │
│预置不启动   │ │预置不启动   │ │预置不启动                    │
│实盘/测试盘  │ │订单监控时   │ │用户触发回测时启动             │
│时由Agent   │ │由Agent启动  │ │                             │
│启动        │ │            │ │                             │
│            │ │ + RiskGuard│ │                             │
│ + RiskGuard│ │（随辅助框架 │ │                             │
│（随交易框架 │ │  一同启动） │ │                             │
│  一同启动） │ │            │ │                             │
└───┬────────┘ └───┬────────┘ └──────┬──────────────────────┘
    │              │                  │
┌───▼──────────────▼──────────────────▼──────────────────────┐
│                         数据层                               │
│  PostgreSQL │ Redis │ pgvector │ TimescaleDB │ MinIO         │
└─────────────────────────────────────────────────────────────┘
    │
┌───▼────────────────────────────────────────────────────────┐
│  外部服务：CCXT交易所 │ OpenAI API │ Telegram Bot API        │
└────────────────────────────────────────────────────────────┘
```

---

## 二、核心设计原则

### 2.0 架构核心原则（需求对齐，R1修正）

| 原则 | 说明 |
|------|------|
| **Agent为主框架** | Agent管理框架是系统唯一主框架，所有其他框架是Agent的服务 |
| **懒加载** | 交易框架、辅助框架、回测框架预置在系统中，初始化不启动，Agent按需启动/停止 |
| **RiskGuard按需启动** | RiskGuard不常驻，仅在启动实盘/测试盘/订单监控时随交易框架或辅助框架一同启动 |
| **Skill是流程载体** | 所有业务流程定义为Skill，Agent动态加载Skill执行；QuantEngineerAgent的Skill生成策略代码并被交易框架热加载 |

### 2.1 RiskGuard优先原则（P0）
- 所有资金操作必须经过RiskGuard审批
- RiskGuard随交易/辅助框架启动，随框架停止而停止
- RiskGuard进程崩溃时，立即触发全局熔断（交易所适配器层硬编码开关）

### 2.2 防御性执行原则
- 宁可误判不执行，也不能在异常状态下执行交易
- 断网预案：交易所端预置止损单，网络中断不影响已有保护
- LLM不可用时降级到规则引擎（不终止交易流程）

### 2.3 数据一致性原则
- Redis仓位缓存仅为加速读取，交易所持仓为唯一真实来源
- 每5分钟从交易所同步持仓，差异立即告警并以交易所数据覆盖
- 每个用户独立部署一个实例，无需跨用户权限控制

### 2.4 成本意识原则
- LLM调用需Token预算控制，规则引擎覆盖80%常规场景
- Prompt代码化版本管理，变更必须新建版本文件，可追溯可回滚

---

## 三、Agent系统

### 3.1 Agent角色定义

| Agent | 职责 | 类型 | LLM依赖 |
|-------|------|------|---------|
| SupervisorAgent | 用户意图解析、路由子Agent、框架生命周期管理 | 常驻 | 高 |
| AnalystAgent | 市场分析、技术指标解读、信号识别 | 按需 | 高 |
| QuantEngineerAgent | 策略代码生成（Skill）、回测触发、绩效分析 | 按需 | 中 |
| RiskAdvisorAgent | 风险评估建议（咨询，非执行） | 按需 | 中 |
| CoachAgent | 交易计划、复盘总结、行为辅助 | 按需 | 高 |

> 初始内置：SupervisorAgent + QuantEngineerAgent。其他团队（舆情/链数据/量化研究等）可由用户通过对话创建。

### 3.1.1 Skill系统（流程载体）

> **所有业务流程定义为Skill，Agent加载Skill执行。** Agent本身只负责意图理解和路由，具体执行逻辑全在Skill中。

```
Skill分类：

  AnalysisSkill        → AnalystAgent加载：指标计算、图表解读
  SignalSkill          → AnalystAgent加载：信号扫描、提醒推送
  StrategyGenSkill     → QuantEngineerAgent加载：生成策略代码 → 交易框架热加载
  BacktestSkill        → QuantEngineerAgent加载：触发回测引擎、解读结果
  PlanSkill            → CoachAgent加载：起草交易计划、生成入场checklist
  ReviewSkill          → CoachAgent加载：入场时机审核
  SummarySkill         → CoachAgent加载：交易结束后复盘总结
  RiskAssessSkill      → RiskAdvisorAgent加载：风险评估建议（只读）
  OrderMonitorSkill    → CoachAgent加载：触发辅助框架+RiskGuard启动
```

```python
# Skill接口定义
class BaseSkill:
    name: str
    description: str

    async def execute(self, context: SkillContext) -> SkillResult:
        raise NotImplementedError

# Agent动态加载Skill
class BaseAgent:
    def load_skill(self, skill: BaseSkill) -> None: ...
    def unload_skill(self, skill_name: str) -> None: ...
    async def run(self, intent: str, context: dict) -> AgentResult:
        skill = self._route_to_skill(intent)
        return await skill.execute(context)
```

### 3.1.2 Channel系统（用户交互入口）

> Agent不直接面向前端，所有用户输入通过 **Channel适配器** 统一接入 SupervisorAgent。
> Channel负责：接收消息 → 格式标准化 → 写入 agent:tasks → 接收结果 → 回复用户。

```
用户（Telegram / Web / 未来更多）
        │
        ▼
  ChannelAdapter（抽象接口）
        │
        ├── TelegramChannel（Phase 1）
        │       ├── 接收消息（Bot长轮询 / Webhook）
        │       ├── 解析 /start, /help, 自然语言
        │       ├── 推送回复、图表（图片）、告警
        │       └── Inline Keyboard 快捷操作
        │
        └── WebChannel（Phase 2，React SPA）

  ChannelAdapter → SupervisorAgent（统一入口）
```

**BaseChannel 接口：**

```python
class BaseChannel:
    name: str  # 'telegram' | 'web'

    async def start(self) -> None:
        """启动监听"""
        raise NotImplementedError

    async def stop(self) -> None:
        raise NotImplementedError

    async def send_text(self, text: str) -> None:
        raise NotImplementedError

    async def send_image(self, image_bytes: bytes, caption: str = '') -> None:
        raise NotImplementedError

    async def send_alert(self, level: str, message: str) -> None:
        """level: P0 / P1 / P2"""
        raise NotImplementedError
```

**TelegramChannel 设计（Phase 1）：**

```python
class TelegramChannel(BaseChannel):
    """
    - 使用 python-telegram-bot 20.x（原生 asyncio）
    - Webhook 模式（生产）/ 长轮询（开发）
    - 支持 Markdown 格式回复
    - 支持 InlineKeyboard 快捷操作（如：确认下单、查看持仓）
    - 图表以图片方式发送（matplotlib → bytes）
    """

    COMMANDS = {
        '/start':    '初始化会话，介绍系统功能',
        '/status':   '查看当前持仓与运行状态',
        '/stop':     '停止交易/辅助框架',
        '/plan':     '查看/创建交易计划',
        '/backtest': '触发回测',
        '/help':     '帮助信息',
    }
    # 自然语言消息直接转发给 SupervisorAgent 处理
```

**Channel → Agent 消息流：**

```
Telegram消息
  → TelegramChannel.on_message()
  → 标准化为 ChannelMessage(channel='telegram', text=..., user_id='local')
  → SupervisorAgent.handle(message)
  → 路由到对应Skill
  → AgentResult
  → TelegramChannel.send_text() / send_image()
```

**Skill新增：**

```
  ChannelRouteSkill → SupervisorAgent加载：解析Channel消息意图，路由到正确Agent/Skill
```

---

### 3.2 消息协议

```python
# Agent任务消息（写入 Redis Stream: agent:tasks）
{
    "task_id": "uuid",
    "user_id": "uuid",
    "agent_type": "analyst",        # analyst / quant / coach
    "priority": 1,                  # 1=高 2=普通 3=低
    "payload": {
        "intent": "analyze_market",
        "context": {"symbol": "BTC/USDT", ...}
    },
    "created_at": "ISO8601",
    "timeout_ms": 30000
}
```

### 3.3 框架选型

- **核心路径**：自研 `BaseAgent` 抽象类 + asyncio（轻量、可控）
- **工作流编排**：自研 BaseAgent + Skill系统（不依赖第三方框架，轻量、可控）
- **不使用**：LangGraph（状态机过重）、CrewAI（异步不成熟）

### 3.4 Agent输出校验

所有Agent输出交易指令前，必须经过：
1. **Schema校验**：Pydantic模型强校验（数量、价格、方向字段类型）
2. **合理性检查**：数量/价格范围校验（如单笔BTC数量上限）
3. **RiskGuard审批**：通过gRPC调用独立RiskGuard服务

### 3.5 LLM Token预算

```python
# 每用户每日Token预算（可配置）
DAILY_TOKEN_BUDGET = {
    "analyst": 100_000,
    "quant": 50_000,
    "coach": 30_000,
}
# 超预算后降级到规则引擎，不中断服务
```

---

## 四、RiskGuard服务（按需启动）

### 4.1 生命周期

> RiskGuard **不常驻**，仅在以下场景由Agent（通过OrderMonitorSkill或StrategyGenSkill）显式启动：
> - 用户启动实盘交易
> - 用户启动测试盘（Paper Trading）
> - 用户启动订单监控（辅助框架）

```
用户意图 → SupervisorAgent
  → 判断需要实盘/测试盘/订单监控
  → 调用 FrameManager.start('trading' | 'assist')
      → 启动交易/辅助框架进程
      → 同步启动 RiskGuard 进程
      → 注册心跳监控

用户停止或会话结束
  → FrameManager.stop()
      → 熔断检查（有无未平仓持仓）
      → 停止 RiskGuard 进程
      → 停止交易/辅助框架进程
```

### 4.2 部署要求

- **独立进程**，不与Agent服务共享进程空间
- 通过 **gRPC** 与主服务通信（延迟 <1ms，可接受）
- 独立心跳监控（每10秒），心跳中断 → 全局熔断

### 4.2 硬限制（代码层，不可绕过）

```python
# hard_limits.py — 不可通过配置或API修改
MAX_SINGLE_LOSS_PCT = 0.05       # 单笔亏损上限 = 账户净值5%
MAX_LEVERAGE = 20                 # 总仓位杠杆上限
FORCE_LIQUIDATION_THRESHOLD = 0.5 # 净值跌破初始值50%强制清仓
MIN_FREE_MARGIN_PCT = 0.10        # 最低可用保证金比例
```

### 4.3 软限制（数据库配置，管理员可调整）

```sql
-- risk_configs 表
daily_loss_warning_pct    -- 日亏损预警阈值
consecutive_loss_alert    -- 连续亏损告警次数
position_suggestion_limit -- 仓位建议上限
```

### 4.4 熔断流程

```
触发条件：
  ① 硬限制被触碰
  ② RiskGuard进程心跳中断
  ③ 交易所API连续失败 ≥ 3次
  ④ 持仓对账差异超过阈值

熔断动作（按顺序）：
  1. 停止所有新订单提交
  2. 取消所有挂单
  3. 可选：市价平仓所有持仓（由配置决定）
  4. 写入 risk_events 表，发送P0告警（Telegram + 短信）
  5. 人工确认后才能解除熔断
```

### 4.5 断网预案

```
建仓同时：在交易所端预置止损单（不依赖本地服务）
网络中断时：交易所止损单仍有效，资金得到保护
恢复连接后：从交易所拉取最新持仓，对账校正Redis缓存
```

---

## 五、交易执行系统（懒加载框架）

### 5.0 框架生命周期

> 交易框架**预置但不启动**，由 `FrameManager`（SupervisorAgent控制）按需启动/停止。

```python
class FrameManager:
    """由SupervisorAgent调用，管理各框架的生命周期"""

    async def start_trading_frame(self, mode: str) -> None:
        """mode: 'live' | 'paper'"""
        await self._start_data_feed()       # 启动WebSocket实时数据
        await self._start_risk_guard()          # 启动RiskGuard进程
        await self._start_order_consumer()      # 启动订单消费者

    async def stop_trading_frame(self) -> None:
        await self._check_open_positions()      # 有持仓则警告
        await self._stop_order_consumer()
        await self._stop_risk_guard()
        await self._stop_data_feed()

    async def start_assist_frame(self) -> None:
        """辅助框架：订单监控、信号提醒等"""
        await self._start_data_feed()
        await self._start_risk_guard()          # 订单监控也需要RiskGuard
        await self._start_signal_monitor()
```

### 5.1 订单流程

```
Agent决策（QuantEngineerAgent或SupervisorAgent）
  → 写入 Redis Stream: trading:orders
  → OrderConsumer消费（仅在交易框架启动时运行）
  → RiskGuard gRPC审批
  → ExchangeRouter路由
  → CCXT适配器执行
  → 订单状态回写PostgreSQL
  → WebSocket推送前端
```

### 5.1.1 策略热加载

```
QuantEngineerAgent
  → 加载 StrategyGenSkill
  → Skill生成策略Python代码
  → 写入 strategies/{user_id}/{strategy_id}.py
  → StrategyLoader.hot_reload(strategy_id)
      → importlib.reload() 动态加载新策略
      → 注册到 EventBus（接收行情事件）
      → 旧策略实例销毁
```

### 5.2 订单幂等性

```python
# 每个订单携带 request_id（UUID），交易所适配器去重
# 网络重试不会导致重复下单
request_id = str(uuid4())  # 客户端生成，全局唯一
```

### 5.3 部分成交处理策略

| 场景 | 处理方式 |
|------|----------|
| 限价单部分成交，价格仍在区间 | 继续等待，最长等待时间可配置 |
| 超时未完全成交 | 取消剩余，以市价补足（可关闭此行为）|
| 止损单部分成交 | 立即市价成交剩余（资金安全优先）|

### 5.4 持仓对账（Reconciler）

```
频率：每5分钟
来源：从交易所API拉取真实持仓
对比：与Redis缓存持仓做diff
差异处理：
  - 差异 < 0.1%：忽略（精度误差）
  - 差异 0.1%~5%：以交易所数据覆盖Redis，写入日志
  - 差异 > 5%：触发P0告警，暂停新订单，人工介入
```

### 5.5 CCXT适配器

```python
class BaseExchangeAdapter:
    async def place_order(self, order: OrderRequest) -> OrderResult: ...
    async def cancel_order(self, order_id: str) -> bool: ...
    async def get_positions(self) -> list[Position]: ...
    async def get_balance(self) -> Balance: ...
    async def place_stop_loss(self, stop: StopLossRequest) -> str: ...

# 支持交易所：Binance / OKX / Bybit（通过CCXT统一接口）
# 数据标准化层：统一不同交易所的ticker格式差异
```

---

## 六、回测系统

### 6.1 架构概览

```
BacktestRequest
  → Celery异步任务（隔离用户请求）
  → UnifiedBacktestEngine
      ├── DataCursor（防前视偏差）
      ├── StrategyExecutor（策略逻辑）
      ├── FeeModel（手续费+动态滑点）
      ├── PortfolioTracker（持仓记录）
      └── PerformanceAnalyzer（绩效指标）
  → BacktestResult → PostgreSQL存储
```

### 6.2 防前视偏差（Look-ahead Bias）

```python
class DataCursor:
    """严格时间对齐的数据游标，每个bar只能访问当前及之前的数据"""
    def __init__(self, data: pd.DataFrame):
        self._data = data
        self._cursor = 0

    def current_bar(self) -> pd.Series:
        return self._data.iloc[self._cursor]

    def history(self, n: int) -> pd.DataFrame:
        """只能查看cursor之前的n条数据"""
        start = max(0, self._cursor - n)
        return self._data.iloc[start:self._cursor + 1]

    def advance(self):
        self._cursor += 1

    # 严禁提供 lookahead() 或未来数据访问接口
```

### 6.3 动态滑点模型

```python
# 滑点 = f(订单大小, 市场深度, 时间段)
def calc_slippage(order_size_usdt: float, avg_volume_24h: float,
                  time_of_day: int) -> float:
    base_bps = 5  # 基础5bps
    size_ratio = order_size_usdt / (avg_volume_24h * 0.01)  # 占日成交量比例
    time_factor = 1.5 if time_of_day in [0, 1, 22, 23] else 1.0  # 低流动性时段
    return base_bps * (1 + size_ratio) * time_factor
```

### 6.4 策略版本管理

- 策略代码存储在 Git 仓库中，`strategies/` 目录
- 回测结果记录 `git_commit_hash`，与策略代码版本强绑定
- 禁止在数据库中存储策略代码快照（以Git为唯一来源）

### 6.5 绩效指标

| 指标 | 计算方式 |
|------|----------|
| 夏普比率 | 年化收益/年化波动率 |
| 最大回撤 | 峰值到谷底最大跌幅 |
| 盈亏比 | 平均盈利/平均亏损 |
| 胜率 | 盈利交易数/总交易数 |
| Calmar比率 | 年化收益/最大回撤 |
| 蒙特卡洛P5 | Bootstrap抽样，P5分位数收益 |

---

## 七、分层记忆系统

```
┌────────────────────────────────────────────────┐
│  L1 工作记忆（Redis，TTL=2小时）                 │
│  当前会话上下文、最近对话轮次                      │
├────────────────────────────────────────────────┤
│  L2 交易记忆（Redis，TTL=7天）                   │
│  近期交易结果、策略执行状态、跨会话短期记忆          │
├────────────────────────────────────────────────┤
│  L3 长期记忆（pgvector + PostgreSQL）            │
│  历史决策摘要、用户偏好模式、重要事件               │
└────────────────────────────────────────────────┘
```

### 7.1 记忆写入策略

- **L1**：每次对话轮次自动写入，会话结束自动过期
- **L2**：每笔交易完成后写入，重要决策点强制写入
- **L3**：每日凌晨批量归档L2中的重要记忆，向量化后存入pgvector

### 7.2 记忆检索

```python
# 检索topK相关记忆（L3）
async def retrieve_memory(query: str, user_id: str, top_k: int = 5):
    embedding = await embed(query)  # OpenAI text-embedding-3-small
    results = await pgvector_search(embedding, user_id, top_k)
    return results  # top_k=5 平衡相关性与噪声
```

---

## 八、数据库Schema

### 8.1 用户与权限

> 单实例部署，每个用户独立部署一套系统，无需多用户隔离与权限管理。
> `users` 表仅用于本地登录验证，无 role 字段。

```sql
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           VARCHAR(255) UNIQUE NOT NULL,
    username        VARCHAR(64) UNIQUE NOT NULL,
    password_hash   VARCHAR(255) NOT NULL,  -- bcrypt
    is_frozen       BOOLEAN NOT NULL DEFAULT false,  -- 风控冻结
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login_at   TIMESTAMPTZ
);
```

### 8.2 交易所账户

```sql
CREATE TABLE exchange_accounts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    exchange        VARCHAR(32) NOT NULL,  -- binance / okx / bybit
    label           VARCHAR(64),
    api_key_enc     BYTEA NOT NULL,        -- Fernet加密
    api_secret_enc  BYTEA NOT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_exchange_accounts_user ON exchange_accounts(user_id);
```

### 8.3 策略

```sql
CREATE TABLE strategies (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id),
    name            VARCHAR(128) NOT NULL,
    description     TEXT,
    git_commit_hash VARCHAR(40),           -- 策略代码版本
    parameters      JSONB NOT NULL DEFAULT '{}',
    is_active       BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### 8.4 订单

```sql
CREATE TABLE orders (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id      UUID UNIQUE NOT NULL,  -- 幂等键
    user_id         UUID NOT NULL REFERENCES users(id),
    exchange_account_id UUID NOT NULL REFERENCES exchange_accounts(id),
    strategy_id     UUID REFERENCES strategies(id),
    symbol          VARCHAR(32) NOT NULL,
    side            VARCHAR(8) NOT NULL CHECK (side IN ('buy', 'sell')),
    order_type      VARCHAR(16) NOT NULL,  -- market / limit / stop
    qty             NUMERIC(20, 8) NOT NULL,
    price           NUMERIC(20, 8),        -- NULL for market orders
    status          VARCHAR(16) NOT NULL DEFAULT 'pending',
    filled_qty      NUMERIC(20, 8) NOT NULL DEFAULT 0,
    avg_fill_price  NUMERIC(20, 8),
    fee_usdt        NUMERIC(20, 8),
    exchange_order_id VARCHAR(64),
    risk_approved   BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_orders_user ON orders(user_id, created_at DESC);
CREATE INDEX idx_orders_status ON orders(status) WHERE status IN ('pending', 'open', 'partial');
```

### 8.5 风控事件

```sql
CREATE TABLE risk_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID REFERENCES users(id),
    event_type      VARCHAR(32) NOT NULL,  -- hard_limit / circuit_breaker / reconcile_diff
    severity        VARCHAR(8) NOT NULL CHECK (severity IN ('P0', 'P1', 'P2')),
    description     TEXT NOT NULL,
    metadata        JSONB,
    resolved_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
) PARTITION BY RANGE (created_at);  -- 月分区
```

### 8.6 Agent审计日志

```sql
CREATE TABLE agent_audit_logs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id),
    agent_type      VARCHAR(32) NOT NULL,
    task_id         UUID NOT NULL,
    input_summary   TEXT,
    output_summary  TEXT,
    llm_model       VARCHAR(64),
    token_used      INTEGER,
    duration_ms     INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
) PARTITION BY RANGE (created_at);  -- 月分区
```

### 8.7 回测结果

```sql
CREATE TABLE backtest_results (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id),
    strategy_id         UUID REFERENCES strategies(id),
    git_commit_hash     VARCHAR(40),
    symbol              VARCHAR(32) NOT NULL,
    timeframe           VARCHAR(8) NOT NULL,
    start_date          DATE NOT NULL,
    end_date            DATE NOT NULL,
    initial_capital     NUMERIC(20, 2) NOT NULL,
    final_capital       NUMERIC(20, 2),
    total_return_pct    NUMERIC(8, 4),
    sharpe_ratio        NUMERIC(8, 4),
    max_drawdown_pct    NUMERIC(8, 4),
    win_rate            NUMERIC(8, 4),
    total_trades        INTEGER,
    monte_carlo_p5      NUMERIC(8, 4),
    parameters          JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### 8.8 长期记忆（pgvector）

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE agent_memory (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id),
    agent_type  VARCHAR(32) NOT NULL,
    content     TEXT NOT NULL,
    embedding   vector(1536),  -- OpenAI text-embedding-3-small
    memory_type VARCHAR(16) NOT NULL CHECK (memory_type IN ('decision', 'preference', 'event')),
    importance  SMALLINT NOT NULL DEFAULT 1 CHECK (importance BETWEEN 1 AND 5),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_agent_memory_vector ON agent_memory USING ivfflat (embedding vector_cosine_ops);
CREATE INDEX idx_agent_memory_user ON agent_memory(user_id, agent_type);
```

### 8.9 K线时序数据（TimescaleDB）

```sql
CREATE TABLE klines (
    time        TIMESTAMPTZ NOT NULL,
    exchange    VARCHAR(32) NOT NULL,
    symbol      VARCHAR(32) NOT NULL,
    timeframe   VARCHAR(8) NOT NULL,
    open        NUMERIC(20, 8) NOT NULL,
    high        NUMERIC(20, 8) NOT NULL,
    low         NUMERIC(20, 8) NOT NULL,
    close       NUMERIC(20, 8) NOT NULL,
    volume      NUMERIC(30, 8) NOT NULL
);
SELECT create_hypertable('klines', 'time', chunk_time_interval => INTERVAL '7 days');
-- 连续聚合：自动生成1h聚合视图
```

---

## 九、消息总线设计

```
Redis Stream键名规范：

agent:tasks          — Agent任务队列（SupervisorAgent消费）
trading:orders       — 订单队列（OrderConsumer消费）
trading:positions    — 持仓更新事件（前端WebSocket订阅）
risk:events          — 风控事件广播
system:heartbeat     — 各服务心跳
```

### 消费者组

```
agent:tasks     → consumer_group: agents     → SupervisorAgent（轮询分配）
trading:orders  → consumer_group: executor   → OrderConsumer（顺序执行）
risk:events     → consumer_group: monitor    → AlertService（广播）
```

### 消息重试策略

```
消费失败 → PEL（Pending Entry List）保留消息
重试间隔：1s / 5s / 30s（指数退避）
超过3次 → 写入 Dead Letter Stream: {stream}:dlq
DLQ监控：每5分钟扫描，告警人工处理
```

---

## 十、通知与监控

### 10.1 告警等级

| 等级 | 触发场景 | 通知方式 |
|------|----------|----------|
| P0 | 熔断触发、硬限制触碰、RiskGuard崩溃 | Telegram即时 + 短信 |
| P1 | 持仓对账差异>5%、API Key异常 | Telegram即时 |
| P2 | 日亏损预警、连续亏损告警 | Telegram每小时汇总 |

### 10.2 Telegram通知格式

```
🔴 [P0] 熔断触发
用户: alice@example.com
时间: 2025-01-01 10:30:00 UTC
原因: 日亏损达到账户净值8%（限制5%）
当前持仓: 3个
操作: 已暂停所有新订单
处理: 请登录系统确认后手动解除
```

### 10.3 健康检查端点

```
GET /health          — 服务存活检查
GET /health/ready    — 依赖就绪检查（DB、Redis、交易所API）
GET /metrics         — Prometheus指标
```

---

## 十一、技术栈

| 层次 | 技术选型 | 版本要求 |
|------|----------|----------|
| 后端框架 | Django + Django REST Framework | Django 5.x |
| 异步 | Django Channels + asyncio | Channels 4.x |
| Agent框架 | 自研 BaseAgent + Skill系统 | 无第三方框架依赖 |
| LLM | OpenAI GPT-4o（主）/ Claude（备） | API调用 |
| 向量数据库 | pgvector（PostgreSQL扩展） | 0.7+ |
| 时序数据库 | TimescaleDB | 2.x |
| 缓存/队列 | Redis Stack（Stream + TimeSeries） | 7.x |
| 交易所接口 | CCXT | 4.x |
| 异步任务 | Celery + Redis Broker | Celery 5.x |
| 风控通信 | gRPC | grpcio 1.x |
| 加密 | cryptography（Fernet） | — |
| 监控 | Prometheus + Grafana | — |
| 通知 | python-telegram-bot | 20.x |
| 容器化 | Docker + Docker Compose | — |
| CI/CD | GitHub Actions | — |

---

## 十二、项目目录结构

```
trade_agent_sys/
├── apps/
│   ├── users/              # 用户认证、权限
│   ├── exchange/           # 交易所账户、CCXT适配器
│   ├── trading/            # 订单、持仓、对账
│   ├── agents/             # Agent系统核心
│   │   ├── base.py         # BaseAgent抽象类
│   │   ├── supervisor.py
│   │   ├── analyst.py
│   │   ├── quant.py
│   │   ├── coach.py
│   │   ├── channels/       # Channel适配器
│   │   │   ├── base.py     # BaseChannel抽象接口
│   │   │   └── telegram.py # TelegramChannel（Phase 1）
│   │   ├── skills/         # Skill定义
│   │   │   ├── analysis.py
│   │   │   ├── signal.py
│   │   │   ├── strategy_gen.py
│   │   │   ├── backtest.py
│   │   │   ├── plan.py
│   │   │   ├── review.py
│   │   │   ├── summary.py
│   │   │   ├── risk_assess.py
│   │   │   ├── order_monitor.py
│   │   │   └── channel_route.py
│   │   └── memory/         # 分层记忆系统
│   ├── risk/               # RiskGuard独立服务
│   │   ├── guard.py        # 主服务
│   │   ├── hard_limits.py  # 硬限制（不可修改）
│   │   └── grpc_server.py
│   ├── backtest/           # 回测引擎
│   │   ├── engine.py
│   │   ├── cursor.py       # DataCursor（防前视偏差）
│   │   ├── fee_model.py
│   │   └── performance.py
│   └── notifications/      # 告警通知
├── prompts/
│   ├── v1/                 # Prompt版本文件（只增不改）
│   │   ├── analyst_system.txt
│   │   ├── quant_system.txt
│   │   └── coach_system.txt
│   └── v2/                 # 新版本新目录
├── strategies/             # 策略代码（Git版本管理）
├── config/
│   ├── settings/
│   │   ├── base.py
│   │   ├── development.py
│   │   └── production.py
│   └── grpc/               # gRPC proto文件
├── tests/
│   ├── unit/
│   ├── integration/        # 必须使用真实DB（不允许Mock DB）
│   └── e2e/
├── docs/arch/              # 架构文档
├── docker-compose.yml
└── manage.py
```

---

## 十三、实施路线图

### Phase 1（第1~4周）— 核心基础设施

**Week 1：环境与数据层**
- [ ] Docker Compose配置（PostgreSQL + TimescaleDB + Redis Stack）
- [ ] 数据库Schema迁移（users / exchange_accounts / orders / risk_events）
- [ ] CCXT适配器：Binance现货基础功能（下单、查单、持仓）
- [ ] 用户认证API（注册/登录/JWT）

**Week 2：RiskGuard独立服务**
- [ ] gRPC proto定义（ApproveOrder / TriggerCircuitBreaker / HealthCheck）
- [ ] 硬限制实现（hard_limits.py，代码审查必须2人以上）
- [ ] 心跳监控（10秒间隔，中断自动熔断）
- [ ] 单元测试覆盖率 ≥ 95%

**Week 3：OrderConsumer + 持仓对账**
- [ ] Redis Stream消费者（OrderConsumer）
- [ ] RiskGuard gRPC调用集成
- [ ] 持仓Reconciler（5分钟定时任务）
- [ ] 订单幂等性测试

**Week 4：Agent基础框架 + Telegram Channel**
- [ ] BaseAgent抽象类 + Skill加载机制（自研，无第三方框架）
- [ ] BaseChannel抽象接口 + TelegramChannel实现
- [ ] SupervisorAgent任务路由 + ChannelRouteSkill
- [ ] Telegram Bot基础命令（/start /status /help）+ 自然语言转发
- [ ] Token预算控制机制
- [ ] Prompt v1版本文件

### Phase 2（第5~8周）— 功能完善

- 回测引擎（DataCursor + 动态滑点 + 蒙特卡洛）
- QuantEngineerAgent + CoachAgent
- 分层记忆系统（L1/L2/L3全链路）
- WebSocket实时推送
- Telegram告警通知

### Phase 3（第9~12周）— 生产就绪

- 多交易所支持（OKX / Bybit）
- Prometheus + Grafana监控看板
- 压力测试 + 故障注入测试
- 安全审计（API Key加密、SQL注入、权限越界）
- 灰度发布流程

---

## 十四、关键决策记录（ADR）

### ADR-001：RiskGuard必须独立进程（gRPC通信）
- **决策**：RiskGuard作为独立进程部署，通过gRPC与主服务通信
- **原因**：Agent进程崩溃不应影响风控能力；进程级隔离比线程更可靠
- **后果**：增加gRPC通信延迟（<1ms，可接受）；需要维护独立进程的健康监控

### ADR-002：交易所持仓为唯一真实来源
- **决策**：Redis持仓缓存仅加速读取，每5分钟从交易所同步真实持仓
- **原因**：网络中断/服务重启可能导致Redis数据陈旧，资金操作必须基于真实数据
- **后果**：每5分钟有短暂数据延迟窗口（可接受）；需要对账差异处理逻辑

### ADR-003：LLM不可用时降级到规则引擎
- **决策**：OpenAI API不可用时，自动降级到规则引擎，不中断已有交易计划
- **原因**：API不可用是高频事件；规则引擎可覆盖80%常规场景
- **后果**：降级期间Agent分析质量下降（可接受，安全性优先）

### ADR-004：Prompt代码化版本管理
- **决策**：Prompt存储在代码文件中（prompts/v{n}/），变更必须新建版本目录
- **原因**：Prompt变更直接影响Agent行为，需可追溯、可回滚
- **后果**：无法热更新Prompt，需重新部署（可接受，换取稳定性）

### ADR-005：Agent框架自研，不依赖第三方
- **决策**：放弃LangGraph/CrewAI，自研 BaseAgent + Skill加载机制
- **原因**：LangGraph状态机过重，不符合本系统「流程即Skill」的设计哲学；CrewAI异步支持不成熟；自研可完全控制Agent生命周期和Skill动态加载
- **后果**：需自行实现任务路由、消息协议、Skill注册/发现；无第三方社区支持，但维护成本可控

### ADR-006：单实例部署，每用户独立
- **决策**：系统设计为单用户单实例，不支持多租户
- **原因**：避免多用户隔离的复杂性（权限、数据隔离、资源争抢）；每用户独立实例更安全，资金风险完全隔离
- **后果**：多用户场景需运维层面部署多套实例（可接受）

---

## 十五、风险与缓解矩阵

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| 交易所API限流 | 高 | 订单延迟 | 指数退避重试，限流监控告警 |
| LLM API不可用 | 中 | 功能降级 | 降级到规则引擎，备用模型（Claude）|
| Redis宕机 | 低 | 持仓数据丢失 | AOF持久化 + 从交易所恢复持仓 |
| DB连接池耗尽 | 中 | 请求超时 | 连接池监控，超时快速失败 |
| API Key泄露 | 低 | 资金损失 | Fernet加密 + 最小权限 + IP白名单 |
| 前视偏差进入回测 | 中 | 策略虚假盈利 | DataCursor强制时间对齐 + 单元测试 |
| 持仓对账差异 | 低 | 风控数据错误 | 5分钟定时对账，差异>5%立即告警 |
| RiskGuard进程崩溃 | 低 | 失去风控保护 | 心跳监控，崩溃触发全局熔断 |

---

*文档版本：Final-R2 | 综合v1~v5 | 三架构师 + 三专家评审 | R1修正：懒加载+按需RiskGuard+Skill系统 | R2修正：单实例部署+自研Agent框架*
