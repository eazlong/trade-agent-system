from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

from .base import BaseAgent, AgentMessage, AgentResult
from .llm_client import LLMClient, is_fallback
from .prompt_loader import PromptLoader
from .frame_manager import FrameManager
from .session_manager import get_session_manager, SessionState

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
#  动态意图注册中心                                                     #
# ------------------------------------------------------------------ #


class IntentRouter:
    """动态意图路由注册表

    支持运行时注册意图与Agent的映射、框架操作意图、降级规则。
    SubAgent 可通过装饰器或调用注册方法动态添加意图。
    """

    _instance: Optional["IntentRouter"] = None

    def __init__(self):
        # 意图 → Agent 名称
        self._intent_to_agent: dict[str, str] = {}
        # Agent 名称 → 意图（反向映射）
        self._agent_to_intent: dict[str, str] = {}
        # 框架意图 → (frame_type, action)
        self._frame_intents: dict[str, tuple[str, str]] = {}
        # 降级规则列表 [(regex_pattern, intent), ...]
        self._fallback_rules: list[tuple[str, str]] = []

    @classmethod
    def get_instance(cls) -> "IntentRouter":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """重置单例（主要用于测试）"""
        cls._instance = None

    # ---- 意图注册 ----

    def register_intent(self, intent: str, agent_name: str) -> None:
        """注册单个意图到Agent的映射"""
        self._intent_to_agent[intent] = agent_name
        self._agent_to_intent[agent_name] = intent

    def register_intents(self, intent_map: dict[str, str]) -> None:
        """批量注册意图映射 {intent: agent_name}"""
        for intent, agent in intent_map.items():
            self.register_intent(intent, agent)

    def unregister_intent(self, intent: str) -> None:
        """移除意图映射"""
        agent = self._intent_to_agent.pop(intent, None)
        if agent and self._agent_to_intent.get(agent) == intent:
            del self._agent_to_intent[agent]

    # ---- 框架意图注册 ----

    def register_frame_intent(self, intent: str, frame_type: str, action: str) -> None:
        """注册框架操作意图（如 start_trading, stop_monitor）"""
        self._frame_intents[intent] = (frame_type, action)

    def unregister_frame_intent(self, intent: str) -> None:
        self._frame_intents.pop(intent, None)

    # ---- 降级规则注册 ----

    def register_fallback_rule(self, pattern: str, intent: str) -> None:
        """注册降级规则（正则匹配 → 意图）"""
        self._fallback_rules.append((pattern, intent))

    def register_fallback_rules(self, rules: list[tuple[str, str]]) -> None:
        """批量注册降级规则"""
        for pattern, intent in rules:
            self.register_fallback_rule(pattern, intent)

    # ---- 查询 ----

    def get_agent_for_intent(self, intent: str) -> str | None:
        return self._intent_to_agent.get(intent)

    def get_intent_for_agent(self, agent_name: str) -> str | None:
        return self._agent_to_intent.get(agent_name)

    def get_frame_intent(self, intent: str) -> tuple[str, str] | None:
        return self._frame_intents.get(intent)

    def is_frame_intent(self, intent: str) -> bool:
        return intent in self._frame_intents

    def all_intents(self) -> list[str]:
        return list(self._intent_to_agent.keys())

    def all_frame_intents(self) -> list[str]:
        return list(self._frame_intents.keys())

    def match_fallback(self, text: str) -> str | None:
        for pattern, intent in self._fallback_rules:
            if re.search(pattern, text):
                return intent
        return None

    def valid_intents_for_prompt(self) -> list[str]:
        """返回LLM Prompt中可用的意图列表"""
        return self.all_intents() + self.all_frame_intents()


def register_intent(intent: str, agent_name: str):
    """装饰器：在函数或类上注册意图映射

    用法:
        @register_intent('analyze_market', 'analyst')
        class AnalystAgent(_LLMAgent): ...

        @register_intent('custom_intent', 'my_agent')
        def some_setup(): ...
    """

    def decorator(target):
        IntentRouter.get_instance().register_intent(intent, agent_name)
        return target

    return decorator


def register_frame_intent(intent: str, frame_type: str, action: str):
    """装饰器：注册框架操作意图"""

    def decorator(target):
        IntentRouter.get_instance().register_frame_intent(intent, frame_type, action)
        return target

    return decorator


