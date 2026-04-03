from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import BaseSkill


class SkillRegistry:
    """全局Skill注册中心"""

    _registry: dict[str, type] = {}

    @classmethod
    def register(cls, skill_cls):
        cls._registry[skill_cls.name] = skill_cls
        return skill_cls

    @classmethod
    def get(cls, name: str) -> type:
        skill_cls = cls._registry.get(name)
        if not skill_cls:
            raise ValueError(f'Skill {name!r} not found in registry')
        return skill_cls

    @classmethod
    def all_names(cls) -> list[str]:
        return list(cls._registry.keys())
