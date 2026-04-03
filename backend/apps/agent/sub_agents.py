from __future__ import annotations

import json
import logging
import re
import time

from .base import BaseAgent, AgentMessage, AgentResult
from .llm_client import LLMClient, is_fallback
from .prompt_loader import PromptLoader
from .registry import AgentRegistry

logger = logging.getLogger(__name__)

_MAX_TOOL_ROUNDS = 5  # 防止无限循环


class _LLMAgent(BaseAgent):
    """所有基于LLM的子Agent的公共基类，支持工具调用循环"""

    prompt_name: str = ''
    domain_description: str = ''  # 子类覆盖：领域边界描述

    def __init__(self):
        super().__init__()
        self._llm = LLMClient.get_instance()
        self._system_prompt = PromptLoader.load(self.prompt_name) if self.prompt_name else ''

    def _get_tools_schema(self) -> list[dict]:
        """获取当前可用工具的OpenAI function calling schema"""
        from apps.agent.tools.base import ToolRegistry
        return ToolRegistry.get_all_schemas()

    def _get_memory_manager(self, message: AgentMessage):
        from apps.memory.manager import MemoryManager
        return MemoryManager(agent_type=self.name, user_id=message.user_id)

    async def _get_recent_conversation_context(self, message: AgentMessage, max_turns: int = 5) -> list[dict]:
        """
        获取最近的对话上下文，用于多轮对话
        返回格式: [{'role': 'user', 'content': '...'}, {'role': 'assistant', 'content': '...'}]
        """
        mem = self._get_memory_manager(message)

        # 获取最近的对话记录
        recent_conv = mem._l1.get('conv_history', [])[-max_turns*2:]  # 每轮对话包含用户和助手

        logger.debug('[%s] Retrieved recent conversation from memory: %s', self.name, recent_conv)

        context_messages = []
        for item in recent_conv:
            if isinstance(item, dict):
                role = item.get('role', 'user')
                text = item.get('text', '')
                if text:
                    context_messages.append({
                        'role': role,
                        'content': text
                    })

        return context_messages

    async def handle(self, message: AgentMessage) -> AgentResult:
        logger.info('[%s] Handling message with intent: %s, payload keys: %s', self.name, message.intent, list(message.payload.keys()))
        text = message.payload.get('text', '')
        extra = self._build_context(message)
        user_prompt = f'{extra}\n\n{text}'.strip() if extra else text

        # 检索相关记忆，注入system prompt
        mem = self._get_memory_manager(message)
        memories = await mem.retrieve(query=text, top_k=5)
        system = self._system_prompt
        if memories:
            mem_lines = '\n'.join(f'- [{m["source"]}] {m["content"]}' for m in memories)
            system = f'{system}\n\n### 相关记忆\n{mem_lines}'

        logger.debug('[%s] Final system prompt:\n%s', self.name, system[:1000])

        # 更新system prompt以包含多轮对话指导
        system += "\n\n### 多轮对话说明\n如果用户的问题需要持续的多轮交互来完成任务或者你为用户提供了继续对话的选项时，你需要：\n1. 询问用户更多细节或澄清问题\n2. 在回答最后加上'请提供您的反馈'。\n3. 根据上下文判断是否需要继续对话\n如果你认为对话已完成，请在回答末尾加入'任务完成'。"

        # 获取最近的对话上下文
        recent_context = await self._get_recent_conversation_context(message)

        tools = self._get_tools_schema()

        # 构建消息历史，先添加最近的对话记录，然后添加当前的用户消息
        messages = recent_context + [{'role': 'user', 'content': user_prompt}]
        logger.debug('[%s] Final user prompt:\n%s', self.name, user_prompt[:1000])

        for _ in range(_MAX_TOOL_ROUNDS):
            resp = await self._llm.chat_with_tools(
                system=system,
                messages=messages,
                tools=tools,
                max_tokens=2048,
            )

            if not resp.has_tool_calls:
                # 最终文本回复
                content = resp.content
                if is_fallback(content):
                    return AgentResult(task_id=message.task_id, success=False, error='LLM暂时不可用')

                # 检查拒收信号
                rejection = self._check_rejection(content)
                if rejection:
                    return AgentResult(
                        task_id=message.task_id,
                        success=False,
                        need_reroute=True,
                        reroute_reason=rejection.get('reason', '不属于本Agent职责范围'),
                        reroute_suggestion=rejection.get('suggested_agent', ''),
                    )

                # 分析内容以确定是否需要继续多轮对话
                continue_conversation = self._should_continue_conversation(content)

                # 准备返回数据，包含多轮对话控制信息
                response_data = {
                    'content': content,
                    'continue_conversation': continue_conversation,
                    'start_multi_turn': continue_conversation,  # 开始多轮对话模式
                    'agent_name': self.name
                }

                # 写入记忆
                mem.write_l1(message.task_id, f'Q:{text[:200]}|A:{content[:200]}')

                # 更新对话历史到L1记忆
                conv_history = mem._l1.get('conv_history', [])
                
                # 添加用户消息
                conv_history.append({
                    'role': 'user',
                    'text': text[:200],
                    'ts': int(time.time())
                })
                # 添加助手回复
                conv_history.append({
                    'role': 'assistant',
                    'text': content[:200],
                    'ts': int(time.time())
                })
                # 限制对话历史长度
                mem._l1['conv_history'] = conv_history[-20:]  # 保留最近10轮对话

                await mem.write_l2(
                    content=f'user: {text}\nassistant: {content}',
                    memory_type='conversation',
                )
                return AgentResult(task_id=message.task_id, success=True, data=response_data)

            # 执行工具调用，收集结果
            tool_results = []
            for tc in resp.tool_calls:
                try:
                    logger.debug('[%s] Executing tool call: %s with arguments %s', self.name, tc.name, tc.arguments)
                    result = await self.run_tool(tc.name, **tc.arguments)
                    tool_results.append({
                        'role': 'tool',
                        'tool_call_id': tc.call_id,
                        'content': str(result.data) if result.success else f'Error: {result.error}',
                    })
                except Exception as e:
                    tool_results.append({
                        'role': 'tool',
                        'tool_call_id': tc.call_id,
                        'content': f'Error: {e}',
                    })
                    logger.warning('[%s] tool %s failed: %s', self.name, tc.name, e)

            # 把assistant的tool_calls消息和工具结果追加到对话
            messages.append({
                'role': 'assistant',
                'content': resp.content or '',
                'tool_calls': [
                    {
                        'id': tc.call_id,
                        'type': 'function',
                        'function': {'name': tc.name, 'arguments': str(tc.arguments)},
                    }
                    for tc in resp.tool_calls
                ],
            })
            messages.extend(tool_results)

        # 超出轮次，直接让LLM总结
        # 将对话历史拼接为文本，因为 chat() 只接受 system/user 两个字符串参数
        history_text = '\n'.join(
            f"[{m.get('role', 'user')}]: {m.get('content', '')}" for m in messages
        )
        final = await self._llm.chat(
            system=self._system_prompt,
            user=f'{history_text}\n\n请根据以上工具调用结果给出最终回答。',
            max_tokens=2048,
        )

        # 默认认为超出轮次时不再继续对话
        response_data = {
            'content': final,
            'continue_conversation': False,
            'start_multi_turn': False,
            'agent_name': self.name
        }
        return AgentResult(task_id=message.task_id, success=True, data=response_data)

    def _build_context(self, message: AgentMessage) -> str:
        """子类可覆写，提取payload中的结构化数据拼入prompt"""
        return ''

    def _should_continue_conversation(self, content: str) -> bool:
        """
        判断是否需要继续多轮对话
        通过分析响应内容中的特定模式来判断
        """
        content_lower = content.lower()

        # 检查是否有表示继续对话的词汇
        continuation_indicators = [
            '是否需要进一步', '还需要什么', '继续', '接下来', '还有其他',
            '是否还有', '还有什么', '下一步', '后续步骤', '想了解更多',
            '继续帮你', '接下来我', '下一步是', '后续是', '然后呢',
            '要不要', '是否想', '你想知道', '我可以帮你', '我可以继续',
            '继续讨论', '深入探讨', '详细说明', '具体介绍','请提供您的反馈'
        ]

        # 检查是否有表示结束对话的词汇
        termination_indicators = [
            '完成', '结束', '完毕', '搞定', '解决了', '任务完成',
            '感谢使用', '再见', '如果有问题', '随时联系', '下次再说'
        ]

        # 计算延续和终止词汇的数量
        continuation_count = sum(1 for indicator in continuation_indicators if indicator in content_lower)
        termination_count = sum(1 for indicator in termination_indicators if indicator in content_lower)

        # 如果延续词汇数量多于终止词汇数量，则继续对话
        return continuation_count > termination_count

    def _check_rejection(self, content: str) -> dict | None:
        """检查 LLM 是否返回了拒收信号"""
        try:
            match = re.search(r'\{[^}]*"rejected"\s*:\s*true[^}]*\}', content)
            if match:
                return json.loads(match.group(0))
        except (json.JSONDecodeError, AttributeError):
            pass
        return None

    def _should_reject(self, text: str) -> bool:
        """
        在 LLM 回复前，通过规则快速预检是否明显不属于本 Agent。
        子类可覆写。
        """
        return False