def register_fallback_rule(pattern: str, intent: str):
    """装饰器：注册降级规则

    用法:
        @register_fallback_rule(r'(分析|行情).*(BTC|ETH)', 'analyze_market')
        class AnalystAgent(_LLMAgent): ...
    """

    def decorator(target):
        IntentRouter.get_instance().register_fallback_rule(pattern, intent)
        return target

    return decorator


# ------------------------------------------------------------------ #
#  内置意图注册（保持向后兼容）                                          #
# ------------------------------------------------------------------ #

_router = IntentRouter.get_instance()

# 默认框架意图
_router.register_frame_intent("start_trading", "trading", "start")
_router.register_frame_intent("stop_trading", "trading", "stop")
_router.register_frame_intent("start_monitor", "assist", "start")
_router.register_frame_intent("stop_monitor", "assist", "stop")

# 默认降级规则（直接映射到 Agent 名称，非中间意图名）
_router.register_fallback_rules(
    [
        (r"(分析|行情|走势|K线|趋势).*(BTC|ETH|币|市场)", "analyst"),
        (r"(回测|测试策略|历史数据|backtest)", "quant"),
        (r"(风险|止损|仓位|风控)", "risk_advisor"),
        (r"(计划|复盘|总结|周报)", "coach"),
        (r"(实现.*策略|创建.*策略|编写.*策略|生成.*策略代码)", "quant"),
        (r"(研究|调研|收集.*资料|查找.*知识|搜索.*信息|内容研究|找.*策略|搜索.*策略)", "researcher"),
        (
            r"(价格|突破|跌破|高于|低于|提醒|通知|监控).*(BTC|ETH|币|\d{4,})",
            "supervisor",
        ),
    ]
)


MAX_REROUTE = 2
PAUSE_TTL = 300  # 5 minutes


