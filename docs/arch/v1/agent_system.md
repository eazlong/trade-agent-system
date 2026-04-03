# Agent 系统架构设计

> 版本：v1（第1轮）
> 负责架构师：架构师A
> 文档时间：2026-03-27

---

## 1. 设计目标与边界

### 1.1 设计目标

1. **可信任的协调层**：SupervisorAgent 作为唯一任务调度中心，所有 Agent 只接受 Supervisor 的指令，不直接通信
2. **强制风控**：RiskGuard 作为不可绕过的拦截层，优先级高于所有 Agent 指令
3. **可观测性**：每个 Agent 的决策链路全程记录，支持完整推理日志追溯
4. **可扩展性**：Skill 系统解耦能力与 Agent 实体，新 Agent 类型可快速接入
5. **容错性**：单个 Agent 崩溃不影响整体系统，LLM 超时有降级处理

### 1.2 系统边界

- **包含**：Agent 生命周期管理、消息路由、Skill 执行、记忆读写、RiskGuard 拦截
- **不包含**：具体交易所 API 调用（属于交易系统）、回测计算（属于回测系统）、前端 UI
- **集成点**：通过 Redis Stream 与回测系统交换数据；通过 Django REST API 暴露 Agent 状态

---

## 2. 核心模块图（ASCII）

```
┌─────────────────────────────────────────────────────────────────┐
│                        Agent 系统边界                            │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                   SupervisorAgent                         │   │
│  │                                                           │   │
│  │  ┌─────────────┐    ┌──────────────────────────────────┐ │   │
│  │  │ TaskPlanner │    │     ★ RiskGuard（强制拦截层）    │ │   │
│  │  │             │    │  - 单笔亏损阈值检查              │ │   │
│  │  │ 任务分解    │    │  - 日/周回撤熔断                │ │   │
│  │  │ 优先级排序  │    │  - 仓位上限硬限制               │ │   │
│  │  └──────┬──────┘    │  - 断网安全处置                 │ │   │
│  │         │           │  优先级 > 所有Agent指令          │ │   │
│  │         │           └──────────────┬───────────────────┘ │   │
│  │         │                          │ 拦截/放行            │   │
│  │         ▼                          ▼                      │   │
│  │  ┌─────────────┐    ┌─────────────────────────────────┐  │   │
│  │  │AgentRouter  │───▶│         Agent 消息总线           │  │   │
│  │  │             │    │      (Redis Stream)              │  │   │
│  │  │ 路由策略    │    └─────────────┬───────────────────┘  │   │
│  │  └─────────────┘                  │                       │   │
│  └───────────────────────────────────┼───────────────────────┘   │
│                                      │                            │
│          ┌───────────────────────────┼──────────────────┐        │
│          │                           │                  │        │
│          ▼                           ▼                  ▼        │
│  ┌───────────────┐        ┌─────────────────┐  ┌──────────────┐ │
│  │QuantEngineer  │        │  [未来]舆情Agent │  │[未来]链数据  │ │
│  │Agent          │        │                 │  │Agent         │ │
│  │               │        │                 │  │              │ │
│  │ Skill集合：   │        │  Skill集合：    │  │ Skill集合：  │ │
│  │ - backtest    │        │  - sentiment    │  │ - onchain    │ │
│  │ - ta_analyze  │        │  - news_fetch   │  │ - whale_track│ │
│  │ - signal_gen  │        │                 │  │              │ │
│  └───────┬───────┘        └─────────────────┘  └──────────────┘ │
│          │                                                        │
│          ▼                                                        │
│  ┌───────────────────────────────────────────────────────────┐   │
│  │                      Skill 执行层                          │   │
│  │  BacktestSkill │ TAAnalysisSkill │ SignalGenSkill │ ...    │   │
│  └───────────────────────────────────────────────────────────┘   │
│                                                                   │
│  ┌─────────────────────────┐  ┌─────────────────────────────┐   │
│  │     工作记忆层           │  │        长期记忆层            │   │
│  │  Redis（TTL=会话级）    │  │  PostgreSQL + pgvector      │   │
│  │  - 当前任务上下文        │  │  - 历史决策记录             │   │
│  │  - Agent执行状态        │  │  - 策略知识库               │   │
│  │  - 临时推理链            │  │  - 向量化交易记忆           │   │
│  └─────────────────────────┘  └─────────────────────────────┘   │
│                                                                   │
│  ┌───────────────────────────────────────────────────────────┐   │
│  │                     审计日志层                             │   │
│  │  DecisionAuditLog：记录每个Agent决策的完整推理链           │   │
│  └───────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. 接口定义

### 3.1 核心数据结构

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional
import uuid
from datetime import datetime

class AgentMessageType(Enum):
    TASK_REQUEST = "task_request"       # Supervisor -> Worker Agent
    TASK_RESULT = "task_result"         # Worker Agent -> Supervisor
    RISK_CHECK = "risk_check"           # 任意 -> RiskGuard
    RISK_BLOCK = "risk_block"           # RiskGuard -> Supervisor（拦截）
    RISK_PASS = "risk_pass"             # RiskGuard -> Supervisor（放行）
    HEARTBEAT = "heartbeat"             # Agent -> AgentRegistry
    ERROR = "error"                     # Agent -> Supervisor

@dataclass
class AgentMessage:
    """Agent间通信的标准消息格式"""
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    message_type: AgentMessageType = AgentMessageType.TASK_REQUEST
    sender_id: str = ""              # Agent标识符
    receiver_id: str = ""            # 目标Agent标识符
    session_id: str = ""             # 用户会话ID
    user_id: int = 0                 # Django用户ID
    payload: dict[str, Any] = field(default_factory=dict)
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))  # 追踪链路
    parent_trace_id: Optional[str] = None  # 父追踪ID（形成链路树）
    created_at: datetime = field(default_factory=datetime.utcnow)
    priority: int = 5                # 1=最高(RiskGuard)，10=最低

@dataclass
class RiskCheckRequest:
    """风控检查请求"""
    action_type: str          # "open_position" | "close_position" | "signal"
    symbol: str
    side: str                 # "long" | "short"
    quantity: float
    price: float
    user_id: int
    strategy_id: str
    agent_id: str

@dataclass
class RiskCheckResult:
    """风控检查结果"""
    allowed: bool
    reason: str
    triggered_rules: list[str]  # 触发的风控规则列表
    emergency_action: Optional[str] = None  # "close_all" | "freeze_account"

@dataclass
class SkillResult:
    """Skill执行结果"""
    skill_name: str
    success: bool
    data: Any
    error: Optional[str] = None
    execution_time_ms: int = 0
```

