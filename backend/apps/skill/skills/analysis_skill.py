from __future__ import annotations
from apps.skill.base import BaseSkill
from apps.skill.registry import SkillRegistry


@SkillRegistry.register
class AnalysisSkill(BaseSkill):
    name = 'AnalysisSkill'
    description = '对指定交易品种进行技术面分析，输出分析报告'

    async def execute(self, payload: dict) -> dict:
        symbol = payload.get('symbol', 'BTCUSDT')
        timeframe = payload.get('timeframe', '1h')
        extra = payload.get('extra', '')

        from apps.agent.llm_client import LLMClient
        from apps.agent.prompt_loader import PromptLoader
        system = PromptLoader.load('analyst')
        user_msg = (
            f'请对 {symbol} 在 {timeframe} 周期进行技术分析。'
            f'{" 补充信息：" + extra if extra else ""}'
        )
        llm = LLMClient.get_instance()
        text = await llm.chat(system=system, user=user_msg)
        return {'symbol': symbol, 'timeframe': timeframe, 'analysis': text}
