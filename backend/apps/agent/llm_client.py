from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

FALLBACK_MARKER = '__FALLBACK__'


class LLMClient:
    """
    LLM调用客户端，带降级机制。
    主用 OpenAI (gpt-4o)，失败后自动切换 Anthropic (claude-opus-4-6)。
    """

    _instance: Optional['LLMClient'] = None

    @classmethod
    def get_instance(cls) -> 'LLMClient':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def chat(
        self,
        system: str,
        user: str,
        max_tokens: int = 1024,
        temperature: float = 0.3,
    ) -> str:
        """调用LLM，主用OpenAI，自动降级到Anthropic"""
        t0 = time.monotonic()
        try:
            result = await self._call_openai(system, user, max_tokens, temperature)
            logger.debug(f'OpenAI OK ({(time.monotonic()-t0)*1000:.0f}ms)')
            return result
        except Exception as e:
            logger.warning(f'OpenAI failed ({e}), falling back to Anthropic')

        try:
            result = await self._call_anthropic(system, user, max_tokens, temperature)
            logger.debug(f'Anthropic OK ({(time.monotonic()-t0)*1000:.0f}ms)')
            return result
        except Exception as e:
            logger.error(f'Anthropic fallback also failed: {e}')
            return FALLBACK_MARKER

    async def _call_openai(
        self, system: str, user: str, max_tokens: int, temperature: float
    ) -> str:
        api_key = settings.OPENAI_API_KEY
        base_url = getattr(settings, 'OPENAI_API_BASE_URL', 'https://api.openai.com/v1/')
        if not api_key:
            raise ValueError('OPENAI_API_KEY not configured')
        model = getattr(settings, 'OPENAI_MODEL_PRIMARY', 'gpt-4o')
        proxy = getattr(settings, 'OPENAI_PROXY', '') or None
        async with httpx.AsyncClient(timeout=60.0, proxy=proxy) as client:
            resp = await client.post(
                f'{base_url}/chat/completions',
                headers={'Authorization': f'Bearer {api_key}'},

                json={
                    'model': model,
                    'messages': [
                        {'role': 'system', 'content': system},
                        {'role': 'user', 'content': user},
                    ],
                    'max_tokens': max_tokens,
                    'temperature': temperature,
                },
            )
            resp.raise_for_status()
            return resp.json()['choices'][0]['message']['content']

    async def chat_with_tools(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int = 2048,
        temperature: float = 0.3,
    ) -> LLMToolResponse:
        """支持工具调用的LLM接口（OpenAI function calling格式）"""
        try:
            return await self._call_openai_with_tools(system, messages, tools, max_tokens, temperature)
        except Exception as e:
            logger.warning(f'OpenAI tool call failed ({e}), falling back to plain chat')
            # 降级：拼接工具描述到system prompt，让LLM输出JSON
            tool_desc = json.dumps(tools, ensure_ascii=False)
            fallback_system = (
                f'{system}\n\n可用工具（如需使用，以JSON输出 {{"tool": "name", "args": {{...}}}}）:\n{tool_desc}'
            )
            user_text = messages[-1].get('content', '') if messages else ''
            result = await self.chat(fallback_system, user_text, max_tokens, temperature)
            return LLMToolResponse(content=result)

    async def _call_openai_with_tools(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int,
        temperature: float,
    ) -> LLMToolResponse:
        api_key = settings.OPENAI_API_KEY
        base_url = getattr(settings, 'OPENAI_API_BASE_URL', 'https://api.openai.com/v1/')
        if not api_key:
            raise ValueError('OPENAI_API_KEY not configured')
        model = getattr(settings, 'OPENAI_MODEL_PRIMARY', 'gpt-4o')
        proxy = getattr(settings, 'OPENAI_PROXY', '') or None
        full_messages = [{'role': 'system', 'content': system}] + messages
        async with httpx.AsyncClient(timeout=60.0, proxy=proxy) as client:
            resp = await client.post(
                f'{base_url}/chat/completions',
                headers={'Authorization': f'Bearer {api_key}'},
                json={
                    'model': model,
                    'messages': full_messages,
                    'tools': tools,
                    'tool_choice': 'auto',
                    'max_tokens': max_tokens,
                    'temperature': temperature,
                },
            )
            resp.raise_for_status()
            msg = resp.json()['choices'][0]['message']
            raw_calls = msg.get('tool_calls') or []
            tool_calls = [
                ToolCallRequest(
                    call_id=tc['id'],
                    name=tc['function']['name'],
                    arguments=json.loads(tc['function']['arguments']),
                )
                for tc in raw_calls
            ]
            return LLMToolResponse(content=msg.get('content') or '', tool_calls=tool_calls)

    async def _call_anthropic(
        self, system: str, user: str, max_tokens: int, temperature: float
    ) -> str:
        api_key = settings.ANTHROPIC_API_KEY
        if not api_key:
            raise ValueError('ANTHROPIC_API_KEY not configured')
        model = getattr(settings, 'ANTHROPIC_MODEL_FALLBACK', 'claude-opus-4-6')
        proxy = getattr(settings, 'ANTHROPIC_PROXY', '') or None
        async with httpx.AsyncClient(timeout=60.0, proxy=proxy) as client:
            resp = await client.post(
                'https://api.anthropic.com/v1/messages',
                headers={
                    'x-api-key': api_key,
                    'anthropic-version': '2023-06-01',
                },
                json={
                    'model': model,
                    'system': system,
                    'messages': [{'role': 'user', 'content': user}],
                    'max_tokens': max_tokens,
                    'temperature': temperature,
                },
            )
            resp.raise_for_status()
            return resp.json()['content'][0]['text']


def is_fallback(response: str) -> bool:
    """判断LLM是否返回了兜底标记"""
    return response == FALLBACK_MARKER


class ToolCallRequest:
    """LLM返回的工具调用请求"""
    def __init__(self, call_id: str, name: str, arguments: dict):
        self.call_id = call_id
        self.name = name
        self.arguments = arguments


class LLMToolResponse:
    """chat_with_tools的返回值"""
    def __init__(self, content: str = '', tool_calls: list[ToolCallRequest] | None = None):
        self.content = content
        self.tool_calls: list[ToolCallRequest] = tool_calls or []

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)