### 3.2 SupervisorAgent 接口

```python
class SupervisorAgent:
    async def dispatch_task(
        self,
        task_type: str,
        task_payload: dict,
        user_id: int,
        session_id: str,
        priority: int = 5
    ) -> str:
        """分发任务到合适的Worker Agent，返回trace_id"""
        ...

    async def collect_result(
        self,
        trace_id: str,
        timeout_seconds: int = 30
    ) -> AgentMessage:
        """等待并收集Worker Agent的执行结果"""
        ...

    async def handle_risk_event(
        self,
        risk_result: RiskCheckResult,
        original_message: AgentMessage
    ) -> None:
        """处理风控拦截事件，执行紧急措施"""
        ...
```

### 3.3 RiskGuard 接口

```python
class RiskGuard:
    """强制风控拦截层，优先级最高，不可绕过"""

    async def check(
        self,
        request: RiskCheckRequest
    ) -> RiskCheckResult:
        """同步风控检查（必须在任何交易动作前调用）"""
        ...

    async def update_rules(
        self,
        user_id: int,
        rules: dict
    ) -> None:
        """更新用户风控规则（需要管理员权限）"""
        ...

    async def emergency_stop(
        self,
        user_id: int,
        reason: str
    ) -> None:
        """紧急停止：冻结账户，触发平仓"""
        ...
```

### 3.4 Skill 基类接口

