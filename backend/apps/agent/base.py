from __future__ import annotations
import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
import uuid

# 延迟导入避免循环依赖
def _get_tool_registry():
    from apps.agent.tools.base import ToolRegistry
    return ToolRegistry

logger = logging.getLogger(__name__)


@dataclass
class AgentMessage:
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    sender: str = ''          # agent type or 'user'
    recipient: str = ''       # target agent type
    intent: str = ''
    payload: dict = field(default_factory=dict)
    timeout_ms: int = 30000
    user_id: str = 'anonymous'


@dataclass
class AgentResult:
    task_id: str = ''
    success: bool = True
    data: Any = None
    error: str = ''
    token_used: int = 0
    duration_ms: int = 0
    need_reroute: bool = False
    reroute_reason: str = ''
    reroute_suggestion: str = ''


class BaseAgent(ABC):
    """所有Agent的抽象基类（自研，无第三方框架依赖）"""

    name: str = 'base'

    def __init__(self):
        self._skills: dict[str, Any] = {}
        self._running = False

    def register_skill(self, skill) -> None:
        self._skills[skill.name] = skill
        logger.debug('[%s] skill registered: %s', self.name, skill.name)

    def get_skill(self, name: str):
        skill = self._skills.get(name)
        if not skill:
            raise ValueError(f'Skill {name!r} not registered on {self.name}')
        return skill

    async def run_tool(self, tool_name: str, **kwargs) -> Any:
        """执行一个工具，优先从全局ToolRegistry查找"""
        registry = _get_tool_registry()
        tool = registry.get(tool_name)
        if tool is None:
            raise ValueError(f'Tool {tool_name!r} not found in registry')
        result = await tool.execute(**kwargs)
        logger.debug('[%s] tool=%s success=%s', self.name, tool_name, result.success)
        return result

    @abstractmethod
    async def handle(self, message: AgentMessage) -> AgentResult:
        """处理一条消息，返回结果"""

    async def start(self) -> None:
        self._running = True
        logger.info('[%s] started', self.name)

    async def stop(self) -> None:
        self._running = False
        logger.info('[%s] stopped', self.name)