@AgentRegistry.register_class
class AnalystAgent(_LLMAgent):
    """市场分析Agent：技术面+基本面分析，信号生成"""

    name = 'analyst'
    prompt_name = 'analyst'
    domain_description = (
        '市场行情分析（技术面/基本面）、K线解读、趋势判断、交易信号生成。'
        '不负责：交易计划制定、风控规则、仓位管理、策略代码编写。'
    )

    def _build_context(self, message: AgentMessage) -> str:
        parts = []
        if symbol := message.payload.get('symbol'):
            parts.append(f'Symbol: {symbol}')
        if timeframe := message.payload.get('timeframe'):
            parts.append(f'Timeframe: {timeframe}')
        if kline_data := message.payload.get('kline_data'):
            parts.append(f'K-line data (latest 20 bars):\n{kline_data}')
        return '\n'.join(parts)


@AgentRegistry.register_class
class QuantEngineerAgent(_LLMAgent):
    """量化工程师Agent：策略代码生成与优化"""

    name = 'quant'
    prompt_name = 'quant'
    domain_description = (
        '量化策略代码编写、策略优化、回测执行。'
        '不负责：市场分析、交易计划制定、风控建议。'
    )


@AgentRegistry.register_class
class CoachAgent(_LLMAgent):
    """交易教练Agent：计划制定、复盘总结"""

    name = 'coach'
    prompt_name = 'coach'
    domain_description = (
        '交易计划制定、交易复盘、周报总结、交易心理辅导。'
        '不负责：实时行情分析、策略代码编写、仓位计算。'
    )


