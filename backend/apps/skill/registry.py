from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from apps.common.registry import Registry

if TYPE_CHECKING:
    from apps.skill.base import BaseSkill

logger = logging.getLogger(__name__)


class SkillRegistry:
    """Skill注册中心（类装饰器 + 按name查找）。

    内部使用泛型 Registry[type[BaseSkill]] 后端，保证"注册/查找/清理"的
    通用逻辑只在一处维护。
    """

    _backend: Registry[type[BaseSkill]] = Registry("SkillRegistry")

    @classmethod
    def register(cls, skill_cls: type[BaseSkill]) -> type[BaseSkill]:
        """注册Skill类（类装饰器）"""
        cls._backend.register(skill_cls.name, skill_cls, overwrite=True)
        return skill_cls

    @classmethod
    def get(cls, name: str) -> type[BaseSkill] | None:
        """按名称获取Skill类"""
        return cls._backend.get(name)

    @classmethod
    def all(cls) -> list[type[BaseSkill]]:
        return cls._backend.all()

    @classmethod
    def reset(cls) -> None:
        """清空所有已注册的 Skill。用于测试隔离。"""
        cls._backend.reset()
