from __future__ import annotations

import json
import logging
import re
import time
from typing import Optional

from .base import BaseAgent, AgentMessage, AgentResult
from .llm_client import LLMClient, is_fallback
from .prompt_loader import PromptLoader
from .frame_manager import FrameManager
from .session_manager import get_session_manager, SessionState

logger = logging.getLogger(__name__)

INTENT_TO_AGENT = {
    'analyze_market':    'analyst',
    'generate_signal':   'analyst',
    'generate_strategy': 'quant',
    'run_backtest':      'backtest',
    'create_plan':       'coach',
    'create_trading_system': 'coach',
    'review_trade':      'coach',
    'summarize_week':    'coach',
    'assess_risk':       'risk_advisor',
}

AGENT_TO_INTENT = {v: k for k, v in INTENT_TO_AGENT.items()}

FRAME_INTENTS = {
    'start_trading':  ('trading', 'start'),
    'stop_trading':   ('trading', 'stop'),
    'start_monitor':  ('assist', 'start'),
    'stop_monitor':   ('assist', 'stop'),
}

# 意图解析降级规则
FALLBACK_RULES = [
    (r'(分析|行情|走势|K线|趋势).*(BTC|ETH|币|市场)', 'analyze_market'),
    (r'(回测|测试策略|历史数据)', 'run_backtest'),
    (r'(风险|止损|仓位|风控)', 'assess_risk'),
    (r'(计划|复盘|总结|周报)', 'create_plan'),
    (r'(策略|代码|编写)', 'generate_strategy'),
]

MAX_REROUTE = 2
PAUSE_TTL = 300  # 5 minutes


