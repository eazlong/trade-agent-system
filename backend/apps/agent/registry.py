from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import BaseAgent


class AgentRegistry:
    """全局Agent注册中心（单例懒加载 + 动态发现）"""

    _registry: dict[str, "BaseAgent"] = {}  # 已实例化的Agent
    _classes: dict[str, type] = {}  # 注册的Agent类
    _discovered = False  # 是否已从 Prompt 发现

    @classmethod
    def register_class(cls, agent_cls: type) -> type:
        """注册Agent类（类装饰器）"""
        cls._classes[agent_cls.name] = agent_cls
        return agent_cls

    @classmethod
    def register_dynamic(cls, name: str, agent_cls: type) -> None:
        """动态注册Agent类（由 discover_from_prompts 调用）"""
        cls._classes[name] = agent_cls

    @classmethod
    def discover_from_prompts(cls) -> None:
        """从 Prompt 文件动态发现并注册 Agent。"""
        if cls._discovered:
            return

        from apps.agent.prompt_loader import PromptLoader
        from apps.agent.sub_agents import _build_dynamic_agent_class

        agents = PromptLoader.list_agents()
        for meta in agents:
            name = meta.get("name")
            if not name:
                continue
            agent_cls = _build_dynamic_agent_class(meta)
            cls.register_dynamic(name, agent_cls)
            logging.getLogger(__name__).info(
                "[AgentRegistry] discovered agent: %s (tools=%s)",
                name,
                meta.get("tools", []),
            )
        cls._discovered = True

    @classmethod
    def get(cls, name: str) -> "BaseAgent":
        """获取Agent实例（懒加载 + 自动发现）"""
        cls.discover_from_prompts()  # 首次调用时自动发现
        if name not in cls._registry:
            agent_cls = cls._classes.get(name)
            if agent_cls is None:
                raise KeyError(f"Agent not registered: {name!r}")
            cls._registry[name] = agent_cls()
        return cls._registry[name]

    @classmethod
    def all_names(cls) -> list[str]:
        cls.discover_from_prompts()
        return list(cls._classes.keys())
