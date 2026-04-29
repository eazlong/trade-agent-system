from __future__ import annotations

import json
import logging
import re
import time

from .base import BaseAgent, AgentMessage, AgentResult
from .llm_client import LLMClient
from .prompt_loader import PromptLoader
from .registry import AgentRegistry

logger = logging.getLogger(__name__)


class _LLMAgent(BaseAgent):
    """所有基于LLM的子Agent的公共基类，支持工具调用循环"""

    prompt_name: str = ""
    domain_description: str = ""  # 子类覆盖：领域边界描述
    _agent_tools: list[str] = ["web_search", "web_fetch", "load_skill"]  # 默认工具列表

    def __init__(self):
        super().__init__()
        self._llm = LLMClient.get_instance()
        # 每个 Agent 声明的可用工具列表（由 Prompt 元数据或子类指定）
        # 子类 __init__ 可通过 _agent_tools.extend() 追加
        # 从 message.payload 中提取的上下文字段列表
        self._context_fields: list[str] = []

    def _build_system_prompt(self) -> str:
        """动态构建 system prompt，注入技能内容。"""
        system_prompt = PromptLoader.load(self.prompt_name) if self.prompt_name else ""
        system_prompt += '\n\n 收到用户消息后，先判断是否属于你的职责范围。如果不属于你的职责，必须只返回以下 JSON 格式，不加任何其他内容：\n {"rejected": true, "reason": "简短原因", "suggested_agent": "coach" 或 "risk_advisor"} \n 如果属于你的职责，正常回答，不要包含 rejected 字段。'
        return self._build_skills_section(system_prompt)

    def _get_tools_schema(self) -> list[dict]:
        """获取当前 Agent 可用的工具 schema。

        优先使用 _agent_tools 中声明的工具列表（由 Prompt 元数据注入）；
        如果未声明，回退到全局所有工具。
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

        # 回退：未声明工具列表时返回全部
        return ToolRegistry.get_all_schemas()

    def _get_memory_manager(self, message: AgentMessage):
        from apps.memory.manager import MemoryManager

        return MemoryManager(agent_type=self.name, user_id=message.user_id)

    async def _get_recent_conversation_context(
        self, message: AgentMessage, max_turns: int = 5
    ) -> list[dict]:
        """
        获取最近的对话上下文，用于多轮对话
        返回格式: [{'role': 'user', 'content': '...'}, {'role': 'assistant', 'content': '...'}]
        """
        mem = self._get_memory_manager(message)

        # 获取最近的对话记录
        recent_conv = mem._l1.get("conv_history", [])[
            -max_turns * 2 :
        ]  # 每轮对话包含用户和助手

        logger.debug(
            "[%s] Retrieved recent conversation from memory: %s", self.name, recent_conv
        )

        context_messages = []
        for item in recent_conv:
            if isinstance(item, dict):
                role = item.get("role", "user")
                text = item.get("text", "")
                if text:
                    context_messages.append({"role": role, "content": text})

        return context_messages

    async def handle(self, message: AgentMessage) -> AgentResult:
        text = message.payload.get("text", "")
        logger.info(
            "[%s] Handling message with intent: %s, payload keys: %s， %s",
            self.name,
            message.intent,
            list(message.payload.keys()),
            text,
        )

        extra = self._build_context(message)
        user_prompt = f"{extra}\n\n{text}".strip() if extra else text

        # 检索相关记忆，注入system prompt
        mem = self._get_memory_manager(message)
        memories = await mem.retrieve(query=text, top_k=5)
        system = self._build_system_prompt()
        if memories:
            mem_lines = "\n".join(f"- [{m['source']}] {m['content']}" for m in memories)
            system = f"{system}\n\n### 相关记忆\n{mem_lines}"

        logger.debug("[%s] Final system prompt:\n%s", self.name, system)

        # 获取最近的对话上下文
        recent_context = await self._get_recent_conversation_context(message)

        tools = self._get_tools_schema()

        # 构建消息历史，先添加最近的对话记录，然后添加当前的用户消息
        messages = recent_context + [{"role": "user", "content": user_prompt}]
        logger.debug("[%s] Final user prompt:\n%s", self.name, user_prompt[:1000])

        content, is_fb = await self._run_tool_loop(
            system, messages, tools, max_tokens=2048
        )

        if is_fb:
            return AgentResult(
                task_id=message.task_id, success=False, error="LLM暂时不可用"
            )

        # 检查拒收信号
        rejection = self._check_rejection(content)
        if rejection:
            return AgentResult(
                task_id=message.task_id,
                success=False,
                need_reroute=True,
                reroute_reason=rejection.get("reason", "不属于本Agent职责范围"),
                reroute_suggestion=rejection.get("suggested_agent", ""),
            )

        # 分析内容以确定是否需要继续多轮对话
        continue_conversation = self._should_continue_conversation(content)

        # 准备返回数据，包含多轮对话控制信息
        response_data = {
            "content": content,
            "continue_conversation": continue_conversation,
            "start_multi_turn": continue_conversation,  # 开始多轮对话模式
            "agent_name": self.name,
        }

        # 写入记忆
        mem.write_l1(message.task_id, f"Q:{text[:200]}|A:{content[:200]}")

        # 更新对话历史到L1记忆
        conv_history = mem._l1.get("conv_history", [])

        # 添加用户消息
        conv_history.append(
            {"role": "user", "text": text[:200], "ts": int(time.time())}
        )
        # 添加助手回复
        conv_history.append(
            {"role": "assistant", "text": content[:200], "ts": int(time.time())}
        )
        # 限制对话历史长度
        mem._l1["conv_history"] = conv_history[-20:]  # 保留最近10轮对话

        await mem.write_l2(
            content=f"user: {text}\nassistant: {content}",
            memory_type="conversation",
        )
        return AgentResult(task_id=message.task_id, success=True, data=response_data)

    def _build_context(self, message: AgentMessage) -> str:
        """根据声明的 context_fields 从 payload 提取上下文"""
        if not self._context_fields:
            return ""
        parts = []
        for field in self._context_fields:
            if value := message.payload.get(field):
                parts.append(f"{field}: {value}")
        return "\n".join(parts)

    def _should_continue_conversation(self, content: str) -> bool:
        """
        判断是否需要继续多轮对话
        通过分析响应内容中的特定模式来判断
        """
        content_lower = content.lower()

        # 检查是否有表示继续对话的词汇
        continuation_indicators = [
            "是否需要进一步",
            "还需要什么",
            "继续",
            "接下来",
            "还有其他",
            "是否还有",
            "还有什么",
            "下一步",
            "后续步骤",
            "想了解更多",
            "继续帮你",
            "接下来我",
            "下一步是",
            "后续是",
            "然后呢",
            "要不要",
            "是否想",
            "你想知道",
            "我可以帮你",
            "我可以继续",
            "继续讨论",
            "深入探讨",
            "详细说明",
            "具体介绍",
            "请提供您的反馈",
        ]

        # 检查是否有表示结束对话的词汇
        termination_indicators = [
            "完成",
            "结束",
            "完毕",
            "搞定",
            "解决了",
            "任务完成",
            "感谢使用",
            "再见",
            "如果有问题",
            "随时联系",
            "下次再说",
        ]

        # 计算延续和终止词汇的数量
        continuation_count = sum(
            1 for indicator in continuation_indicators if indicator in content_lower
        )
        termination_count = sum(
            1 for indicator in termination_indicators if indicator in content_lower
        )

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
        在 LLM 回复前，通过规则预检是否明显不属于本 Agent。
        子类可覆写。
        """
        return False


def _build_dynamic_agent_class(meta: dict) -> type:
    """根据 Prompt 元数据动态创建 Agent 类。"""
    name = meta["name"]
    tools = meta.get("tools", [])
    context_fields = meta.get("context_fields", [])

    def make_init(self):
        _LLMAgent.__init__(self)
        self._agent_tools.extend(tools)
        self._context_fields = context_fields

    agent_cls = type(
        f"{name.capitalize()}Agent",
        (_LLMAgent,),
        {
            "name": name,
            "prompt_name": name,
            "domain_description": "",
            "__init__": make_init,
        },
    )
    return agent_cls
