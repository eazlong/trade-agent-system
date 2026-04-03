from __future__ import annotations
from apps.skill.base import BaseSkill
from apps.skill.registry import SkillRegistry


@SkillRegistry.register
class RiskAssessSkill(BaseSkill):
    name = 'RiskAssessSkill'
    description = '评估仓位风险和账户风险'

    async def execute(self, payload: dict) -> dict:
        position = payload.get('position', '')
        account_info = payload.get('account_info', '')

        from apps.agent.llm_client import LLMClient
        from apps.agent.prompt_loader import PromptLoader
        system = PromptLoader.load('risk_advisor')
        user_msg = (
            f'请评估以下仓位的风险：\n{position}'
            f'{chr(10) + "账户信息：" + account_info if account_info else ""}'
        )
        llm = LLMClient.get_instance()
        text = await llm.chat(system=system, user=user_msg)
        return {'risk_assessment': text}
