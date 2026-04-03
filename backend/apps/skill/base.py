from __future__ import annotations
import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class BaseSkill(ABC):
    """所有Skill的抽象基类。所有业务流程定义为Skill，Agent加载Skill执行。"""

    name: str = 'base_skill'
    description: str = ''

    @abstractmethod
    async def execute(self, payload: dict) -> Any:
        """执行Skill逻辑，payload为Agent传入的参数"""

    def __repr__(self):
        return f'<Skill: {self.name}>'
