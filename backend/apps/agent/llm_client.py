from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Optional

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

FALLBACK_MARKER = "__FALLBACK__"


class ToolCallTruncatedError(Exception):
    """LLM 返回的 tool_call arguments JSON 被截断，需要重试"""


TOOL_CALL_MAX_RETRIES = 2


class LLMClient:
    """
    LLM调用客户端，带降级机制。
    主用 OpenAI (gpt-4o)，失败后自动切换 Anthropic (claude-opus-4-6)。
    """

    _instance: Optional["LLMClient"] = None

    @classmethod
    def get_instance(cls) -> "LLMClient":
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
            logger.debug(f"OpenAI OK ({(time.monotonic() - t0) * 1000:.0f}ms)")
            return result
        except Exception as e:
            logger.warning(f"OpenAI failed ({e}), falling back to Anthropic")

        try:
            result = await self._call_anthropic(system, user, max_tokens, temperature)
            logger.debug(f"Anthropic OK ({(time.monotonic() - t0) * 1000:.0f}ms)")
            return result
        except Exception as e:
            logger.error(f"Anthropic fallback also failed: {e}")
            return FALLBACK_MARKER

    async def _call_openai(
        self, system: str, user: str, max_tokens: int, temperature: float
    ) -> str:
        api_key = settings.OPENAI_API_KEY
        base_url = getattr(
            settings, "OPENAI_API_BASE_URL", "https://api.openai.com/v1/"
        )
        if not api_key:
            raise ValueError("OPENAI_API_KEY not configured")
        model = getattr(settings, "OPENAI_MODEL_PRIMARY", "gpt-4o")
        proxy = getattr(settings, "OPENAI_PROXY", "") or None
        async with httpx.AsyncClient(timeout=60.0, proxy=proxy) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]

    async def chat_with_tools(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int = 2048,
        temperature: float = 0.3,
    ) -> LLMToolResponse:
        """支持工具调用的LLM接口（OpenAI function calling格式），截断自动重试"""
        last_exc: Exception | None = None
        for attempt in range(1, TOOL_CALL_MAX_RETRIES + 1):
            try:
                return await self._call_openai_with_tools(
                    system, messages, tools, max_tokens, temperature
                )
            except ToolCallTruncatedError as e:
                last_exc = e
                logger.warning(
                    "tool_call truncated, retry %d/%d: %s",
                    attempt,
                    TOOL_CALL_MAX_RETRIES,
                    e,
                )
            except Exception as e:
                last_exc = e
                break  # 非截断错误不重试，直接降级

        logger.warning(
            f"OpenAI tool call failed ({last_exc}), falling back to plain chat"
        )
        # 降级：拼接工具描述到system prompt，让LLM输出JSON
        tool_desc = json.dumps(tools, ensure_ascii=False)
        fallback_system = f'{system}\n\n可用工具（如需使用，以JSON输出 {{"tool": "name", "args": {{...}}}}）:\n{tool_desc}'
        user_text = messages[-1].get("content", "") if messages else ""
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
        base_url = getattr(
            settings, "OPENAI_API_BASE_URL", "https://api.openai.com/v1/"
        )
        if not api_key:
            raise ValueError("OPENAI_API_KEY not configured")
        model = getattr(settings, "OPENAI_MODEL_PRIMARY", "gpt-4o")
        proxy = getattr(settings, "OPENAI_PROXY", "") or None
        full_messages = [{"role": "system", "content": system}] + messages
        async with httpx.AsyncClient(timeout=60.0, proxy=proxy) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": full_messages,
                    "tools": tools,
                    "tool_choice": "auto",
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
            )
            resp.raise_for_status()
            msg = resp.json()["choices"][0]["message"]
            raw_calls = msg.get("tool_calls") or []
            tool_calls = []
            for tc in raw_calls:
                try:
                    args = json.loads(tc["function"]["arguments"])
                except (json.JSONDecodeError, TypeError) as exc:
                    raise ToolCallTruncatedError(
                        f"tool_call '{tc['function']['name']}' arguments truncated: {exc}"
                    ) from exc
                tool_calls.append(
                    ToolCallRequest(
                        call_id=tc["id"],
                        name=tc["function"]["name"],
                        arguments=args,
                    )
                )
            return LLMToolResponse(
                content=msg.get("content") or "", tool_calls=tool_calls
            )

    async def _call_anthropic(
        self, system: str, user: str, max_tokens: int, temperature: float
    ) -> str:
        api_key = settings.ANTHROPIC_API_KEY
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not configured")
        model = getattr(settings, "ANTHROPIC_MODEL_FALLBACK", "claude-opus-4-6")
        proxy = getattr(settings, "ANTHROPIC_PROXY", "") or None
        async with httpx.AsyncClient(timeout=60.0, proxy=proxy) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model": model,
                    "system": system,
                    "messages": [{"role": "user", "content": user}],
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
            )
            resp.raise_for_status()
            return resp.json()["content"][0]["text"]

    async def chat_stream(
        self,
        system: str,
        user: str,
        on_chunk: Callable[[str], Any] | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.3,
    ) -> str:
        """
        流式调用 LLM，边接收 token 边调用 on_chunk 回调。

        on_chunk: 回调函数，接收每个 token 字符串。
                  如果返回 False，停止接收后续 token。

        主用 OpenAI 流式，自动降级到非流式。
        """
        try:
            return await self._call_openai_stream(
                system, user, on_chunk, max_tokens, temperature
            )
        except Exception as e:
            logger.warning(f"OpenAI stream failed ({e}), falling back to non-stream")
            result = await self.chat(system, user, max_tokens, temperature)
            if on_chunk:
                # 一次性输出全部 chunks
                for i in range(0, len(result), 50):
                    stop = on_chunk(result[i : i + 50])
                    if stop is False:
                        break
            return result

    async def _call_openai_stream(
        self,
        system: str,
        user: str,
        on_chunk: Callable[[str], Any] | None,
        max_tokens: int,
        temperature: float,
    ) -> str:
        """OpenAI SSE 流式调用"""
        api_key = settings.OPENAI_API_KEY
        base_url = getattr(
            settings, "OPENAI_API_BASE_URL", "https://api.openai.com/v1/"
        )
        if not api_key:
            raise ValueError("OPENAI_API_KEY not configured")
        model = getattr(settings, "OPENAI_MODEL_PRIMARY", "gpt-4o")
        proxy = getattr(settings, "OPENAI_PROXY", "") or None

        chunks: list[str] = []
        async with httpx.AsyncClient(timeout=120.0, proxy=proxy) as client:
            async with client.stream(
                "POST",
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "stream": True,
                },
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        delta = json.loads(data)["choices"][0]["delta"]
                        token = delta.get("content", "") or delta.get("text", "")
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
                    if token:
                        chunks.append(token)
                        if on_chunk:
                            stop = on_chunk(token)
                            if stop is False:
                                # 通知停止，但继续消费流以避免截断
                                pass
        return "".join(chunks)

    async def chat_stream_with_tools(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        on_chunk: Callable[[str], Any] | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.3,
    ) -> LLMToolResponse:
        """
        支持工具调用的流式 LLM 接口，截断自动重试。
        注意：工具调用模式下流式只用于 content，
        tool_calls 在完全接收后返回。
        """
        last_exc: Exception | None = None
        for attempt in range(1, TOOL_CALL_MAX_RETRIES + 1):
            try:
                return await self._call_openai_stream_with_tools(
                    system, messages, tools, on_chunk, max_tokens, temperature
                )
            except ToolCallTruncatedError as e:
                last_exc = e
                logger.warning(
                    "stream tool_call truncated, retry %d/%d: %s",
                    attempt,
                    TOOL_CALL_MAX_RETRIES,
                    e,
                )
            except Exception as e:
                last_exc = e
                break  # 非截断错误不重试

        logger.warning(
            f"OpenAI tool stream failed ({last_exc}), falling back to non-stream"
        )
        return await self.chat_with_tools(
            system, messages, tools, max_tokens, temperature
        )

    async def _call_openai_stream_with_tools(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        on_chunk: Callable[[str], Any] | None,
        max_tokens: int,
        temperature: float,
    ) -> LLMToolResponse:
        """OpenAI 流式 + 工具调用"""
        api_key = settings.OPENAI_API_KEY
        base_url = getattr(
            settings, "OPENAI_API_BASE_URL", "https://api.openai.com/v1/"
        )
        if not api_key:
            raise ValueError("OPENAI_API_KEY not configured")
        model = getattr(settings, "OPENAI_MODEL_PRIMARY", "gpt-4o")
        proxy = getattr(settings, "OPENAI_PROXY", "") or None

        chunks: list[str] = []
        tool_calls_map: dict[int, dict] = {}  # index → {name, arguments}
        full_messages = [{"role": "system", "content": system}] + messages

        async with httpx.AsyncClient(timeout=120.0, proxy=proxy) as client:
            async with client.stream(
                "POST",
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": full_messages,
                    "tools": tools,
                    "tool_choice": "auto",
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "stream": True,
                },
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        delta = json.loads(data)["choices"][0]["delta"]
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue

                    # content delta
                    content_token = delta.get("content", "") or delta.get("text", "")
                    if content_token:
                        chunks.append(content_token)
                        if on_chunk:
                            on_chunk(content_token)

                    # tool_call delta
                    for tc_delta in delta.get("tool_calls", []):
                        idx = tc_delta.get("index", 0)
                        if idx not in tool_calls_map:
                            tool_calls_map[idx] = {"name": "", "arguments": ""}
                        if "function" in tc_delta:
                            tc_map = tool_calls_map[idx]
                            if "name" in tc_delta["function"]:
                                tc_map["name"] += tc_delta["function"]["name"]
                            if "arguments" in tc_delta["function"]:
                                tc_map["arguments"] += tc_delta["function"]["arguments"]

        tool_calls = []
        for i, tc_map in sorted(tool_calls_map.items()):
            raw_args = tc_map.get("arguments", "")
            try:
                args = json.loads(raw_args) if raw_args else {}
            except json.JSONDecodeError as exc:
                raise ToolCallTruncatedError(
                    f"stream tool_call '{tc_map['name']}' arguments truncated: {exc}"
                ) from exc
            tool_calls.append(
                ToolCallRequest(
                    call_id=f"tc_{i}",
                    name=tc_map["name"],
                    arguments=args,
                )
            )
        return LLMToolResponse(content="".join(chunks), tool_calls=tool_calls)


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

    def __init__(
        self, content: str = "", tool_calls: list[ToolCallRequest] | None = None
    ):
        self.content = content
        self.tool_calls: list[ToolCallRequest] = tool_calls or []

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)
