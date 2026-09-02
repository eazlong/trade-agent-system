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


def _prepend_system(system: str, messages: list[dict]) -> list[dict]:
    """组装完整 messages：将 messages 内的 system 条目并入开头 system 消息。

    对话压缩摘要以 role=system 条目存储，若原样发送，网关会拒收
    400 "System message must be at the beginning."
    """
    extra = "\n\n".join(
        str(m.get("content", "")) for m in messages if m.get("role") == "system"
    )
    lead = f"{system}\n\n{extra}" if extra else system
    body = [m for m in messages if m.get("role") != "system"]
    return [{"role": "system", "content": lead}] + body


class LLMClient:
    """
    LLM调用客户端，带降级机制。
    主用 OpenAI (gpt-4o)，失败后自动切换 DeepSeek。
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
        """调用LLM，主用OpenAI，自动降级到DeepSeek"""
        t0 = time.monotonic()
        try:
            result = await self._call_openai(system, user, max_tokens, temperature)
            logger.debug(f"OpenAI OK ({(time.monotonic() - t0) * 1000:.0f}ms)")
            return result
        except Exception as e:
            logger.warning(f"OpenAI failed ({e}), falling back to DeepSeek")

        try:
            result = await self._call_deepseek(system, user, max_tokens, temperature)
            logger.debug(f"DeepSeek OK ({(time.monotonic() - t0) * 1000:.0f}ms)")
            return result
        except Exception as e:
            logger.error(f"DeepSeek fallback also failed: {e}")
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
        async with httpx.AsyncClient(timeout=360.0, proxy=proxy) as client:
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
            resp_data = resp.json()
            choices = resp_data.get("choices", [])
            if not choices:
                logger.warning("[LLMClient] No choices in OpenAI response!")
                raise ValueError("No choices in API response")

            msg = choices[0].get("message", {})
            # DeepSeek reasoning models (R1/V3) 可能将输出放在 reasoning_content 而非 content
            content = msg.get("content", "")
            reasoning_content = msg.get("reasoning_content", "")

            # 优先使用 content,fallback 到 reasoning_content (reasoning model 场景)
            final_content = content if content else reasoning_content

            if not final_content:
                logger.warning(
                    f"[LLMClient] Empty content and reasoning_content in response: {msg}"
                )

            return final_content

    async def chat_with_tools(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int = 2048,
        temperature: float = 0.3,
    ) -> LLMToolResponse:
        """支持工具调用的LLM接口（OpenAI function calling格式），截断/网络错误自动重试"""
        last_exc: Exception | None = None
        current_max_tokens = max_tokens
        # 截断重试时翻倍 token 预算，上限 32768；
        # 避免 write_file 等大参数工具因 max_tokens 不足反复失败
        max_tokens_cap = 32768
        for attempt in range(1, TOOL_CALL_MAX_RETRIES + 1):
            try:
                return await self._call_openai_with_tools(
                    system, messages, tools, current_max_tokens, temperature
                )
            except ToolCallTruncatedError as e:
                last_exc = e
                next_max = min(current_max_tokens * 2, max_tokens_cap)
                logger.warning(
                    "tool_call truncated, retry %d/%d (max_tokens %d->%d): %s",
                    attempt,
                    TOOL_CALL_MAX_RETRIES,
                    current_max_tokens,
                    next_max,
                    e,
                )
                current_max_tokens = next_max
            except httpx.HTTPStatusError as e:
                last_exc = e
                # 5xx / 429 可重试，其他直接降级
                if e.response.status_code in (429, 500, 502, 503, 504):
                    logger.warning(
                        "HTTP %d, retry %d/%d",
                        e.response.status_code,
                        attempt,
                        TOOL_CALL_MAX_RETRIES,
                    )
                else:
                    break
            except (
                httpx.TimeoutException,
                httpx.ConnectError,
                httpx.RemoteProtocolError,
            ) as e:
                last_exc = e
                logger.warning(
                    "Network error (%s), retry %d/%d", e, attempt, TOOL_CALL_MAX_RETRIES
                )
            except Exception as e:
                last_exc = e
                break  # 配置错误等非可重试异常，直接降级

        logger.warning(
            f"OpenAI tool call failed ({last_exc}), falling back to plain chat"
        )
        
        # 降级：拼接工具描述到system prompt，让LLM输出JSON
        tool_desc = json.dumps(tools, ensure_ascii=False)
        fallback_system = (
            f'{system}\n\n可用工具（如需使用，必须以 JSON 输出 '
            f'{{"tool": "name", "args": {{...}}}}。'
            f'禁止输出 XML/DSML 等标记格式的工具调用。）:\n{tool_desc}'
        )
        # 将完整对话历史格式化（不仅取最后一条），让LLM在多轮工具调用中也能看到上下文
        conversation_parts = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "user":
                conversation_parts.append(f"[用户] {content}")
            elif role == "assistant":
                tc_info = ""
                if "tool_calls" in m:
                    tc_info = (
                        " (调用了工具: "
                        + ", ".join(
                            f"{tc['function']['name']}"
                            for tc in m.get("tool_calls", [])
                        )
                        + ")"
                    )
                conversation_parts.append(f"[助手] {content}{tc_info}")
            elif role == "tool":
                conversation_parts.append(
                    f"[工具返回 {m.get('tool_call_id', '')}] {content[:600]}"
                )
            elif role == "system":
                # 压缩摘要等 system 条目，降级时保留上下文
                conversation_parts.append(f"[上下文] {content}")
        user_text = (
            "\n\n---\n\n".join(conversation_parts)
            if conversation_parts
            else (messages[-1].get("content", "") if messages else "")
        )
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
        full_messages = _prepend_system(system, messages)
        async with httpx.AsyncClient(timeout=360.0, proxy=proxy) as client:
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
            if resp.status_code >= 400:
                body = resp.text
                logger.error(
                    "LLM API error %d for %s:\n%s",
                    resp.status_code,
                    resp.url,
                    body[:2000],
                )
                resp.raise_for_status()
            resp_json = resp.json()
            choice = resp_json["choices"][0]
            msg = choice["message"]
            finish_reason = choice.get("finish_reason")
            raw_calls = msg.get("tool_calls") or []
            tool_calls = []
            parse_failed = False
            for tc in raw_calls:
                try:
                    args = json.loads(tc["function"]["arguments"])
                except (json.JSONDecodeError, TypeError) as exc:
                    parse_failed = True
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
            # finish_reason='length' 表示输出被 max_tokens 截断；
            # 即使 JSON 碰巧合法，tool_call 内容也可能不完整
            if finish_reason == "length" and raw_calls:
                raise ToolCallTruncatedError(
                    f"finish_reason='length' with {len(raw_calls)} tool_call(s); "
                    "arguments likely incomplete"
                )
            if parse_failed:
                # json.loads 成功的路径不会到这里；保留以防未来逻辑变更
                raise ToolCallTruncatedError("tool_call arguments truncated")
            return LLMToolResponse(
                content=msg.get("content") or "",
                tool_calls=tool_calls,
                reasoning_content=msg.get("reasoning_content") or "",
            )

    async def _call_deepseek(
        self, system: str, user: str, max_tokens: int, temperature: float
    ) -> str:
        api_key = settings.DEEPSEEK_API_KEY
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY not configured")
        base_url = getattr(
            settings, "DEEPSEEK_API_BASE_URL", "https://api.deepseek.com/v1"
        )
        model = getattr(settings, "DEEPSEEK_MODEL_FALLBACK", "deepseek-chat")
        proxy = getattr(settings, "DEEPSEEK_PROXY", "") or None
        async with httpx.AsyncClient(timeout=360.0, proxy=proxy) as client:
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
        current_max_tokens = max_tokens
        max_tokens_cap = 32768
        for attempt in range(1, TOOL_CALL_MAX_RETRIES + 1):
            try:
                return await self._call_openai_stream_with_tools(
                    system, messages, tools, on_chunk, current_max_tokens, temperature
                )
            except ToolCallTruncatedError as e:
                last_exc = e
                next_max = min(current_max_tokens * 2, max_tokens_cap)
                logger.warning(
                    "stream tool_call truncated, retry %d/%d (max_tokens %d->%d): %s",
                    attempt,
                    TOOL_CALL_MAX_RETRIES,
                    current_max_tokens,
                    next_max,
                    e,
                )
                current_max_tokens = next_max
            except httpx.HTTPStatusError as e:
                last_exc = e
                if e.response.status_code in (429, 500, 502, 503, 504):
                    logger.warning(
                        "HTTP %d, retry %d/%d",
                        e.response.status_code,
                        attempt,
                        TOOL_CALL_MAX_RETRIES,
                    )
                else:
                    break
            except (
                httpx.TimeoutException,
                httpx.ConnectError,
                httpx.RemoteProtocolError,
            ) as e:
                last_exc = e
                logger.warning(
                    "Network error (%s), retry %d/%d", e, attempt, TOOL_CALL_MAX_RETRIES
                )
            except Exception as e:
                last_exc = e
                break  # 配置错误等非可重试异常，直接降级

        logger.warning(
            f"OpenAI tool stream failed ({last_exc}), falling back to non-stream"
        )
        return await self.chat_with_tools(
            system, messages, tools, current_max_tokens, temperature
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
        reasoning_chunks: list[str] = []
        tool_calls_map: dict[int, dict] = {}  # index → {name, arguments}
        finish_reason: str | None = None
        full_messages = _prepend_system(system, messages)

        async with httpx.AsyncClient(timeout=360.0, proxy=proxy) as client:
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
                if resp.status_code >= 400:
                    body = await resp.aread()
                    logger.error(
                        "LLM stream API error %d for %s:\n%s",
                        resp.status_code,
                        resp.url,
                        body.decode(errors="replace")[:2000],
                    )
                    resp.raise_for_status()
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        payload = json.loads(data)
                        choice = payload["choices"][0]
                        delta = choice["delta"]
                        fr = choice.get("finish_reason")
                        if fr:
                            finish_reason = fr
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue

                    # reasoning_content delta (DeepSeek thinking mode)
                    rc_token = delta.get("reasoning_content", "")
                    if rc_token:
                        reasoning_chunks.append(rc_token)

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
        # 流式 finish_reason='length' 表示被 max_tokens 截断；
        # 即使 JSON 碰巧合法，tool_call 内容也可能不完整
        if finish_reason == "length" and tool_calls_map:
            raise ToolCallTruncatedError(
                f"stream finish_reason='length' with {len(tool_calls_map)} "
                "tool_call(s); arguments likely incomplete"
            )
        return LLMToolResponse(
            content="".join(chunks),
            tool_calls=tool_calls,
            reasoning_content="".join(reasoning_chunks),
        )


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
        self, content: str = "", tool_calls: list[ToolCallRequest] | None = None,
        reasoning_content: str = "",
    ):
        self.content = content
        self.tool_calls: list[ToolCallRequest] = tool_calls or []
        self.reasoning_content = reasoning_content

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)