class SupervisorAgent(BaseAgent):
    """主管Agent：LLM解析用户意图、路由子Agent、管理框架生命周期"""

    name = "supervisor"
    _agent_tools: list[str] = []

    _instance: Optional["SupervisorAgent"] = None

    def __init__(self):
        super().__init__()
        self._llm = LLMClient.get_instance()
        self._frame = FrameManager.get_instance()
        self._router = IntentRouter.get_instance()
        self._current_user_id: str = ""

        # 动态发现所有 Agent（从 Prompt 文件）
        from .registry import AgentRegistry

        AgentRegistry.discover_from_prompts()

        # 从 prompt 元数据加载工具声明
        from .prompt_loader import _parse_frontmatter

        prompt_file = (
            Path(__file__).parent.parent.parent / "prompts" / "v1" / "supervisor.txt"
        )
        raw = prompt_file.read_text(encoding="utf-8")
        meta, _ = _parse_frontmatter(raw)

        # 用 prompt 中声明的工具覆盖硬编码列表
        if "tools" in meta:
            self._agent_tools = meta["tools"]

        self._system_prompt = PromptLoader.load("supervisor")

    def _build_system_prompt_with_skills(self) -> str:
        """动态构建 system prompt，注入 always 技能内容。"""
        return self._build_skills_section(self._system_prompt)

    @classmethod
    def get_instance(cls) -> "SupervisorAgent":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def handle(self, message: AgentMessage, on_tool_result=None) -> AgentResult:
        session_mgr = get_session_manager()
        session_ctx = await session_mgr.get_session_context(message.user_id)

        # ========== 会话状态分发 ==========
        if session_ctx:
            state = session_ctx["state"]

            if state == SessionState.PAUSED.value:
                return await self._handle_paused_session(
                    message, session_ctx, session_mgr, on_tool_result
                )

            if state == SessionState.MULTI_TURN.value:
                return await self._handle_multi_turn(
                    message, session_ctx, session_mgr, on_tool_result
                )

        # ========== 正常路由流程 ==========
        return await self._normal_route(message, on_tool_result)

    async def _handle_paused_session(
        self, message: AgentMessage, ctx: dict, session_mgr,
        on_tool_result=None,
    ) -> AgentResult:
        """处理暂停中的会话"""
        paused_at = ctx.get("paused_at", 0)
        ttl = ctx.get("pause_ttl", PAUSE_TTL)

        # 超时 → 归档
        if time.time() - paused_at > ttl:
            await self._archive_paused_session(message.user_id, ctx, session_mgr)
            return await self._normal_route(message, on_tool_result=on_tool_result)

        # Supervisor 判断是否应恢复原会话
        should_resume = await self._judge_resume(message, ctx)

        if should_resume:
            agent_name = ctx["active_agent"]
            await session_mgr.resume_session(message.user_id, agent_name)
            return await self._route_to_agent(agent_name, message, on_tool_result=on_tool_result)

        # 继续处理当前意图，保持暂停状态
        return await self._normal_route(message, on_tool_result=on_tool_result)

    async def _judge_resume(self, message: AgentMessage, paused_ctx: dict) -> bool:
        """让 Supervisor 判断当前消息是否属于暂停中的对话的延续"""
        text = message.payload.get("text", "")
        pause_context = paused_ctx.get("pause_context", "")

        prompt = (
            f"用户有一个暂停中的对话（正在和 {paused_ctx['active_agent']} 交互）：\n"
            f"暂停上下文：{pause_context}\n\n"
            f"用户当前消息：{text}\n\n"
            f"判断这条消息是否属于暂停中的对话的延续。只回答 true 或 false。"
        )
        resp = await self._llm.chat(
            system="你是一个意图判断助手，只回答 true 或 false。",
            user=prompt,
            max_tokens=10,
            temperature=0.0,
        )
        return resp.strip().lower().startswith("true")

    async def _archive_paused_session(
        self, user_id: str, ctx: dict, session_mgr
    ) -> None:
        """超时后总结暂停会话的记忆，存入 L3，关闭会话"""
        agent_name = ctx.get("active_agent", "")
        pause_context = ctx.get("pause_context", "")

        if not pause_context:
            await session_mgr.clear_session_context(user_id)
            return

        # LLM 做一句话总结
        summary = await self._llm.chat(
            system="总结以下对话上下文为一句话。",
            user=pause_context,
            max_tokens=100,
            temperature=0.1,
        )

        # 存入 L3 长期记忆
        from apps.memory.manager import MemoryManager

        mm = MemoryManager(agent_type="supervisor", user_id=user_id)
        await mm.write_l3(
            content=f"[历史对话摘要-{agent_name}] {summary}",
            memory_type="conversation_summary",
        )

        await session_mgr.clear_session_context(user_id)
        logger.info("[%s] Paused session archived for user %s", self.name, user_id)

    async def _handle_multi_turn(
        self, message: AgentMessage, ctx: dict, session_mgr,
        on_tool_result=None,
    ) -> AgentResult:
        """处理多轮对话中的消息"""
        agent_name = ctx["active_agent"]
        if not agent_name:
            await session_mgr.clear_session_context(message.user_id)
            return await self._normal_route(message, on_tool_result=on_tool_result)

        # 初始化 MemoryManager
        mm = None
        if message.user_id:
            from apps.memory.manager import MemoryManager

            mm = MemoryManager(agent_type="supervisor", user_id=message.user_id)

        result = await self._route_to_agent(agent_name, message, on_tool_result=on_tool_result)

        # 写入统一对话历史
        if mm and result.success and not result.need_reroute:
            user_text = message.payload.get("text", "")[:500]
            agent_text = str(result.data)[:500] if result.data else ""
            conversation_history = await mm.get_conv_history(max_turns=5)
            updated = conversation_history[-4:] + [
                {
                    "role": "user",
                    "agent": agent_name,
                    "text": user_text,
                    "ts": int(time.time()),
                },
                {
                    "role": "agent",
                    "agent": agent_name,
                    "text": agent_text,
                    "ts": int(time.time()),
                },
            ]
            await mm.save_conv_history(updated[-5:])

        # SubAgent 拒收
        if result.need_reroute:
            logger.info(
                "Multi-turn agent %s rejected: %s, pausing session",
                agent_name,
                result.reroute_reason,
            )
            await self._pause_session(message, session_mgr, agent_name, result)

            # 尝试用新意图路由
            new_parsed = await self._parse_intent(message.payload.get("text", ""))
            if isinstance(new_parsed, dict) and new_parsed.get("_free_chat"):
                if new_parsed.get("response"):
                    return AgentResult(
                        task_id=message.task_id,
                        success=True,
                        data=new_parsed["response"],
                    )
                return await self._free_chat(message, on_tool_result=on_tool_result)
            # 新流程：parsed 直接是 agent name 或 frame intent
            new_agent = str(new_parsed)
            if new_agent and new_agent != "free_chat" and new_agent != agent_name:
                return await self._route_to_agent(new_agent, message, on_tool_result=on_tool_result)

            # 无法路由，走 free_chat
            return await self._free_chat(message, on_tool_result=on_tool_result)

        # 检查是否继续多轮
        if isinstance(result.data, dict) and result.data.get(
            "continue_conversation", False
        ):
            await session_mgr.set_session_context(
                message.user_id,
                SessionState.MULTI_TURN,
                agent_name,
            )
        else:
            logger.info(
                "[%s] Ending multi-turn session for user %s", self.name, message.user_id
            )
            await session_mgr.clear_session_context(message.user_id)

        return result

    async def _pause_session(
        self, message: AgentMessage, session_mgr, agent_name: str, result: AgentResult
    ) -> None:
        """暂停多轮对话"""
        # 获取当前对话上下文摘要
        pause_context = f"原意图: {agent_name}, 拒收原因: {result.reroute_reason}"
        if isinstance(result.data, dict):
            content = result.data.get("content", "")
            if content:
                pause_context = f"{content[:100]}... 续"

        await session_mgr.pause_session(
            message.user_id,
            active_agent=agent_name,
            pause_ttl=PAUSE_TTL,
            pause_context=pause_context,
        )

    async def _normal_route(self, message: AgentMessage, on_tool_result=None) -> AgentResult:
        """正常路由流程：意图解析 → 路由 → 结果"""
        logger.info(
            "[%s] Handling message with intent: %s, payload keys: %s",
            self.name,
            message.intent,
            list(message.payload.keys()),
        )

        session_mgr = get_session_manager()
        mm = None
        conversation_history: list = []
        if message.user_id:
            from apps.memory.manager import MemoryManager

            mm = MemoryManager(agent_type="supervisor", user_id=message.user_id)
            conversation_history = await mm.get_conv_history(max_turns=5)

        parsed = message.intent or await self._parse_intent(
            message.payload.get("text", ""),
            context=conversation_history[-5:] if conversation_history else None,
        )

        # 多步骤工作流
        if isinstance(parsed, dict) and parsed.get("_workflow_plan"):
            logger.info(
                "[%s] Multi-step workflow detected: %s",
                self.name, parsed.get("summary", ""),
            )
            return await self._execute_workflow(
                parsed["_workflow_plan"], message, on_tool_result=on_tool_result
            )

        # LLM 直接返回自由对话（节省一次 LLM 调用）
        if isinstance(parsed, dict) and parsed.get("_free_chat"):
            response_text = parsed.get("response")
            if response_text:
                return AgentResult(
                    task_id=message.task_id, success=True, data=response_text
                )
            # response 为空（LLM 降级），用 _free_chat 处理
            return await self._free_chat(message, on_tool_result=on_tool_result)

        # 新流程：parsed 直接是 agent name 或 frame intent
        resolved: str = str(parsed)

        # 框架生命周期
        if self._router.is_frame_intent(resolved):
            return await self._handle_frame(resolved, message)

        # Supervisor 自己处理（使用自身工具）
        if resolved == "supervisor":
            return await self._self_execute(message, on_tool_result=on_tool_result)

        # 路由子Agent（parsed 直接是 agent name）
        agent_name = resolved
        if agent_name and agent_name != "unknown":
            result = await self._route_to_agent(agent_name, message, on_tool_result=on_tool_result)

            # 子Agent 拒收 → 尝试重路由到建议的 agent
            if result.need_reroute:
                logger.info(
                    "Agent %s rejected in normal_route: %s, suggesting %s",
                    agent_name, result.reroute_reason, result.reroute_suggestion,
                )
                if result.reroute_suggestion and result.reroute_suggestion != agent_name:
                    return await self._route_to_agent(
                        result.reroute_suggestion, message, on_tool_result=on_tool_result
                    )
                # 无法重路由，转自由对话
                return await self._free_chat(message, on_tool_result=on_tool_result)

            # 路由成功时写入统一对话历史
            if mm and result.success and not result.need_reroute:
                user_text = message.payload.get("text", "")[:500]
                agent_text = str(result.data)[:500] if result.data else ""
                updated = conversation_history[-4:] + [
                    {
                        "role": "user",
                        "agent": agent_name,
                        "text": user_text,
                        "ts": int(time.time()),
                    },
                    {
                        "role": "agent",
                        "agent": agent_name,
                        "text": agent_text,
                        "ts": int(time.time()),
                    },
                ]
                await mm.save_conv_history(updated[-5:])

                # 检查响应是否要求开始多轮对话
                if hasattr(result.data, "get") and result.data.get(
                    "start_multi_turn", False
                ):
                    await session_mgr.set_session_context(
                        message.user_id,
                        SessionState.MULTI_TURN,
                        agent_name,
                    )

            return result

        # 未知意图
        return await self._free_chat(message, on_tool_result=on_tool_result)

    async def _route_with_fallback(
        self, message: AgentMessage, intent: str, attempted: set | None = None,
        on_tool_result=None,
    ) -> AgentResult:
        """带拒收重路由的 Agent 调用。

        兼容旧调用方式：intent 参数可能是 agent name 或旧式 intent。
        """
        attempted = attempted or set()
        # 兼容：intent 可能是 agent name 或旧式 intent
        agent_name = self._router.get_agent_for_intent(intent) or intent

        if not agent_name or agent_name in attempted or len(attempted) >= MAX_REROUTE:
            return await self._free_chat(message, on_tool_result=on_tool_result)

        attempted.add(agent_name)
        result = await self._route_to_agent(agent_name, message, on_tool_result=on_tool_result)

        # SubAgent 拒收
        if result.need_reroute:
            logger.info("Agent %s rejected: %s", agent_name, result.reroute_reason)

            # 优先用 SubAgent 建议的目标
            if result.reroute_suggestion and result.reroute_suggestion not in attempted:
                return await self._route_with_fallback(
                    message, result.reroute_suggestion, attempted, on_tool_result=on_tool_result
                )

            # 重新解析意图（排除已尝试的 Agent）
            exclude_agents = list(attempted)
            new_parsed = await self._parse_intent(
                message.payload.get("text", ""),
                exclude_agents=exclude_agents,
            )
            if isinstance(new_parsed, dict) and new_parsed.get("_free_chat"):
                if new_parsed.get("response"):
                    return AgentResult(
                        task_id=message.task_id,
                        success=True,
                        data=new_parsed["response"],
                    )
                return await self._free_chat(message, on_tool_result=on_tool_result)
            new_agent = str(new_parsed)
            if new_agent and new_agent != "free_chat" and new_agent not in attempted:
                return await self._route_with_fallback(message, new_agent, attempted, on_tool_result=on_tool_result)

            # 都失败
            return await self._free_chat(message, on_tool_result=on_tool_result)

        return result

    async def _route_to_agent(
        self, agent_name: str, message: AgentMessage, on_tool_result=None,
    ) -> AgentResult:
        """懒加载并调用子Agent，自动注入跨Agent上下文。"""
        from .registry import AgentRegistry

        # 注入跨agent上下文：如果上一轮是其他agent回复了用户，
        # 将其回复内容注入到当前消息的payload中，供目标agent参考。
        if message.user_id:
            try:
                from apps.memory.manager import MemoryManager
                mm = MemoryManager(agent_type="supervisor", user_id=message.user_id)
                conv = await mm.get_conv_history(max_turns=5)
                if len(conv) >= 1:
                    last_agent_entry = conv[-1]
                    if last_agent_entry.get("role") == "agent":
                        prev_agent = last_agent_entry.get("agent", "")
                        prev_response = last_agent_entry.get("text", "")
                        if prev_agent and prev_agent != agent_name and prev_response:
                            if "previous_agent_response" not in message.payload:
                                message.payload["previous_agent_response"] = prev_response
                                message.payload["previous_agent_name"] = prev_agent
                                logger.info(
                                    "[%s] Injecting context: %s -> %s",
                                    self.name, prev_agent, agent_name,
                                )
            except Exception as e:
                logger.debug("Context injection failed: %s", e)

        try:
            agent = AgentRegistry.get(agent_name)
            message.sender = "supervisor"
            message.recipient = agent_name
            return await agent.handle(message, on_tool_result=on_tool_result)
        except Exception as e:
            logger.error("Routing to %s failed: %s", agent_name, e)
            return AgentResult(task_id=message.task_id, success=False, error=str(e))

    async def _execute_workflow(
        self,
        workflow_plan: dict,
        message: AgentMessage,
        on_tool_result=None,
    ) -> AgentResult:
        """Execute a multi-step workflow plan sequentially.

        Each step is dispatched via _route_to_agent. Previous step's result
        is injected into the next step's payload as previous_agent_response.

        Args:
            workflow_plan: {"summary": str, "steps": [{"agent": str, "message": str}]}
            message: Original AgentMessage (for task_id, user_id)
            on_tool_result: Optional callback for tool results

        Returns:
            AgentResult with success=True if all steps completed,
            or success=False if any step failed.
        """
        steps = workflow_plan.get("steps", [])
        if not steps:
            return AgentResult(
                task_id=message.task_id,
                success=False,
                error="工作流计划为空",
            )

        step_results: list[dict] = []

        for idx, step in enumerate(steps):
            agent_name = step.get("agent", "")
            step_message = step.get("message", "")

            if not agent_name:
                return AgentResult(
                    task_id=message.task_id,
                    success=False,
                    error=f"步骤 {idx + 1} 缺少 agent 字段",
                )

            # Build step message with previous context
            step_msg = AgentMessage(
                task_id=message.task_id,
                sender="supervisor",
                recipient=agent_name,
                payload={"text": step_message, "scheduled": True},
                user_id=message.user_id,
            )

            # Inject previous step's result
            if step_results:
                prev = step_results[-1]
                step_msg.payload["previous_agent_response"] = prev.get("data", "")
                step_msg.payload["previous_agent_name"] = prev.get("agent", "")
                logger.info(
                    "[%s] Workflow step %d/%d: injecting context %s -> %s",
                    self.name, idx + 1, len(steps),
                    prev.get("agent", "?"), agent_name,
                )

            logger.info(
                "[%s] Workflow step %d/%d: dispatching to %s",
                self.name, idx + 1, len(steps), agent_name,
            )

            # Dispatch via _route_to_agent
            result = await self._route_to_agent(agent_name, step_msg, on_tool_result=on_tool_result)

            if result.success:
                content = ""
                if isinstance(result.data, dict):
                    content = result.data.get("content", str(result.data))
                else:
                    content = str(result.data)

                step_results.append({
                    "agent": agent_name,
                    "data": content,
                    "step": idx + 1,
                })
            else:
                # Step failed — fail fast
                return AgentResult(
                    task_id=message.task_id,
                    success=False,
                    error=f"工作流步骤 {idx + 1} ({agent_name}) 失败: {result.error}",
                    data={"completed_steps": step_results},
                )

        # All steps completed
        summary_lines = [
            f"工作流完成 ({len(step_results)}/{len(steps)} 步)",
        ]
        for sr in step_results:
            summary_lines.append(
                f"\n--- 步骤 {sr['step']}: {sr['agent']} ---\n{sr['data'][:500]}"
            )

        return AgentResult(
            task_id=message.task_id,
            success=True,
            data={
                "workflow_summary": "\n".join(summary_lines),
                "step_results": step_results,
            },
        )

    def _load_skills_for_context(self, skill_names: list[str]) -> str:
        """
        Load full content for a list of skill names.

        Resolves references recursively and strips frontmatter.
        """
        if not skill_names:
            return ""
        loader = self._get_skills_loader()
        resolved = loader.resolve_references(skill_names)
        return loader.load_skills_content(resolved)

    def _get_skills_summary(self) -> str:
        """Get the skills inventory XML block for progressive loading."""
        # Rebuild summary on demand in case skills were added
        return self._get_skills_loader().build_summary()

    async def handle_text(self, text: str) -> str:
        """Channel收到自然语言文本的便捷入口"""
        msg = AgentMessage(
            sender="user", recipient="supervisor", payload={"text": text}
        )
        result = await self.handle(msg)
        if result.success:
            return str(result.data)
        return f"[错误] {result.error}"

    # ------------------------------------------------------------------ #
    #  内部方法                                                           #
    # ------------------------------------------------------------------ #

    async def _parse_intent(
        self, text: str, context: list | None = None, exclude_agents: list | None = None
    ) -> str | dict:
        """
        调用LLM解析意图，基于 Agent 概述动态识别。

        Returns:
            str: Agent 名称（如 'quant'）或框架意图（如 'start_trading'）
            dict: {'_free_chat': True, 'response': '...'} 未识别意图时 LLM 直接返回回复
        """
        if not text:
            return "unknown"

        # 构建 Agent 概述列表（排除已尝试的）
        agents = PromptLoader.list_agents()
        if exclude_agents:
            agents = [a for a in agents if a.get("name") not in exclude_agents]

        agent_descriptions = "\n".join(
            f"- {a['name']}: {a.get('overview', '').replace(chr(10), chr(10) + '  ')}"
            for a in agents
            if a.get("name")
        )

        # 框架意图列表（仍然需要检查）
        frame_intents = self._router.all_frame_intents()
        frame_block = ""
        if frame_intents:
            frame_block = (
                f"Framework intents (system actions): {json.dumps(frame_intents)}\n"
            )

        context_block = ""
        if context:
            context_block = (
                f"Recent routing history (for reference): {json.dumps(context)}\n\n"
            )

        exclude_block = ""
        if exclude_agents:
            exclude_block = (
                f"Exclude these agents (already tried): {exclude_agents}\n\n"
            )

        skills_summary = self._get_skills_summary()
        skills_block = (
            f"Available agent skills:\n{skills_summary}\n\n" if skills_summary else ""
        )

        # Supervisor 自身能力（工具声明）
        supervisor_tools = (
            self._agent_tools
            if hasattr(self, "_agent_tools") and self._agent_tools
            else []
        )
        supervisor_block = ""
        if supervisor_tools:
            supervisor_block = (
                f"Your own tools (handle these yourself): {json.dumps(supervisor_tools)}\n"
                f'If the user request matches these tools, reply: {{"agent": "supervisor"}}\n\n'
            )

        user_prompt = (
            f"{context_block}"
            f"{exclude_block}"
            f"{skills_block}"
            f"{frame_block}"
            f"{supervisor_block}"
            f"Available agents:\n{agent_descriptions}\n\n"
            f"User message: {text}\n\n"
            "Analyze the user's intent and determine which agent should handle it "
            "based on each agent's overview/role description.\n\n"
            "If the user request clearly requires multiple agents to work in sequence "
            "(e.g., 'research then implement', '研究并实现', '先调研再回测'), reply with:\n"
            '{"_workflow_plan": {"summary": "一句话概括", "steps": [{"agent": "<agent1>", "message": "<step1 prompt>"}, {"agent": "<agent2>", "message": "<step2 prompt>"}]}}\n\n'
            "Otherwise, reply with a JSON object:\n"
            '- If one agent matches: {"agent": "<agent_name>"} (can be "supervisor" for your own tools)\n'
            '- If it matches a framework intent: {"intent": "<frame_intent>"}\n'
            '- If none matches: {"_free_chat": true, "response": "<your reply to the user>"}'
        )
        logger.debug("Supervisor intent parsing prompt: %s", user_prompt[:500])
        response = await self._llm.chat(
            system=self._system_prompt,
            user=user_prompt,
            max_tokens=256,
            temperature=0.1,
        )
        if is_fallback(response):
            fallback_text = self._rule_based_intent(text)
            if fallback_text:
                return fallback_text
            return {"_free_chat": True, "response": None}

        try:
            text = response.strip()
            fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
            if fence_match:
                text = fence_match.group(1).strip()
            else:
                obj_match = re.search(r"\{[\s\S]*\}", text)
                if obj_match:
                    text = obj_match.group(0).strip()
            data = json.loads(text)
            logger.debug(f"Parsed intent data: {data}")

            # 自由对话：LLM 直接返回回复内容
            if data.get("_free_chat"):
                return {"_free_chat": True, "response": data.get("response", "")}

            # 工作流计划：多 Agent 顺序任务
            if data.get("_workflow_plan"):
                return data

            # 框架意图
            if data.get("intent") and data["intent"] in frame_intents:
                return data["intent"]

            # Agent 路由：返回 agent name
            agent_name = data.get("agent")
            if agent_name:
                return agent_name

            return "unknown"
        except Exception:
            logger.warning(f"Intent parse failed, raw: {response[:200]}")
            fallback_text = self._rule_based_intent(text)
            if fallback_text:
                return fallback_text
            return {"_free_chat": True, "response": None}

    def _rule_based_intent(self, text: str) -> str | None:
        """规则引擎降级"""
        return self._router.match_fallback(text)

    async def _handle_frame(self, intent: str, message: AgentMessage) -> AgentResult:
        frame_action = self._router.get_frame_intent(intent)
        if not frame_action:
            return AgentResult(
                task_id=message.task_id,
                success=False,
                error=f"Unknown frame intent: {intent}",
            )

        frame_type, action = frame_action
        try:
            if action == "start":
                await self._frame.start(frame_type)
            else:
                await self._frame.stop(frame_type)
            if message.user_id:
                from apps.memory.manager import MemoryManager

                mm = MemoryManager(agent_type="supervisor", user_id=message.user_id)
                state = "running" if action == "start" else "stopped"
                await mm.write_l2(
                    content=f"{frame_type}:{state}",
                    memory_type="frame_state",
                    importance=2,
                )
            return AgentResult(
                task_id=message.task_id,
                success=True,
                data=f"{frame_type} 已{'启动' if action == 'start' else '停止'}",
            )
        except Exception as e:
            return AgentResult(task_id=message.task_id, success=False, error=str(e))

    async def _self_execute(self, message: AgentMessage, on_tool_result=None) -> AgentResult:
        """Supervisor 使用自身工具执行任务。"""
        self._current_user_id = message.user_id or ""
        text = message.payload.get("text", "")
        tools = self._get_tools_schema()
        system = self._build_system_prompt_with_skills()
        messages = [{"role": "user", "content": text}]

        content, is_fb = await self._run_tool_loop(
            system, messages, tools, max_tokens=2048, on_tool_result=on_tool_result
        )

        if is_fb:
            return AgentResult(
                task_id=message.task_id,
                success=False,
                error="LLM暂时不可用，请稍后再试",
            )

        return AgentResult(task_id=message.task_id, success=True, data=content)

    async def _free_chat(self, message: AgentMessage, on_tool_result=None) -> AgentResult:
        """未识别意图，LLM自由对话，支持工具调用"""
        self._current_user_id = message.user_id or ""
        text = message.payload.get("text", "")

        mm = None
        conversation_history: list = []
        if message.user_id:
            from apps.memory.manager import MemoryManager

            mm = MemoryManager(agent_type="supervisor", user_id=message.user_id)
            conversation_history = (await mm.get_conv_history(max_turns=5))[-5:]

        if conversation_history:
            history_block = "\n".join(
                f"{t['role']}({t.get('agent', 'supervisor')}): {t['text']}"
                for t in conversation_history
            )
            user_prompt = f"[对话历史]\n{history_block}\n\n[当前消息]\n{text}"
        else:
            user_prompt = text

        system = self._build_system_prompt_with_skills()
        tools = self._get_tools_schema()
        messages = [{"role": "user", "content": user_prompt}]

        content, is_fb = await self._run_tool_loop(
            system, messages, tools, max_tokens=2048, on_tool_result=on_tool_result
        )

        if is_fb:
            return AgentResult(
                task_id=message.task_id,
                success=False,
                error="LLM暂时不可用，请稍后再试",
            )

        if mm:
            updated = conversation_history[-4:] + [
                {
                    "role": "user",
                    "agent": "supervisor",
                    "text": text[:500],
                    "ts": int(time.time()),
                },
                {
                    "role": "agent",
                    "agent": "supervisor",
                    "text": content[:500],
                    "ts": int(time.time()),
                },
            ]
            await mm.save_conv_history(updated[-5:])
            await mm.write_l2(
                content=f"user: {text}\nassistant: {content}",
                memory_type="conversation",
                importance=1,
            )

        return AgentResult(task_id=message.task_id, success=True, data=content)
