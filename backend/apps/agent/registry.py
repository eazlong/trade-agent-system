from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import BaseAgent


class AgentRegistry:
    """全局Agent注册中心（单例懒加载）"""

    _registry: dict[str, 'BaseAgent'] = {}   # 已实例化的Agent
    _classes: dict[str, type] = {}           # 注册的Agent类

    @classmethod
    def register_class(cls, agent_cls: type) -> type:
        """注册Agent类（类装饰器）"""
        cls._classes[agent_cls.name] = agent_cls
        return agent_cls

    @classmethod
    def get(cls, name: str) -> 'BaseAgent':
        """获取Agent实例（懒加载）"""
        if name not in cls._registry:
            agent_cls = cls._classes.get(name)
            if agent_cls is None:
                raise KeyError(f'Agent not registered: {name!r}')
            cls._registry[name] = agent_cls()
        return cls._registry[name]

    @classmethod
    def all_names(cls) -> list[str]:
        return list(cls._classes.keys())