class SupervisorAgent(BaseAgent):
    """主管Agent：LLM解析用户意图、路由子Agent、管理框架生命周期"""

    name = 'supervisor'

    _instance: Optional['SupervisorAgent'] = None

    def __init__(self):
        super().__init__()
        self._llm = LLMClient.get_instance()
        self._frame = FrameManager.get_instance()
        self._system_prompt = PromptLoader.load('supervisor')

    @classmethod
    def get_instance(cls) -> 'SupervisorAgent':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def handle(self, message: AgentMessage) -> AgentResult:
        session_mgr = get_session_manager()
        session_ctx = await session_mgr.get_session_context(message.user_id)

        # ========== 会话状态分发 ==========
        if session_ctx:
            state = session_ctx['state']

            if state == SessionState.PAUSED.value:
                return await self._handle_paused_session(message, session_ctx, session_mgr)

            if state == SessionState.MULTI_TURN.value:
                return await self._handle_multi_turn(message, session_ctx, session_mgr)

        # ========== 正常路由流程 ==========
        return await self._normal_route(message)

    async def _handle_paused_session(self, message: AgentMessage,
                                     ctx: dict, session_mgr) -> AgentResult:
        """处理暂停中的会话"""
        paused_at = ctx.get('paused_at', 0)
        ttl = ctx.get('pause_ttl', PAUSE_TTL)

        # 超时 → 归档
        if time.time() - paused_at > ttl:
            await self._archive_paused_session(message.user_id, ctx, session_mgr)
            return await self._normal_route(message)

        # Supervisor 判断是否应恢复原会话
        should_resume = await self._judge_resume(message, ctx)

        if should_resume:
            agent_name = ctx['active_agent']
            await session_mgr.resume_session(message.user_id, agent_name)
            return await self._route_to_agent(agent_name, message)

        # 继续处理当前意图，保持暂停状态
        return await self._normal_route(message)

    async def _judge_resume(self, message: AgentMessage, paused_ctx: dict) -> bool:
        """让 Supervisor 判断当前消息是否属于暂停中的对话的延续"""
        text = message.payload.get('text', '')
        pause_context = paused_ctx.get('pause_context', '')

        prompt = (
            f'用户有一个暂停中的对话（正在和 {paused_ctx["active_agent"]} 交互）：\n'
            f'暂停上下文：{pause_context}\n\n'
            f'用户当前消息：{text}\n\n'
            f'判断这条消息是否属于暂停中的对话的延续。只回答 true 或 false。'
        )
        resp = await self._llm.chat(
            system='你是一个意图判断助手，只回答 true 或 false。',
            user=prompt,
            max_tokens=10,
            temperature=0.0,
        )
        return resp.strip().lower().startswith('true')

    async def _archive_paused_session(self, user_id: str, ctx: dict,
                                     session_mgr) -> None:
        """超时后总结暂停会话的记忆，存入 L3，关闭会话"""
        agent_name = ctx.get('active_agent', '')
        pause_context = ctx.get('pause_context', '')

        if not pause_context:
            await session_mgr.clear_session_context(user_id)
            return

        # LLM 做一句话总结
        summary = await self._llm.chat(
            system='总结以下对话上下文为一句话。',
            user=pause_context,
            max_tokens=100,
            temperature=0.1,
        )

        # 存入 L3 长期记忆
        from apps.memory.manager import MemoryManager
        mm = MemoryManager(agent_type='supervisor', user_id=user_id)
        await mm.write_l3(
            content=f'[历史对话摘要-{agent_name}] {summary}',
            memory_type='conversation_summary',
        )

        await session_mgr.clear_session_context(user_id)
        logger.info('[%s] Paused session archived for user %s', self.name, user_id)

    async def _handle_multi_turn(self, message: AgentMessage,
                                 ctx: dict, session_mgr) -> AgentResult:
        """处理多轮对话中的消息"""
        agent_name = ctx['active_agent']
        if not agent_name:
            await session_mgr.clear_session_context(message.user_id)
            return await self._normal_route(message)

        result = await self._route_to_agent(agent_name, message)

        # SubAgent 拒收
        if result.need_reroute:
            logger.info(
                'Multi-turn agent %s rejected: %s, pausing session',
                agent_name, result.reroute_reason,
            )
            await self._pause_session(message, session_mgr, agent_name, result)

            # 尝试用新意图路由
            new_intent = await self._parse_intent(message.payload.get('text', ''))
            if new_intent and new_intent != 'free_chat':
                new_agent = INTENT_TO_AGENT.get(new_intent)
                if new_agent and new_agent != agent_name:
                    return await self._route_with_fallback(message, new_intent, {agent_name})

            # 无法路由，走 free_chat
            return await self._free_chat(message)

        # 检查是否继续多轮
        if isinstance(result.data, dict) and result.data.get('continue_conversation', False):
            await session_mgr.set_session_context(
                message.user_id, SessionState.MULTI_TURN, agent_name,
            )
        else:
            await session_mgr.clear_session_context(message.user_id)

        return result

    async def _pause_session(self, message: AgentMessage, session_mgr, agent_name: str,
                            result: AgentResult) -> None:
        """暂停多轮对话"""
        # 获取当前对话上下文摘要
        pause_context = f"原意图: {agent_name}, 拒收原因: {result.reroute_reason}"
        if isinstance(result.data, dict):
            content = result.data.get('content', '')
            if content:
                pause_context = f"{content[:100]}... 续"

        await session_mgr.pause_session(
            message.user_id,
            active_agent=agent_name,
            pause_ttl=PAUSE_TTL,
            pause_context=pause_context,
        )

    async def _normal_route(self, message: AgentMessage) -> AgentResult:
        """正常路由流程：意图解析 → 路由 → 结果"""
        logger.info('[%s] Handling message with intent: %s, payload keys: %s',
                    self.name, message.intent, list(message.payload.keys()))

        session_mgr = get_session_manager()
        mm = None
        routing_history: list = []
        if message.user_id:
            from apps.memory.manager import MemoryManager
            mm = MemoryManager(agent_type='supervisor', user_id=message.user_id)
            routing_history = mm._l1.get('routing_history', [])

        intent = message.intent or await self._parse_intent(
            message.payload.get('text', ''),
            context=routing_history[-5:] if routing_history else None,
        )
        message.intent = intent

        # 框架生命周期
        if intent in FRAME_INTENTS:
            return await self._handle_frame(intent, message)

        # 路由子Agent
        agent_name = INTENT_TO_AGENT.get(intent)
        if agent_name:
            result = await self._route_with_fallback(message, intent)

            # 路由成功时写 L1
            if mm and result.success and not result.need_reroute:
                updated = routing_history[-19:] + [{
                    'intent': intent,
                    'agent': agent_name,
                    'q': message.payload.get('text', '')[:100],
                    'ts': int(time.time()),
                }]
                mm.write_l1('routing_history', updated)

                # 检查响应是否要求开始多轮对话
                if hasattr(result.data, 'get') and result.data.get('start_multi_turn', False):
                    await session_mgr.set_session_context(
                        message.user_id, SessionState.MULTI_TURN, agent_name,
                    )

            return result

        # 未知意图
        return await self._free_chat(message)

    async def _route_with_fallback(self, message: AgentMessage,
                                   intent: str,
                                   attempted: set | None = None) -> AgentResult:
        """带拒收重路由的 Agent 调用"""
        attempted = attempted or set()
        agent_name = INTENT_TO_AGENT.get(intent)

        if not agent_name or agent_name in attempted or len(attempted) >= MAX_REROUTE:
            return await self._free_chat(message)

        attempted.add(agent_name)
        result = await self._route_to_agent(agent_name, message)

        # SubAgent 拒收
        if result.need_reroute:
            logger.info('Agent %s rejected: %s', agent_name, result.reroute_reason)

            # 优先用 SubAgent 建议的目标
            if result.reroute_suggestion and result.reroute_suggestion not in attempted:
                suggested_intent = AGENT_TO_INTENT.get(result.reroute_suggestion)
                if suggested_intent:
                    return await self._route_with_fallback(message, suggested_intent, attempted)

            # 重新解析意图（排除已尝试的 Agent）
            exclude_agents = list(attempted)
            new_intent = await self._parse_intent(
                message.payload.get('text', ''),
                exclude_agents=exclude_agents,
            )
            if new_intent and new_intent != 'free_chat':
                new_agent = INTENT_TO_AGENT.get(new_intent)
                if new_agent and new_agent not in attempted:
                    return await self._route_with_fallback(message, new_intent, attempted)

            # 都失败
            return await self._free_chat(message)

        return result

    async def _route_to_agent(self, agent_name: str,
                              message: AgentMessage) -> AgentResult:
        """懒加载并调用子Agent"""
        from .registry import AgentRegistry
        try:
            agent = AgentRegistry.get(agent_name)
            message.sender = 'supervisor'
            message.recipient = agent_name
            return await agent.handle(message)
        except Exception as e:
            logger.error('Routing to %s failed: %s', agent_name, e)
            return AgentResult(task_id=message.task_id, success=False, error=str(e))

    async def handle_text(self, text: str) -> str:
        """Channel收到自然语言文本的便捷入口"""
        msg = AgentMessage(sender='user', recipient='supervisor', payload={'text': text})
        result = await self.handle(msg)
        if result.success:
            return str(result.data)
        return f'[错误] {result.error}'

    # ------------------------------------------------------------------ #
    #  内部方法                                                           #
    # ------------------------------------------------------------------ #

    async def _parse_intent(self, text: str,
                           context: list | None = None,
                           exclude_agents: list | None = None) -> str:
        """
        调用LLM解析意图，支持多意图。

        Returns:
            str: 单个意图名，或 'free_chat'
        """
        if not text:
            return 'unknown'

        valid_intents = list(INTENT_TO_AGENT.keys()) + list(FRAME_INTENTS.keys())
        context_block = ''
        if context:
            context_block = f'Recent routing history (for reference): {json.dumps(context)}\n\n'

        exclude_block = ''
        if exclude_agents:
            exclude_block = f'Exclude these agents (already tried): {exclude_agents}\n\n'

        user_prompt = (
            f'{context_block}'
            f'{exclude_block}'
            f'User message: {text}\n\n'
            f'Valid intents: {json.dumps(valid_intents)}\n\n'
            'Reply with a JSON object. '
            'If the user has one intent: {"intent": "<name>", "params": {}}\n'
            'If multiple independent intents: {"intents": [{"intent": "...", "params": {}}, ...]}\n'
            'If none matches, use intent="free_chat".'
        )
        response = await self._llm.chat(
            system=self._system_prompt,
            user=user_prompt,
            max_tokens=256,
            temperature=0.1,
        )
        if is_fallback(response):
            return self._rule_based_intent(text) or 'free_chat'

        try:
            text = response.strip()
            fence_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
            if fence_match:
                text = fence_match.group(1).strip()
            else:
                obj_match = re.search(r'\{[\s\S]*\}', text)
                if obj_match:
                    text = obj_match.group(0).strip()
            data = json.loads(text)
            logger.debug(f'Parsed intent data: {data}')

            # 多意图处理：取第一个意图，串行处理在调用方处理
            if 'intents' in data and isinstance(data['intents'], list) and len(data['intents']) > 0:
                # 返回第一个，后续由 _handle_multi_intent 串行处理
                return data['intents'][0].get('intent', 'free_chat')

            return data.get('intent', 'free_chat')
        except Exception:
            logger.warning(f'Intent parse failed, raw: {response[:200]}')
            # 降级到规则引擎
            return self._rule_based_intent(text) or 'free_chat'

    def _rule_based_intent(self, text: str) -> str | None:
        """规则引擎降级"""
        for pattern, intent in FALLBACK_RULES:
            if re.search(pattern, text):
                return intent
        return None

    async def _handle_frame(self, intent: str, message: AgentMessage) -> AgentResult:
        frame_type, action = FRAME_INTENTS[intent]
        try:
            if action == 'start':
                await self._frame.start(frame_type)
            else:
                await self._frame.stop(frame_type)
            if message.user_id:
                from apps.memory.manager import MemoryManager
                mm = MemoryManager(agent_type='supervisor', user_id=message.user_id)
                state = 'running' if action == 'start' else 'stopped'
                await mm.write_l2(
                    content=f'{frame_type}:{state}',
                    memory_type='frame_state',
                    importance=2,
                )
            return AgentResult(
                task_id=message.task_id,
                success=True,
                data={'frame': frame_type, 'action': action, 'status': 'ok'},
            )
        except Exception as e:
            return AgentResult(task_id=message.task_id, success=False, error=str(e))

    async def _free_chat(self, message: AgentMessage) -> AgentResult:
        """未识别意图，LLM自由对话"""
        text = message.payload.get('text', '')

        mm = None
        conv_history: list = []
        if message.user_id:
            from apps.memory.manager import MemoryManager
            mm = MemoryManager(agent_type='supervisor', user_id=message.user_id)
            conv_history = mm._l1.get('conv_history', [])[-20:]

        if conv_history:
            history_block = '\n'.join(
                f"{t['role']}: {t['text']}" for t in conv_history
            )
            user_prompt = f'[对话历史]\n{history_block}\n\n[当前消息]\n{text}'
        else:
            user_prompt = text

        response = await self._llm.chat(
            system=self._system_prompt,
            user=user_prompt,
            max_tokens=1024,
        )
        if is_fallback(response):
            return AgentResult(
                task_id=message.task_id,
                success=False,
                error='LLM暂时不可用，请稍后再试',
            )

        if mm:
            updated = conv_history[-18:] + [
                {'role': 'user', 'text': text[:200], 'ts': int(time.time())},
                {'role': 'assistant', 'text': response[:200], 'ts': int(time.time())},
            ]
            mm.write_l1('conv_history', updated)
            await mm.write_l2(
                content=f'user: {text}\nassistant: {response}',
                memory_type='conversation',
                importance=1,
            )

        return AgentResult(task_id=message.task_id, success=True, data=response)