```python
from abc import ABC, abstractmethod

class BaseSkill(ABC):
    skill_name: str
    allowed_agents: list[str]  # 只有这些Agent可以调用此Skill

    @abstractmethod
    async def execute(
        self,
        caller_agent_id: str,
        params: dict
    ) -> SkillResult:
        """执行Skill，自动校验调用权限"""
        ...

    def _verify_permission(self, caller_agent_id: str) -> None:
        """验证调用权限，不在allowed_agents中则抛出异常"""
        if caller_agent_id not in self.allowed_agents:
            raise PermissionError(
                f"Agent '{caller_agent_id}' 无权调用 Skill '{self.skill_name}'"
            )
```

---

## 4. 数据流说明

### 4.1 正常任务流

```
用户对话请求
    │
    ▼
Django View（assistant/views.py）
    │  创建AgentMessage(TASK_REQUEST)
    ▼
Redis Stream: agent:supervisor:inbox
    │
    ▼
SupervisorAgent.dispatch_task()
    │  1. 解析任务类型
    │  2. 选择目标Worker Agent
    │  3. 如果涉及交易动作，先经过RiskGuard
    ▼
[RiskGuard.check()]  ──失败──▶ 返回RISK_BLOCK，记录审计日志，通知用户
    │
  通过
    │
    ▼
Redis Stream: agent:{worker_id}:inbox
    │
    ▼
Worker Agent（如QuantEngineerAgent）
    │  1. 读取工作记忆（Redis）
    │  2. 执行Skill集合
    │  3. 调用LLM（OpenAI）分析
    │  4. 写入推理日志
    ▼
Redis Stream: agent:supervisor:results
    │
    ▼
SupervisorAgent.collect_result()
    │  汇总结果
    ▼
Django WebSocket（Channels）推送给前端
    │
    ▼
写入长期记忆（PostgreSQL + pgvector）
```

### 4.2 风控熔断流

```
RiskGuard检测到日亏损超阈值
    │
    ▼
RiskGuard.emergency_stop()
    │  priority=1（最高优先级）
    ▼
向交易系统发送 EMERGENCY_CLOSE_ALL 指令
    │
    ▼
冻结用户账户（Redis标记 user:{id}:frozen = true）
    │
    ▼
通过通知系统发送紧急告警（Telegram）
    │
写入审计日志（DecisionAuditLog）
```

---

## 5. 与其他子系统集成点

### 5.1 与回测系统集成
BacktestSkill：allowed_agents=[quant_engineer_agent]，通过Redis Stream异步提交回测任务，等待结果（timeout=300s）

### 5.2 与交易系统集成
- 集成点：Redis Stream channel = trading:orders:inbox
- 消息格式：OrderRequest（由RiskGuard校验后转发）
- Agent不直接调用交易所API，必须经过RiskGuard

### 5.3 与Django后端集成
- Django Channels WebSocket Consumer 接收前端指令
- 通过SupervisorAgent.dispatch_task()分发到对应Agent
- Agent结果通过Redis PubSub推送回WebSocket

---

## 6. 技术选型

| 技术 | 选型 | 理由 |
|------|------|------|
| Agent框架 | CrewAI | 项目已有依赖，支持角色定义+任务流 |
| Agent间通信 | Redis Stream | 持久化、消费者组、回溯 |
| 工作记忆 | Redis（TTL=会话级） | 低延迟，自动过期 |
| 长期记忆 | PostgreSQL + pgvector | 已有实例，支持语义检索 |
| 审计日志 | PostgreSQL独立表 | 持久化+可查询 |

---

## 7. 故障处理

| 故障场景 | 检测 | 处理 |
|---------|------|------|
| Agent崩溃 | 心跳超时30s | Supervisor重启，任务重新入队 |
| LLM超时 | asyncio.wait_for 30s | 降级到规则引擎 |
| RiskGuard崩溃 | 独立心跳监控 | **立即冻结所有交易** |
| 断网 | WebSocket心跳丢失 | 触发本地预设止损单，禁止新开仓 |

---

*文档版本：v1 | 架构师A | 待第2轮专家评审*
