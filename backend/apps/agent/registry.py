from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from apps.common.registry import Registry

if TYPE_CHECKING:
    from .base import BaseAgent


class AgentRegistry:
    """全局Agent注册中心（单例懒加载 + 动态发现）。

    内部使用两个泛型 Registry 后端：
      - _classes: 已注册的 Agent 类（name -> class）
      - _registry: 已实例化的 Agent 单例（name -> instance）

    两个 backend 都提供 .clear() 方法，兼容旧测试里的 `._classes.clear()` /
    `._registry.clear()` 调用。

    领域特有逻辑（从 Prompt 文件发现 Agent）保留在本类，不进泛型。
    """

    _classes: Registry[type] = Registry("AgentRegistry.classes")
    _registry: Registry["BaseAgent"] = Registry("AgentRegistry.instances")
    _discovered: bool = False

    @classmethod
    def register_class(cls, agent_cls: type) -> type:
        """注册Agent类（类装饰器）"""
        cls._classes.register(agent_cls.name, agent_cls, overwrite=True)
        return agent_cls

    @classmethod
    def register_dynamic(cls, name: str, agent_cls: type) -> None:
        """动态注册Agent类（由 discover_from_prompts 调用）"""
        cls._classes.register(name, agent_cls, overwrite=True)

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
        cls.discover_from_prompts()
        instance = cls._registry.get(name)
        if instance is None:
            agent_cls = cls._classes.get(name)
            if agent_cls is None:
                raise KeyError(f"Agent not registered: {name!r}")
            instance = agent_cls()
            cls._registry.register(name, instance)
        return instance

    @classmethod
    def all_names(cls) -> list[str]:
        cls.discover_from_prompts()
        return cls._classes.keys()

    @classmethod
    def reset(cls) -> None:
        """清空已实例化的 Agent、已注册的类，以及 discovered 标志。

        替代旧的三行清理模式：
            AgentRegistry._registry.clear()
            AgentRegistry._classes.clear()
            AgentRegistry._discovered = False
        """
        cls._registry.reset()
        cls._classes.reset()
        cls._discovered = False
