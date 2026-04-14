# Agent系统与Skill机制详细设计文档

> 设计师B | 版本：v1.0 | 基于 architecture_final.md (Final-R2)

---

## 目录

1. [BaseAgent抽象类](#一baseagent抽象类)
2. [Skill系统](#二skill系统)
3. [SupervisorAgent实现](#三supervisoragent实现)
4. [各子Agent详细设计](#四各子agent详细设计)
5. [AgentRegistry与路由](#五agentregistry与路由)
6. [消息协议](#六消息协议)
7. [分层记忆系统实现](#七分层记忆系统实现)
8. [Prompt版本管理](#八prompt版本管理)
9. [LLM降级机制](#九llm降级机制)

---

## 一、BaseAgent抽象类

### 1.0 AgentLifecycle 枚举

```python
# apps/agent/lifecycle.py
from enum import Enum


class AgentLifecycle(Enum):
    SPAWN = 'spawn'           # 按需创建，执行完毕即销毁
    PERSISTENT = 'persistent' # 常驻Agent，需要显式启动/关闭
```

```python
# apps/agent/base_agent.py
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from apps.skill.registry import SkillRegistry
from apps.skill.context import SkillContext
from apps.memory.memory_manager import MemoryManager
from apps.agent.lifecycle import AgentLifecycle

logger = logging.getLogger(__name__)


@dataclass
class AgentMessage:
    """Agent间通信消息结构"""
    msg_id: str
    sender: str
    receiver: str
    intent: str
    payload: dict = field(default_factory=dict)
    reply_to: Optional[str] = None


@dataclass
class AgentResult:
    success: bool
    data: Any = None
    error: Optional[str] = None
    skill_used: Optional[str] = None


class BaseAgent(ABC):
    """
    所有Agent的抽象基类。
    - SPAWN 类型：按需实例化，执行完毕后销毁（默认）。
    - PERSISTENT 类型：常驻运行，由 PersistentAgentManager 管理生命周期，
      需要用户显式启动和关闭，支持跨请求保持状态。
    - 通过SkillRegistry动态加载Skill，不硬编码流程。
    - 通过MemoryManager访问分层记忆（L1/L2/L3）。
    """

    agent_type: str = 'base'  # 子类覆盖
    allowed_skills: list[str] = []  # 该Agent允许加载的Skill名称
    lifecycle: AgentLifecycle = AgentLifecycle.SPAWN  # 子类可覆盖为PERSISTENT

    def __init__(self, user_id: str, session_id: str):
        self.user_id = user_id
        self.session_id = session_id
        self._skill_registry = SkillRegistry.get_instance()
        self._memory = MemoryManager(user_id=user_id, agent_type=self.agent_type)
        self._loaded_skills: dict[str, Any] = {}
        self._logger = logging.getLogger(f'{__name__}.{self.agent_type}')
        # PERSISTENT Agent 专用：内部运行状态
        self._running: bool = False
        self._task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------ #
    #  Skill加载/卸载                                                      #
    # ------------------------------------------------------------------ #

    def load_skill(self, skill_name: str) -> None:
        """按需加载Skill（懒加载）"""
        if skill_name in self._loaded_skills:
            return
        if skill_name not in self.allowed_skills:
            raise PermissionError(
                f'Agent {self.agent_type} not allowed to load skill: {skill_name}'
            )
        skill_cls = self._skill_registry.get(skill_name)
        self._loaded_skills[skill_name] = skill_cls(agent=self)
        self._logger.debug(f'Skill loaded: {skill_name}')

    def unload_skill(self, skill_name: str) -> None:
        self._loaded_skills.pop(skill_name, None)

    def get_skill(self, skill_name: str):
        if skill_name not in self._loaded_skills:
            self.load_skill(skill_name)
        return self._loaded_skills[skill_name]

    # ------------------------------------------------------------------ #
    #  执行入口                                                            #
    # ------------------------------------------------------------------ #

    async def execute(self, message: AgentMessage) -> AgentResult:
        """统一执行入口，子类实现 _handle 方法"""
        self._logger.info(
            f'[{self.agent_type}] execute intent={message.intent} '
            f'session={self.session_id}'
        )
        try:
            # 注入相关记忆
            memories = await self._memory.retrieve(query=message.intent)
            message.payload['_memories'] = memories

            result = await self._handle(message)

            # 重要决策点写入L2记忆
            if result.success and result.data:
                await self._memory.write_l2(
                    content=str(result.data),
                    memory_type='decision',
                    importance=2,
                )
            return result
        except Exception as e:
            self._logger.exception(f'Agent {self.agent_type} error: {e}')
            return AgentResult(success=False, error=str(e))

    @abstractmethod
    async def _handle(self, message: AgentMessage) -> AgentResult:
        """子类实现具体处理逻辑"""
        ...

    # ------------------------------------------------------------------ #
    #  PERSISTENT Agent 生命周期钩子（SPAWN类型无需覆盖）                  #
    # ------------------------------------------------------------------ #

    async def on_start(self) -> None:
        """
        PERSISTENT Agent 启动钩子。
        子类可在此初始化长期状态（如加载历史记忆、订阅事件流）。
        """
        self._running = True
        self._logger.info(f'[{self.agent_type}] started for user={self.user_id}')

    async def on_stop(self) -> None:
        """
        PERSISTENT Agent 停止钩子。
        子类可在此持久化状态、清理资源。
        """
        self._running = False
        self._logger.info(f'[{self.agent_type}] stopped for user={self.user_id}')

    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------ #
    #  工具方法                                                            #
    # ------------------------------------------------------------------ #

    def build_context(self, message: AgentMessage, **extra) -> SkillContext:
        return SkillContext(
            user_id=self.user_id,
            session_id=self.session_id,
            payload=message.payload,
            memories=message.payload.get('_memories', []),
            **extra,
        )
```

---

## 一-A、PersistentAgentManager（常驻Agent管理器）

```python
# apps/agent/persistent_manager.py
from __future__ import annotations

import json
import logging
from typing import Optional

import redis.asyncio as aioredis
from django.conf import settings

from apps.agent.base_agent import BaseAgent
from apps.agent.lifecycle import AgentLifecycle
from apps.agent.registry import AgentRegistry

logger = logging.getLogger(__name__)

PERSISTENT_REGISTRY_KEY = 'persistent_agents'  # Redis Hash: {user_id}:{agent_type} -> session_id


class PersistentAgentManager:
    """
    管理所有 PERSISTENT 类型 Agent 的生命周期。
    - 内存中维护运行中的实例（{user_id}:{agent_type} -> BaseAgent）
    - Redis 持久化运行状态，进程重启后可恢复
    - Supervisor 通过此管理器路由消息给常驻 Agent
    """

    _instance: 'PersistentAgentManager | None' = None
    # 内存索引：key = "{user_id}:{agent_type}"，value = BaseAgent实例
    _running: dict[str, BaseAgent] = {}

    @classmethod
    def get_instance(cls) -> 'PersistentAgentManager':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------ #
    #  生命周期控制                                                        #
    # ------------------------------------------------------------------ #

    async def start(self, agent_type: str, user_id: str, session_id: str) -> BaseAgent:
        """
        启动一个 PERSISTENT Agent。
        若已在运行，直接返回现有实例（幂等）。
        """
        key = self._key(user_id, agent_type)
        if key in self._running:
            logger.info(f'PersistentAgent already running: {key}')
            return self._running[key]

        agent_cls = AgentRegistry.get_class(agent_type)
        if agent_cls.lifecycle != AgentLifecycle.PERSISTENT:
            raise ValueError(f'Agent {agent_type} is not PERSISTENT lifecycle')

        agent = agent_cls(user_id=user_id, session_id=session_id)
        await agent.on_start()
        self._running[key] = agent

        # 持久化到 Redis，进程重启后 on_startup 可恢复
        await self._persist_state(user_id, agent_type, session_id, status='running')
        logger.info(f'PersistentAgent started: {key}')
        return agent

    async def stop(self, agent_type: str, user_id: str) -> None:
        """
        关闭一个 PERSISTENT Agent。
        """
        key = self._key(user_id, agent_type)
        agent = self._running.pop(key, None)
        if agent:
            await agent.on_stop()
        await self._remove_state(user_id, agent_type)
        logger.info(f'PersistentAgent stopped: {key}')

    def get(self, agent_type: str, user_id: str) -> Optional[BaseAgent]:
        """获取正在运行的 PERSISTENT Agent 实例，不存在返回 None。"""
        return self._running.get(self._key(user_id, agent_type))

    def is_running(self, agent_type: str, user_id: str) -> bool:
        return self._key(user_id, agent_type) in self._running

    def list_running(self, user_id: str) -> list[str]:
        """列出该用户当前所有运行中的 PERSISTENT Agent 类型。"""
        prefix = f'{user_id}:'
        return [
            k.removeprefix(prefix)
            for k in self._running
            if k.startswith(prefix)
        ]

    # ------------------------------------------------------------------ #
    #  进程重启恢复                                                        #
    # ------------------------------------------------------------------ #

    async def restore_on_startup(self) -> None:
        """
        Django on_ready 信号中调用，从 Redis 恢复所有 PERSISTENT Agent。
        """
        r =

---

## 二、Skill系统

### 2.1 BaseSkill抽象类

```python
# apps/skill/base_skill.py
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from apps.agent.base_agent import BaseAgent

from apps.skill.context import SkillContext


class BaseSkill(ABC):
    """
    所有Skill的抽象基类。
    Skill = 可复用的业务流程单元，由Agent动态加载并执行。
    Skill不持有状态，每次执行通过SkillContext传递上下文。
    """

    skill_name: str = 'base_skill'  # 子类覆盖，注册键
    description: str = ''           # 用于LLM路由时的语义描述

    def __init__(self, agent: 'BaseAgent'):
        self._agent = agent

    @abstractmethod
    async def run(self, ctx: SkillContext) -> Any:
        """Skill核心逻辑，必须是纯异步，不修改ctx"""
        ...

    async def validate(self, ctx: SkillContext) -> bool:
        """执行前校验（可选覆盖），返回False时跳过执行"""
        return True
```

### 2.2 SkillContext数据结构

```python
# apps/skill/context.py
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SkillContext:
    user_id: str
    session_id: str
    payload: dict = field(default_factory=dict)
    memories: list[dict] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.payload.get(key, default)
```

### 2.3 SkillRegistry

```python
# apps/skill/registry.py
from __future__ import annotations

from typing import Type
from apps.skill.base_skill import BaseSkill


class SkillRegistry:
    _instance: 'SkillRegistry | None' = None
    _skills: dict[str, Type[BaseSkill]] = {}

    @classmethod
    def get_instance(cls) -> 'SkillRegistry':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def register(self, skill_cls: Type[BaseSkill]) -> Type[BaseSkill]:
        """装饰器：@SkillRegistry.get_instance().register"""
        self._skills[skill_cls.skill_name] = skill_cls
        return skill_cls

    def get(self, skill_name: str) -> Type[BaseSkill]:
        if skill_name not in self._skills:
            raise KeyError(f'Skill not registered: {skill_name}')
        return self._skills[skill_name]

    def list_skills(self) -> list[str]:
        return list(self._skills.keys())


def register_skill(cls):
    """模块级装饰器，自动注册Skill"""
    SkillRegistry.get_instance().register(cls)
    return cls
```

### 2.4 具体Skill示例：AnalysisSkill

```python
# apps/skill/skills/analysis_skill.py
import json
from apps.skill.base_skill import BaseSkill
from apps.skill.context import SkillContext
from apps.skill.registry import register_skill
from apps.agent.llm_client import LLMClient


@register_skill
class AnalysisSkill(BaseSkill):
    skill_name = 'AnalysisSkill'
    description = '对指定交易品种进行技术面和基本面分析，输出分析报告'

    async def run(self, ctx: SkillContext) -> dict:
        symbol = ctx.get('symbol')
        timeframe = ctx.get('timeframe', '1h')
        memories_text = self._format_memories(ctx.memories)

        prompt = self._build_prompt(symbol, timeframe, memories_text)

        llm = LLMClient.get_instance()
        response = await llm.chat(
            system=self._get_system_prompt(),
            user=prompt,
            max_tokens=2000,
        )

        return {
            'symbol': symbol,
            'timeframe': timeframe,
            'analysis': response,
        }

    def _format_memories(self, memories: list[dict]) -> str:
        if not memories:
            return '（无历史记忆）'
        return '\n'.join(f'- {m["content"]}' for m in memories[:5])

    def _build_prompt(self, symbol: str, timeframe: str, memories: str) -> str:
        return (
            f'请对 {symbol} 在 {timeframe} 时间框架进行分析。\n'
            f'相关历史决策：\n{memories}\n'
            '请输出：趋势判断、支撑压力位、风险提示。'
        )

    def _get_system_prompt(self) -> str:
        from apps.agent.prompt_loader import PromptLoader
        return PromptLoader.load('analyst', version='v1')


@register_skill
class SignalSkill(BaseSkill):
    skill_name = 'SignalSkill'
    description = '基于分析结果生成交易信号（买入/卖出/观望）'

    async def run(self, ctx: SkillContext) -> dict:
        analysis = ctx.get('analysis_result', {})
        symbol = ctx.get('symbol')

        # 规则引擎优先（节省LLM Token）
        signal = self._rule_engine_signal(analysis)
        if signal is not None:
            return {'symbol': symbol, 'signal': signal, 'source': 'rule_engine'}

        # 规则引擎无法判断时调用LLM
        llm = LLMClient.get_instance()
        response = await llm.chat(
            system='你是专业交易信号生成器，只输出JSON格式信号。',
            user=f'分析结果：{json.dumps(analysis, ensure_ascii=False)}\n请生成交易信号。',
            max_tokens=200,
        )
        return {'symbol': symbol, 'signal': response, 'source': 'llm'}

    def _rule_engine_signal(self, analysis: dict) -> str | None:
        """规则引擎覆盖80%常规场景（ADR-003）"""
        trend = analysis.get('trend', '')
        if trend == 'strong_uptrend':
            return 'buy'
        if trend == 'strong_downtrend':
            return 'sell'
        return None  # 无法判断，交给LLM
```

### 2.5 StrategyGenSkill（策略代码生成 + 热加载）

```python
# apps/skill/skills/strategy_gen_skill.py
import importlib
import importlib.util
import sys
import tempfile
import os
from pathlib import Path

from apps.skill.base_skill import BaseSkill
from apps.skill.context import SkillContext
from apps.skill.registry import register_skill
from apps.agent.llm_client import LLMClient

STRATEGY_OUTPUT_DIR = Path('/tmp/generated_strategies')
STRATEGY_OUTPUT_DIR.mkdir(exist_ok=True)


@register_skill
class StrategyGenSkill(BaseSkill):
    """
    QuantEngineerAgent的核心Skill：
    1. LLM生成策略Python代码
    2. 安全沙箱校验
    3. 写入策略文件
    4. 通知交易框架热加载
    """
    skill_name = 'StrategyGenSkill'
    description = '根据用户需求生成量化交易策略代码，并热加载到交易框架'

    async def run(self, ctx: SkillContext) -> dict:
        requirements = ctx.get('strategy_requirements', '')
        strategy_name = ctx.get('strategy_name', 'custom_strategy')

        # 1. LLM生成策略代码
        llm = LLMClient.get_instance()
        code = await llm.chat(
            system=self._system_prompt(),
            user=f'需求：{requirements}\n请生成策略类代码，类名为 {strategy_name}。',
            max_tokens=3000,
        )

        # 2. 代码安全校验（简单白名单）
        self._validate_code(code)

        # 3. 写入文件
        strategy_file = STRATEGY_OUTPUT_DIR / f'{strategy_name}.py'
        strategy_file.write_text(code, encoding='utf-8')

        # 4. 动态导入（热加载）
        module = self._hot_load(strategy_name, strategy_file)
        strategy_cls = getattr(module, strategy_name)

        # 5. 注册到交易框架
        from apps.trading.executor import OrderExecutor
        executor = OrderExecutor.get_instance()
        if executor:
            executor.register_strategy(strategy_name, strategy_cls)

        return {
            'strategy_name': strategy_name,
            'file': str(strategy_file),
            'loaded': True,
        }

    def _validate_code(self, code: str) -> None:
        """拒绝危险操作"""
        forbidden = ['import os', 'import subprocess', 'exec(', 'eval(', '__import__']
        for f in forbidden:
            if f in code:
                raise ValueError(f'策略代码包含禁止操作: {f}')

    def _hot_load(self, name: str, path: Path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    def _system_prompt(self) -> str:
        return (
            '你是资深量化工程师，用Python生成策略类。'
            '策略类必须继承 BaseStrategy，实现 on_bar(bar) 方法。'
            '只输出纯Python代码，不包含任何markdown标记。'
        )
```

---

## 三、SupervisorAgent实现

```python
# apps/agent/supervisor.py
from __future__ import annotations

import uuid
import logging
from apps.agent.base_agent import BaseAgent, AgentMessage, AgentResult
from apps.agent.registry import AgentRegistry
from apps.agent.frame_manager import frame_manager, FrameType
from apps.agent.llm_client import LLMClient

logger = logging.getLogger(__name__)


INTENT_TO_AGENT = {
    # 分析类
    'analyze_market':     'AnalystAgent',
    'generate_signal':    'AnalystAgent',
    # 量化/策略类
    'generate_strategy':  'QuantEngineerAgent',
    'run_backtest':    'AnalystAgent',
    # 计划/复盘
    'create_plan':        'PlannerAgent',
    'review_trade':       'CoachAgent',
    'summarize_week':     'CoachAgent',
    # 风险咨询
    'assess_risk':        'RiskAdvisorAgent',
    # 框架控制
    'start_trading':      '__frame__',
    'stop_trading':       '__frame__',
    'start_monitor':      '__frame__',
    'stop_monitor':       '__frame__',
}


class SupervisorAgent(BaseAgent):
    """
    常驻Agent，负责：
    1. 解析用户意图
    2. 路由到子Agent
    3. 管理框架生命周期（懒加载）
    """
    agent_type = 'supervisor'
    allowed_skills = ['ChannelRouteSkill']

    async def _handle(self, message: AgentMessage) -> AgentResult:
        intent = message.intent

        # 框架生命周期控制
        if intent in ('start_trading', 'start_monitor'):
            return await self._handle_frame_start(intent)
        if intent in ('stop_trading', 'stop_monitor'):
            return await self._handle_frame_stop(intent)

        # 路由到子Agent
        agent_name = INTENT_TO_AGENT.get(intent)
        if not agent_name:
            agent_name = await self._infer_intent(message)  # LLM意图推断

        sub_agent = AgentRegistry.create(agent_name, self.user_id, self.session_id)
        return await sub_agent.execute(message)

    async def _handle_frame_start(self, intent: str) -> AgentResult:
        frame_map = {
            'start_trading': FrameType.TRADING,
            'start_monitor': FrameType.AUXILIARY,
        }
        frame = frame_map[intent]
        await frame_manager.start_frame(frame)
        return AgentResult(success=True, data={'frame': frame.value, 'status': 'started'})

    async def _handle_frame_stop(self, intent: str) -> AgentResult:
        frame_map = {
            'stop_trading': FrameType.TRADING,
            'stop_monitor': FrameType.AUXILIARY,
        }
        frame = frame_map[intent]
        await frame_manager.stop_frame(frame)
        return AgentResult(success=True, data={'frame': frame.value, 'status': 'stopped'})

    async def _infer_intent(self, message: AgentMessage) -> str:
        """LLM意图推断（规则无法覆盖时）"""
        llm = LLMClient.get_instance()
        skill_list = '\n'.join(
            f'- {k}: {v}' for k, v in INTENT_TO_AGENT.items()
        )
        response = await llm.chat(
            system='你是意图分类器，只输出意图键名，不输出任何其他内容。',
            user=f'用户消息：{message.payload.get("text", "")}\n可用意图：\n{skill_list}',
            max_tokens=20,
        )
        return response.strip()


---

## 四、各子Agent详细设计

### 4.1 AnalystAgent

```python
# apps/agent/agents/analyst_agent.py
from apps.agent.base_agent import BaseAgent, AgentMessage, AgentResult


class AnalystAgent(BaseAgent):
    agent_type = 'analyst'
    allowed_skills = ['AnalysisSkill', 'SignalSkill']

    async def _handle(self, message: AgentMessage) -> AgentResult:
        ctx = self.build_context(message)

        # 步骤1：市场分析
        analysis_skill = self.get_skill('AnalysisSkill')
        analysis_result = await analysis_skill.run(ctx)

        # 步骤2：信号生成
        ctx.payload['analysis_result'] = analysis_result
        signal_skill = self.get_skill('SignalSkill')
        signal_result = await signal_skill.run(ctx)

        return AgentResult(
            success=True,
            data={'analysis': analysis_result, 'signal': signal_result},
            skill_used='AnalysisSkill+SignalSkill',
        )
```

### 4.2 QuantEngineerAgent

```python
class QuantEngineerAgent(BaseAgent):
    agent_type = 'quant_engineer'
    allowed_skills = ['StrategyGenSkill', 'BacktestSkill']

    async def _handle(self, message: AgentMessage) -> AgentResult:
        intent = message.intent
        ctx = self.build_context(message)

        if intent == 'generate_strategy':
            skill = self.get_skill('StrategyGenSkill')
            result = await skill.run(ctx)
            return AgentResult(success=True, data=result, skill_used='StrategyGenSkill')

        if intent == 'run_backtest':
            # 确保回测框架已启动
            from apps.agent.frame_manager import frame_manager, FrameType
            await frame_manager.start_frame(FrameType.BACKTEST)

            skill = self.get_skill('BacktestSkill')
            result = await skill.run(ctx)
            return AgentResult(success=True, data=result, skill_used='BacktestSkill')

        return AgentResult(success=False, error=f'Unknown intent: {intent}')
```

### 4.3 PlannerAgent

```python
class PlannerAgent(BaseAgent):
    agent_type = 'planner'
    allowed_skills = ['PlanSkill', 'RiskAssessSkill']

    async def _handle(self, message: AgentMessage) -> AgentResult:
        ctx = self.build_context(message)

        # 先进行风险评估（咨询，非执行）
        risk_skill = self.get_skill('RiskAssessSkill')
        risk_result = await risk_skill.run(ctx)

        if not risk_result.get('approved', False):
            return AgentResult(
                success=False,
                error=f'风险评估不通过: {risk_result.get("reason", "")}',
            )

        # 生成交易计划
        ctx.payload['risk_assessment'] = risk_result
        plan_skill = self.get_skill('PlanSkill')
        plan_result = await plan_skill.run(ctx)

        return AgentResult(success=True, data=plan_result, skill_used='PlanSkill')
```

### 4.4 CoachAgent

```python
class CoachAgent(BaseAgent):
    agent_type = 'coach'
    allowed_skills = ['ReviewSkill', 'SummarySkill']

    async def _handle(self, message: AgentMessage) -> AgentResult:
        intent = message.intent
        ctx = self.build_context(message)

        skill_name = 'ReviewSkill' if intent == 'review_trade' else 'SummarySkill'
        skill = self.get_skill(skill_name)
        result = await skill.run(ctx)

        # 复盘结论写入L3持久记忆（importance=4，重要）
        await self._memory.write_l3(
            content=str(result),
            memory_type='decision',
            importance=4,
        )

        return AgentResult(success=True, data=result, skill_used=skill_name)
```

### 4.5 RiskAdvisorAgent

```python
class RiskAdvisorAgent(BaseAgent):
    """
    纯咨询型Agent，不执行任何交易操作。
    加载 RiskAssessSkill，仅返回风险建议。
    """
    agent_type = 'risk_advisor'
    allowed_skills = ['RiskAssessSkill']

    async def _handle(self, message: AgentMessage) -> AgentResult:
        ctx = self.build_context(message)
        skill = self.get_skill('RiskAssessSkill')
        result = await skill.run(ctx)
        return AgentResult(success=True, data=result, skill_used='RiskAssessSkill')
```

---

## 五、AgentRegistry与路由

```python
# apps/agent/registry.py
from __future__ import annotations
from typing import Type
from apps.agent.base_agent import BaseAgent


class AgentRegistry:
    _registry: dict[str, Type[BaseAgent]] = {}

    @classmethod
    def register(cls, agent_cls: Type[BaseAgent]) -> Type[BaseAgent]:
        cls._registry[agent_cls.agent_type] = agent_cls
        return agent_cls

    @classmethod
    def create(cls, agent_type: str, user_id: str, session_id: str) -> BaseAgent:
        if agent_type not in cls._registry:
            raise KeyError(f'Agent not registered: {agent_type}')
        return cls._registry[agent_type](user_id=user_id, session_id=session_id)

    @classmethod
    def list_agents(cls) -> list[str]:
        return list(cls._registry.keys())


def register_agent(cls):
    AgentRegistry.register(cls)
    return cls
```

---

## 六、消息协议

### 6.1 Redis Stream消息格式

```python
# agent:tasks Stream消息体
{
    'msg_id':    'uuid4',
    'sender':    'telegram_channel',     # 来源
    'intent':    'analyze_market',        # 意图键
    'user_id':   'uuid4',
    'session_id': 'uuid4',
    'payload':   '{...}',                # JSON字符串
    'ts':        '1700000000.000',        # 时间戳
}
```

### 6.2 WebSocket推送格式（前端）

```json
{
    "type": "agent_result",
    "session_id": "uuid4",
    "agent_type": "analyst",
    "data": { "analysis": "...", "signal": "buy" },
    "ts": 1700000000
}
```

### 6.3 Django Channels Consumer

```python
# apps/agent/consumers.py
import json
from channels.generic.websocket import AsyncWebsocketConsumer


class AgentConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        user = self.scope['user']
        if not user.is_authenticated:
            await self.close()
            return
        self.group_name = f'agent_{user.id}'
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def agent_result(self, event):
        """接收来自Celery任务的推送"""
        await self.send(text_data=json.dumps(event['data']))
```

---

## 七、分层记忆系统实现

```python
# apps/memory/memory_manager.py
from __future__ import annotations

import json
import logging
from typing import Optional

import redis.asyncio as aioredis
from django.conf import settings

logger = logging.getLogger(__name__)

L1_TTL = 2 * 3600    # 2小时
L2_TTL = 7 * 86400   # 7天


class MemoryManager:
    """
    分层记忆统一读写接口：
    L1 (Redis DB3, TTL=2h)  → 当前会话上下文
    L2 (Redis DB4, TTL=7d)  → 近期决策记忆
    L3 (pgvector)           → 长期语义记忆
    """

    def __init__(self, user_id: str, agent_type: str):
        self.user_id = user_id
        self.agent_type = agent_type

    def _l1_key(self, suffix='') -> str:
        return f'l1:{self.user_id}:{self.agent_type}:{suffix}'

    def _l2_key(self, suffix='') -> str:
        return f'l2:{self.user_id}:{self.agent_type}:{suffix}'

    async def _get_redis(self, db: int) -> aioredis.Redis:
        url = settings.REDIS_URL.rstrip('/')
        return await aioredis.from_url(f'{url}/{db}', decode_responses=True)

    # ------------------------------------------------------------------ #
    #  写入                                                               #
    # ------------------------------------------------------------------ #

    async def write_l1(self, key: str, value: dict, ttl: int = L1_TTL) -> None:
        r = await self._get_redis(db=3)
        await r.setex(self._l1_key(key), ttl, json.dumps(value, ensure_ascii=False))
        await r.aclose()

    async def write_l2(self, content: str, memory_type: str, importance: int = 2) -> None:
        r = await self._get_redis(db=4)
        import uuid, time
        key = self._l2_key(str(uuid.uuid4()))
        data = {'content': content, 'type': memory_type, 'importance': importance, 'ts': time.time()}
        await r.setex(key, L2_TTL, json.dumps(data, ensure_ascii=False))
        await r.aclose()

    async def write_l3(self, content: str, memory_type: str, importance: int) -> None:
        """异步写入pgvector（通过Celery任务）"""
        from apps.memory.tasks import store_l3_memory
        store_l3_memory.delay(
            user_id=self.user_id,
            agent_type=self.agent_type,
            content=content,
            memory_type=memory_type,
            importance=importance,
        )

    # ------------------------------------------------------------------ #
    #  检索                                                               #
    # ------------------------------------------------------------------ #

    async def retrieve(self, query: str, top_k: int = 5) -> list[dict]:
        """优先L1→L2→L3，合并返回top_k条"""
        results = []

        # L1快速查询
        r1 = await self._get_redis(db=3)
        keys = await r1.keys(self._l1_key('*'))
        for k in keys[:3]:
            raw = await r1.get(k)
            if raw:
                results.append(json.loads(raw))
        await r1.aclose()

        if len(results) >= top_k:
            return results[:top_k]

        # L3语义检索
        from apps.memory.l3_store import L3Store
        l3_results = await L3Store.search(query, self.user_id, top_k=top_k - len(results))
        results.extend(l3_results)

        return results[:top_k]
```

---

## 八、Prompt版本管理

```python
# apps/agent/prompt_loader.py
from pathlib import Path

PROMPT_BASE = Path(__file__).parent.parent.parent / 'prompts'


class PromptLoader:
    _cache: dict[str, str] = {}

    @classmethod
    def load(cls, name: str, version: str = 'v1') -> str:
        """
        加载指定版本的Prompt文件。
        变更必须新建版本目录（ADR-004），可追溯可回滚。
        """
        cache_key = f'{version}/{name}'
        if cache_key not in cls._cache:
            file_path = PROMPT_BASE / version / f'{name}.txt'
            if not file_path.exists():
                raise FileNotFoundError(f'Prompt not found: {file_path}')
            cls._cache[cache_key] = file_path.read_text(encoding='utf-8')
        return cls._cache[cache_key]

    @classmethod
    def clear_cache(cls) -> None:
        """测试或热更新时使用"""
        cls._cache.clear()
```

---

## 九、LLM降级机制

```python
# apps/agent/llm_client.py
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAY = 1.0  # 秒
FALLBACK_RESPONSE = '__RULE_ENGINE_FALLBACK__'


class LLMClient:
    """
    OpenAI API封装，支持：
    - 指数退避重试（最多3次）
    - 主模型失败时降级到备用模型
    - 规则引擎兜底（不可用时返回标记）
    """

    _instance: 'LLMClient | None' = None

    def __init__(self):
        self._primary_model = settings.OPENAI_CHAT_MODEL
        self._fallback_model = 'gpt-3.5-turbo'
        self._api_key = settings.OPENAI_API_KEY
        self._base_url = 'https://api.openai.com/v1/chat/completions'

    @classmethod
    def get_instance(cls) -> 'LLMClient':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def chat(self, system: str, user: str, max_tokens: int = 1000,
                   model: Optional[str] = None) -> str:
        model = model or self._primary_model
        for attempt in range(MAX_RETRIES):
            try:
                return await self._call_api(system, user, max_tokens, model)
            except Exception as e:
                logger.warning(f'LLM attempt {attempt+1} failed ({model}): {e}')
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_DELAY * (2 ** attempt))
                    # 第2次尝试切换备用模型
                    if attempt == 1:
                        model = self._fallback_model

        # 所有尝试失败，返回兜底标记
        logger.error('LLM全部重试失败，返回规则引擎兜底标记')
        return FALLBACK_RESPONSE

    async def _call_api(self, system: str, user: str, max_tokens: int, model: str) -> str:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                self._base_url,
                headers={'Authorization': f'Bearer {self._api_key}'},
                json={
                    'model': model,
                    'messages': [
                        {'role': 'system', 'content': system},
                        {'role': 'user', 'content': user},
                    ],
                    'max_tokens': max_tokens,
                    'temperature': 0.3,
                },
            )
            resp.raise_for_status()
            return resp.json()['choices'][0]['message']['content']


def is_fallback(response: str) -> bool:
    """判断LLM是否返回了兜底标记，Skill内部据此走规则引擎分支"""
    return response == FALLBACK_RESPONSE
```

---

*设计师B | agent_skill.md v1.0 | Agent系统与Skill机制详细设计完成*
