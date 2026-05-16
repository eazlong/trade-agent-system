from __future__ import annotations
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
import uuid


# 延迟导入避免循环依赖
def _get_tool_registry():
    from apps.agent.tools.base import ToolRegistry

    return ToolRegistry


logger = logging.getLogger(__name__)


@dataclass
class AgentMessage:
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    sender: str = ""  # agent type or 'user'
    recipient: str = ""  # target agent type
    intent: str = ""
    payload: dict = field(default_factory=dict)
    timeout_ms: int = 30000
    user_id: str = "anonymous"


@dataclass
class AgentResult:
    task_id: str = ""
    success: bool = True
    data: Any = None
    error: str = ""
    token_used: int = 0
    duration_ms: int = 0
    need_reroute: bool = False
    reroute_reason: str = ""
    reroute_suggestion: str = ""


class BaseAgent(ABC):
    """所有Agent的抽象基类（自研，无第三方框架依赖）"""

    name: str = "base"
    # 子类可覆盖：声明该 Agent 可用的工具名称列表
    _agent_tools: list[str] = []
    # 工具调用最大循环轮数
    _max_tool_rounds: int = 5

    def __init__(self):
        self._running = False
        self._skills_loader = None

    # ------------------------------------------------------------------ #
    #  工具 & 技能（公共逻辑）                                              #
    # ------------------------------------------------------------------ #

    def _get_skills_loader(self):
        """懒加载 skills_loader。"""
        if self._skills_loader is None:
            from apps.skill.loader import get_skills_loader

            self._skills_loader = get_skills_loader(self.name)
        return self._skills_loader

    def _get_tools_schema(self) -> list[dict]:
        """获取当前 Agent 可用的工具 schema。

        优先使用 _agent_tools 声明列表；未声明则回退到全局所有工具。
        """
        from apps.agent.tools.base import ToolRegistry

        if self._agent_tools:
            schemas = []
            for tool_name in self._agent_tools:
                tool = ToolRegistry.get(tool_name)
                if tool:
                    schemas.append(tool.schema)
                else:
                    logger.warning(
                        "[%s] tool %s not found in registry", self.name, tool_name
                    )
            return schemas
        return ToolRegistry.get_all_schemas()

    def _build_skills_section(self, base_prompt: str) -> str:
        """注入 always 技能 + 非 always 技能摘要到 system prompt。

        子类传入基础 prompt，返回增强后的 prompt。
        """
        loader = self._get_skills_loader()
        always_skills = loader.get_always_skills()
        if always_skills:
            skills_content = loader.load_skills_content(always_skills)
            base_prompt = f"{base_prompt}\n\n### Agent Skills\n{skills_content}"

        skill_summary = loader.build_summary()
        if skill_summary:
            base_prompt = (
                f"{base_prompt}\n\n"
                f"### Skills\n"
                f"以下技能扩展了你的能力, **能用则必须要使用**。使用 load_skill 工具加载完整内容。\n"
                f"如果用户的需求与以下技能描述相关，**一定要**使用 load_skill 工具。\n"
                f"{skill_summary}"
            )

        return base_prompt

    async def _execute_tool_call(self, tc) -> str:
        """执行单个工具调用，返回结果字符串。

        特殊处理 load_skill（传 agent_name）。
        """
        try:
            if tc.name == "load_skill":
                from apps.agent.tools.load_skill import LoadSkillTool

                skill_name = tc.arguments.get("skill_name", "")
                tool = LoadSkillTool(agent_name=self.name)
                logger.info(
                    "[%s] executing tool: %s with args: %s",
                    self.name,
                    tc.name,
                    tc.arguments,
                )
                result = await tool.execute(skill_name=skill_name)
            else:
                result = await self.run_tool(tc.name, **tc.arguments)

            if result.success:
                return str(result.data)
            return f"Error: {result.error}"
        except Exception as e:
            logger.warning("[%s] tool %s failed: %s", self.name, tc.name, e)
            return f"Error: {e}"

    def _try_parse_json_tool_call(self, content: str) -> dict | None:
        """尝试从 LLM 文本回复中解析 JSON 格式的 tool call（fallback 路径）。

        当 LLM 不支持原生 function calling 时，fallback 会让 LLM 输出
        {"tool": "<name>", "args": {...}} 格式。此方法解析这种格式并返回
        标准化的 tool call dict。
        """
        if not content or not content.strip():
            return None
        text = content.strip()
        # 去掉 markdown 代码块包裹
        if text.startswith("```"):
            idx = text.find("\n")
            text = text[idx + 1 :] if idx > 0 else text[3:]
            if text.rstrip().endswith("```"):
                text = text.rstrip()[:-3]
        # 提取 JSON 对象
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            import re

            m = re.search(
                r'\{[^{}]*"tool"\s*:\s*"[^"]+"\s*,\s*"args"\s*:\s*\{[^{}]*\}\s*\}',
                text,
            )
            if not m:
                return None
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
        if isinstance(data, dict) and "tool" in data and "args" in data:
            return data
        return None

    async def _run_tool_loop(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int = 2048,
    ) -> tuple[str, bool]:
        """通用工具调用循环。子类可复用。

        Returns:
            (final_content, is_fallback)
        """
        from .llm_client import LLMClient, is_fallback

        llm = LLMClient.get_instance()

        for _ in range(self._max_tool_rounds):
            resp = await llm.chat_with_tools(
                system=system,
                messages=messages,
                tools=tools,
                max_tokens=max_tokens,
            )
            if not resp.has_tool_calls:
                content = resp.content
                if is_fallback(content):
                    return ("", True)

                # 检查是否是 fallback 路径的 JSON tool call
                json_tc = self._try_parse_json_tool_call(content)
                if json_tc is None:
                    return (content, False)

                # 将 JSON tool call 转为标准 ToolCallRequest
                tc = type(
                    "ToolCallRequest",
                    (),
                    {
                        "call_id": "json_tc_0",
                        "name": json_tc["tool"],
                        "arguments": json_tc["args"],
                    },
                )()
                tool_calls = [tc]
            else:
                tool_calls = resp.tool_calls

            # 执行工具
            tool_results = []
            for tc in tool_calls:
                result_text = await self._execute_tool_call(tc)
                tool_results.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.call_id,
                        "content": result_text,
                    }
                )
                # Update last_alive so watchdog knows task is still making progress
                from .task_tracker import tracker_context
                tracker = tracker_context.get(None)
                if tracker is not None:
                    tracker.alive()

            messages.append(
                {
                    "role": "assistant",
                    "content": resp.content or "",
                    "tool_calls": [
                        {
                            "id": tc.call_id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments),
                            },
                        }
                        for tc in tool_calls
                    ],
                }
            )
            messages.extend(tool_results)

        # 超出轮次，让 LLM 总结
        history_text = "\n".join(
            f"[{m.get('role', 'user')}]: {m.get('content', '')}" for m in messages
        )
        final = await llm.chat(
            system=system,
            user=f"{history_text}\n\n请根据以上工具调用结果给出最终回答。",
            max_tokens=max_tokens,
        )
        return (final, False)

    async def run_tool(self, tool_name: str, **kwargs) -> Any:
        """执行一个工具，优先从全局ToolRegistry查找"""
        registry = _get_tool_registry()
        tool = registry.get(tool_name)
        if tool is None:
            raise ValueError(f"Tool {tool_name!r} not found in registry")
        result = await tool.execute(**kwargs)
        logger.debug("[%s] tool=%s success=%s", self.name, tool_name, result.success)
        return result

    @abstractmethod
    async def handle(self, message: AgentMessage) -> AgentResult:
        """处理一条消息，返回结果"""

    async def start(self) -> None:
        self._running = True
        logger.info("[%s] started", self.name)

    async def stop(self) -> None:
        self._running = False
        logger.info("[%s] stopped", self.name)
