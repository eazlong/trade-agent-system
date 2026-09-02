from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from apps.common.registry import Registry

logger = logging.getLogger(__name__)


@dataclass
class ToolResult:
    success: bool
    data: Any = None
    error: str = ""


class BaseTool(ABC):
    """所有工具的抽象基类"""

    # 工具名称（用于LLM function calling中的name字段）
    name: str = ""
    # 工具描述（用于LLM function calling中的description字段）
    description: str = ""

    @property
    def schema(self) -> dict:
        """返回OpenAI function calling格式的schema"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema,
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
    """全局工具注册表，支持动态注册。

    内部使用泛型 Registry[BaseTool] 后端。
    """

    _backend: Registry[BaseTool] = Registry("ToolRegistry")

    @classmethod
    def register(cls, tool: BaseTool) -> None:
        cls._backend.register(tool.name, tool, overwrite=True)
        logger.debug(f"[ToolRegistry] registered: {tool.name}")

    @classmethod
    def unregister(cls, name: str) -> None:
        cls._backend.unregister(name)

    @classmethod
    def get(cls, name: str) -> BaseTool | None:
        return cls._backend.get(name)

    @classmethod
    def all(cls) -> list[BaseTool]:
        return cls._backend.all()

    @classmethod
    def schemas(cls) -> list[dict]:
        """返回所有工具的schema列表，直接用于LLM tools参数"""
        return [t.schema for t in cls._backend]

    @classmethod
    def get_all_schemas(cls) -> list[dict]:
        """schemas()的别名，语义更清晰"""
        return cls.schemas()

    @classmethod
    def reset(cls) -> None:
        """清空所有已注册工具。用于测试隔离。"""
        cls._backend.reset()
