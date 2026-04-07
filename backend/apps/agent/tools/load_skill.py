"""Skill loading tool — allows LLM agents to dynamically load skill content on demand."""

from __future__ import annotations

import logging

from .base import BaseTool, ToolResult
from ...skill.loader import get_skills_loader

logger = logging.getLogger(__name__)


class LoadSkillTool(BaseTool):
    """
    Dynamic skill loading tool.

    When the LLM determines a skill is needed for the current task,
    it calls this tool to load the full skill content into context.
    The tool returns the skill content for injection into the conversation.
    """

    name = 'load_skill'
    description = (
        '加载指定技能的完整内容。当当前任务与某个可用技能的描述匹配时，'
        '调用此工具获取技能的详细指导和代码模板。'
    )

    def __init__(self, agent_name: str = ''):
        self._agent_name = agent_name

    @property
    def parameters_schema(self) -> dict:
        return {
            'type': 'object',
            'properties': {
                'skill_name': {
                    'type': 'string',
                    'description': '要加载的技能名称',
                },
            },
            'required': ['skill_name'],
        }

    async def execute(self, skill_name: str = '', **kwargs) -> ToolResult:
        if not skill_name:
            return ToolResult(success=False, error='skill_name 参数缺失')

        loader = get_skills_loader(self._agent_name)
        content = loader.load_skill(skill_name)
        if content is None:
            available = [s['name'] for s in loader.list_skills()]
            return ToolResult(
                success=False,
                error=f'技能 {skill_name!r} 不存在。可用技能: {", ".join(available)}',
            )

        body = loader._strip_frontmatter(content)
        return ToolResult(success=True, data=f'### Skill: {skill_name}\n\n{body}')
