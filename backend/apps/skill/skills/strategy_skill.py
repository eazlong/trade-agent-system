from __future__ import annotations
from apps.skill.base import BaseSkill
from apps.skill.registry import SkillRegistry


@SkillRegistry.register
class StrategySkill(BaseSkill):
    name = 'StrategySkill'
    description = '生成或优化量化交易策略代码'

    async def execute(self, payload: dict) -> dict:
        description = payload.get('description', '')
        constraints = payload.get('constraints', '')

        from apps.agent.llm_client import LLMClient
        from apps.agent.prompt_loader import PromptLoader
        system = PromptLoader.load('quant')
        user_msg = (
            f'请根据以下描述生成一个Python量化交易策略：\n{description}'
            f'{chr(10) + "约束条件：" + constraints if constraints else ""}'
        )
        llm = LLMClient.get_instance()
        text = await llm.chat(system=system, user=user_msg)
        return {'strategy_code': text}
