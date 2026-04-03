from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ToolResult:
    success: bool
    data: Any = None
    error: str = ''


class BaseTool(ABC):
    """所有工具的抽象基类"""

    # 工具名称（用于LLM function calling中的name字段）
    name: str = ''
    # 工具描述（用于LLM function calling中的description字段）
    description: str = ''

    @property
    def schema(self) -> dict:
        """返回OpenAI function calling格式的schema"""
        return {
            'type': 'function',
            'function': {
                'name': self.name,
                'description': self.description,
                'parameters': self.parameters_schema,
            },
        }

    @property
    @abstractmethod
    def parameters_schema(self) -> dict:
        """返回参数的JSON Schema"""

    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult:
        """执行工具，返回ToolResult"""


class ToolRegistry:
    """全局工具注册表，支持动态注册"""

    _tools: dict[str, BaseTool] = {}

    @classmethod
    def register(cls, tool: BaseTool) -> None:
        cls._tools[tool.name] = tool
        logger.debug(f'[ToolRegistry] registered: {tool.name}')

    @classmethod
    def unregister(cls, name: str) -> None:
        cls._tools.pop(name, None)

    @classmethod
    def get(cls, name: str) -> BaseTool | None:
        return cls._tools.get(name)

    @classmethod
    def all(cls) -> list[BaseTool]:
        return list(cls._tools.values())

    @classmethod
    def schemas(cls) -> list[dict]:
        """返回所有工具的schema列表，直接用于LLM tools参数"""
        return [t.schema for t in cls._tools.values()]

    @classmethod
    def get_all_schemas(cls) -> list[dict]:
        """schemas()的别名，语义更清晰"""
        return cls.schemas()