@AgentRegistry.register_class
class RiskAdvisorAgent(_LLMAgent):
    """风险顾问Agent：仓位评估、风控建议"""

    name = 'risk_advisor'
    prompt_name = 'risk_advisor'
    domain_description = (
        '仓位管理、止损建议、风险评估、风控规则制定。'
        '不负责：行情预测、策略代码编写、交易复盘。'
    )

    def _build_context(self, message: AgentMessage) -> str:
        parts = []
        if position := message.payload.get('position_pct'):
            parts.append(f'Current position: {position}%')
        if daily_pnl := message.payload.get('daily_pnl_pct'):
            parts.append(f'Daily PnL: {daily_pnl}%')
        return '\n'.join(parts)


# @AgentRegistry.register_class
# class PlannerAgent(_LLMAgent):
#     """计划制定Agent：制定交易计划"""

#     name = 'planner'
#     prompt_name = 'coach'   # 复用coach prompt，phase2独立


@AgentRegistry.register_class
class BacktestAgent(BaseAgent):
    """回测Agent：触发回测任务并返回结果摘要"""

    name = 'backtest'

    async def handle(self, message: AgentMessage) -> AgentResult:
        strategy_id = message.payload.get('strategy_id')
        symbol = message.payload.get('symbol', 'BTCUSDT')
        timeframe = message.payload.get('timeframe', '1h')
        start_date = message.payload.get('start_date')
        end_date = message.payload.get('end_date')

        if not strategy_id:
            return AgentResult(
                task_id=message.task_id,
                success=False,
                error='缺少 strategy_id 参数',
            )

        # 异步触发Celery任务（phase2）
        # from apps.backtest.tasks import run_backtest_task
        # task = run_backtest_task.delay(strategy_id, symbol, timeframe, start_date, end_date)
        logger.info(
            f'[BacktestAgent] strategy={strategy_id} symbol={symbol} '
            f'tf={timeframe} {start_date}~{end_date}'
        )

        # 准备响应数据，包含多轮对话控制信息
        response_data = {
            'content': f'回测任务已提交：{symbol} {timeframe}',
            'strategy_id': strategy_id,
            'continue_conversation': False,  # 回测通常是单次操作
            'start_multi_turn': False,
            'agent_name': self.name
        }

        # 写入记忆
        from apps.memory.manager import MemoryManager
        mem = MemoryManager(agent_type=self.name, user_id=message.user_id)

        # 更新对话历史到L1记忆
        conv_history = mem._l1.get('conv_history', [])
        # 添加用户消息
        text = message.payload.get('text', '')
        conv_history.append({
            'role': 'user',
            'text': text[:200],
            'ts': int(time.time())
        })
        # 添加助手回复
        conv_history.append({
            'role': 'assistant',
            'text': f'回测任务已提交：{symbol} {timeframe}'[:200],
            'ts': int(time.time())
        })
        # 限制对话历史长度
        mem._l1['conv_history'] = conv_history[-20:]  # 保留最近10轮对话

        return AgentResult(
            task_id=message.task_id,
            success=True,
            data=response_data,
        )
