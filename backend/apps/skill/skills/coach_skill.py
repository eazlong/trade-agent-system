from __future__ import annotations
from apps.skill.base import BaseSkill
from apps.skill.registry import SkillRegistry


@SkillRegistry.register
class TradeReviewSkill(BaseSkill):
    name = 'TradeReviewSkill'
    description = '复盘交易记录，给出改进建议'

    async def execute(self, payload: dict) -> dict:
        trade_records = payload.get('records', '')
        period = payload.get('period', '本周')

        from apps.agent.llm_client import LLMClient
        from apps.agent.prompt_loader import PromptLoader
        system = PromptLoader.load('coach')
        user_msg = f'请对{period}的交易记录进行复盘分析并给出改进建议：\n{trade_records}'
        llm = LLMClient.get_instance()
        text = await llm.chat(system=system, user=user_msg)
        return {'review': text}


@SkillRegistry.register
class WeeklySummarySkill(BaseSkill):
    name = 'WeeklySummarySkill'
    description = '生成每周交易总结报告'

    async def execute(self, payload: dict) -> dict:
        data = payload.get('data', '')

        from apps.agent.llm_client import LLMClient
        from apps.agent.prompt_loader import PromptLoader
        system = PromptLoader.load('coach')
        user_msg = f'请生成本周交易总结报告，数据如下：\n{data}'
        llm = LLMClient.get_instance()
        text = await llm.chat(system=system, user=user_msg)
        return {'summary': text}
