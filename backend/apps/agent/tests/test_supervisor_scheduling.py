"""Black-box tests for SupervisorAgent scheduling and dispatching accuracy.

Covers:
- P0: Target mutation (T04-T06), concurrent sessions (T07-T08),
       free-chat interspersion (T09-T10)
- P1: Basic routing (T01-T03), paused sessions (T11-T13),
       LLM fallback (T14-T15)
- P2: Frame intent interrupting multi-turn (T16)
"""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import unittest

from apps.agent.supervisor import (
    IntentRouter,
    SupervisorAgent,
    SessionState,
    AgentMessage,
    AgentResult,
)
from apps.agent.llm_client import FALLBACK_MARKER


# ------------------------------------------------------------------ #
#  Shared Helpers                                                     #
# ------------------------------------------------------------------ #


def reset_all_singletons():
    IntentRouter.reset()
    SupervisorAgent._instance = None

    from apps.agent.registry import AgentRegistry
    from apps.memory.manager import _L1_CACHE
    from apps.agent.frame_manager import FrameManager
    from apps.agent.llm_client import LLMClient

    AgentRegistry._registry.clear()
    AgentRegistry._classes.clear()
    AgentRegistry._discovered = False
    FrameManager._instance = None
    LLMClient._instance = None
    _L1_CACHE.clear()


class MockSessionManager:
    """In-memory session manager for testing."""

    def __init__(self):
        self._store: dict = {}
        self.call_log: list = []

    async def get_session_context(self, user_id):
        self.call_log.append(("get_session_context", user_id))
        entry = self._store.get(user_id)
        if entry is None:
            return None
        if entry.get("expires_at", 0) < time.time():
            self._store.pop(user_id, None)
            return None
        return dict(entry)

    async def set_session_context(self, user_id, state, active_agent=None, ttl=1800):
        self.call_log.append(
            ("set_session_context", user_id, state.value, active_agent)
        )
        self._store[user_id] = {
            "state": state.value,
            "active_agent": active_agent,
            "expires_at": time.time() + ttl,
        }

    async def pause_session(
        self, user_id, active_agent, pause_ttl=300, pause_context=""
    ):
        self.call_log.append(("pause_session", user_id, active_agent))
        self._store[user_id] = {
            "state": SessionState.PAUSED.value,
            "active_agent": active_agent,
            "paused_at": time.time(),
            "pause_ttl": pause_ttl,
            "pause_context": pause_context,
            "expires_at": time.time() + pause_ttl,
        }

    async def resume_session(self, user_id, agent_name, ttl=1800):
        self.call_log.append(("resume_session", user_id, agent_name))
        self._store[user_id] = {
            "state": SessionState.MULTI_TURN.value,
            "active_agent": agent_name,
            "expires_at": time.time() + ttl,
        }

    async def clear_session_context(self, user_id):
        self.call_log.append(("clear_session_context", user_id))
        self._store.pop(user_id, None)


class MockMemoryManager:
    def __init__(self, agent_type="supervisor", user_id="test"):
        self._l1 = {}

    def write_l1(self, key, value):
        self._l1[key] = value

    async def write_l2(self, *args, **kwargs):
        pass

    async def write_l3(self, *args, **kwargs):
        pass

    async def retrieve(self, query, top_k=5):
        return []

    async def get_conv_history(self, max_turns=5):
        return self._l1.get("conv_history", [])

    async def save_conv_history(self, history):
        self._l1["conv_history"] = list(history)

    async def append_conv_history(self, entries, keep_turns=10):
        current = self._l1.get("conv_history", [])
        updated = (current + entries)[-keep_turns:]
        self._l1["conv_history"] = updated
        return updated


def llm_agent(name: str) -> str:
    return json.dumps({"agent": name})


def llm_frame(intent: str) -> str:
    return json.dumps({"intent": intent})


def llm_free(text: str = "I'm not sure.") -> str:
    return json.dumps({"_free_chat": True, "response": text})


def make_agent_getter(**agents):
    def getter(name):
        if name in agents:
            return agents[name]
        raise KeyError(f"Agent not registered: {name!r}")

    return getter


def make_accepting_agent(content="ok", continue_conversation=False):
    agent = AsyncMock()
    agent.handle = AsyncMock(
        return_value=AgentResult(
            success=True,
            data={
                "content": content,
                "continue_conversation": continue_conversation,
            },
        )
    )
    return agent


def make_multi_turn_agent():
    """Agent whose first response starts multi-turn."""
    agent = AsyncMock()
    agent.handle = AsyncMock(
        return_value=AgentResult(
            success=True,
            data={
                "content": "ok",
                "start_multi_turn": True,
                "continue_conversation": True,
            },
        )
    )
    return agent


def make_rejecting_agent(suggestion="", reason="not my domain"):
    agent = AsyncMock()
    agent.handle = AsyncMock(
        return_value=AgentResult(
            success=False,
            need_reroute=True,
            reroute_reason=reason,
            reroute_suggestion=suggestion,
        )
    )
    return agent


def make_continuing_agent(content="need more info"):
    return make_accepting_agent(content=content, continue_conversation=True)


def create_supervisor_with_mocks(mock_session_mgr=None):
    if mock_session_mgr is None:
        mock_session_mgr = MockSessionManager()

    patch_sm = patch(
        "apps.agent.supervisor.get_session_manager",
        return_value=mock_session_mgr,
    )
    patch_mm = patch("apps.memory.manager.MemoryManager", MockMemoryManager)
    patch_redis = patch(
        "redis.asyncio.from_url",
        side_effect=Exception("no redis in tests"),
    )

    # Shared mock LLM client — used by both _parse_intent (self._llm)
    # and _run_tool_loop (LLMClient.get_instance())
    mock_llm = MagicMock()
    mock_llm.chat = AsyncMock()
    mock_llm.chat_with_tools = AsyncMock()
    patch_llm = patch(
        "apps.agent.llm_client.LLMClient.get_instance",
        return_value=mock_llm,
    )

    patch_sm.start()
    patch_mm.start()
    patch_redis.start()
    patch_llm.start()

    reset_all_singletons()
    router = IntentRouter.get_instance()
    router.register_frame_intent("start_trading", "trading", "start")
    router.register_frame_intent("stop_trading", "trading", "stop")
    router.register_frame_intent("start_monitor", "assist", "start")
    router.register_frame_intent("stop_monitor", "assist", "stop")
    router.register_fallback_rules(
        [
            (r"(分析|行情|走势|K线|趋势).*(BTC|ETH|币|市场)", "analyze_market"),
            (r"(回测|测试策略|历史数据)", "run_backtest"),
            (r"(风险|止损|仓位|风控)", "assess_risk"),
            (r"(计划|复盘|总结|周报)", "create_plan"),
            (r"(策略|代码|编写)", "generate_and_test_strategy"),
        ]
    )

    supervisor = SupervisorAgent()
    supervisor._llm = mock_llm

    def stopper():
        patch_sm.stop()
        patch_mm.stop()
        patch_redis.stop()
        patch_llm.stop()

    return supervisor, mock_session_mgr, stopper


# ------------------------------------------------------------------ #
#  P1: Basic Single-Turn Routing (T01-T03)                            #
# ------------------------------------------------------------------ #


class TestBasicRouting(unittest.TestCase):
    def _setup(self):
        sup, sm, stop = create_supervisor_with_mocks()
        self.addCleanup(stop)
        return sup, sm

    # T01
    def test_strategy_request_routes_to_quant(self):
        sup, sm = self._setup()
        mock_quant = make_accepting_agent()

        sup._llm.chat = AsyncMock(return_value=llm_agent("quant"))
        with patch("apps.agent.registry.AgentRegistry.get", return_value=mock_quant):
            result = asyncio.run(
                sup.handle(
                    AgentMessage(payload={"text": "帮我写RSI策略"}, user_id="u1")
                )
            )

        mock_quant.handle.assert_called_once()
        self.assertTrue(result.success)
        self.assertNotIn("u1", sm._store)

    # T02
    def test_market_analysis_routes_to_analyst(self):
        sup, sm = self._setup()
        mock_analyst = make_accepting_agent()

        sup._llm.chat = AsyncMock(return_value=llm_agent("analyst"))
        with patch("apps.agent.registry.AgentRegistry.get", return_value=mock_analyst):
            result = asyncio.run(
                sup.handle(AgentMessage(payload={"text": "BTC走势"}, user_id="u1"))
            )

        mock_analyst.handle.assert_called_once()
        self.assertTrue(result.success)

    # T03
    def test_unknown_intent_returns_free_chat_directly(self):
        sup, sm = self._setup()

        sup._llm.chat = AsyncMock(return_value=llm_free("你好，有什么可以帮你的？"))
        with patch("apps.agent.registry.AgentRegistry.get") as mock_get:
            result = asyncio.run(
                sup.handle(AgentMessage(payload={"text": "hello"}, user_id="u1"))
            )

        mock_get.assert_not_called()
        self.assertTrue(result.success)
        self.assertIn("你好", str(result.data))

    # T03a — empty free_chat response triggers _free_chat → _run_tool_loop
    def test_free_chat_empty_response_triggers_tool_loop(self):
        sup, sm = self._setup()

        sup._llm.chat = AsyncMock(
            return_value=json.dumps({"_free_chat": True, "response": ""})
        )
        # _run_tool_loop calls chat_with_tools via LLMClient.get_instance()
        mock_tool_resp = MagicMock()
        mock_tool_resp.has_tool_calls = False
        mock_tool_resp.content = "fallback reply"
        sup._llm.chat_with_tools = AsyncMock(return_value=mock_tool_resp)

        result = asyncio.run(
            sup.handle(AgentMessage(payload={"text": "!!!"}, user_id="u1"))
        )

        self.assertTrue(result.success)
        sup._llm.chat_with_tools.assert_called()


# ------------------------------------------------------------------ #
#  P0: Target Mutation (T04-T06)                                      #
# ------------------------------------------------------------------ #


class TestTargetMutation(unittest.TestCase):
    def _setup(self):
        sup, sm, stop = create_supervisor_with_mocks()
        self.addCleanup(stop)
        return sup, sm

    # T04 — multi-turn agent rejects, re-parse via LLM, route to new agent
    def test_multi_turn_agent_rejects_reroutes_via_reparse(self):
        sup, sm = self._setup()
        mock_quant = make_rejecting_agent(
            suggestion="risk_advisor", reason="not my domain"
        )
        mock_risk = make_accepting_agent()

        sm._store["u1"] = {
            "state": SessionState.MULTI_TURN.value,
            "active_agent": "quant",
            "expires_at": time.time() + 1800,
        }

        # LLM re-parse after rejection returns risk_advisor
        sup._llm.chat = AsyncMock(return_value=llm_agent("risk_advisor"))

        with patch(
            "apps.agent.registry.AgentRegistry.get",
            side_effect=make_agent_getter(quant=mock_quant, risk_advisor=mock_risk),
        ):
            result = asyncio.run(
                sup.handle(
                    AgentMessage(payload={"text": "评估一下仓位风险"}, user_id="u1")
                )
            )

        mock_quant.handle.assert_called_once()
        mock_risk.handle.assert_called_once()
        self.assertTrue(result.success)
        ctx = sm._store.get("u1")
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx["state"], SessionState.MULTI_TURN.value)
        self.assertEqual(ctx["active_agent"], "risk_advisor")

    # T05 — multi-turn agent rejects, re-parse via LLM returns free_chat
    def test_multi_turn_reject_reparse_free_chat(self):
        sup, sm = self._setup()
        mock_quant = make_rejecting_agent(
            suggestion="researcher", reason="not strategy"
        )

        sm._store["u1"] = {
            "state": SessionState.MULTI_TURN.value,
            "active_agent": "quant",
            "expires_at": time.time() + 1800,
        }

        sup._llm.chat = AsyncMock(return_value=llm_free("我不太确定该找谁处理"))

        with patch(
            "apps.agent.registry.AgentRegistry.get",
            side_effect=make_agent_getter(quant=mock_quant),
        ):
            result = asyncio.run(
                sup.handle(
                    AgentMessage(payload={"text": "帮我做内容研究"}, user_id="u1")
                )
            )

        mock_quant.handle.assert_called_once()
        self.assertTrue(result.success)
        self.assertIn("确定", str(result.data))
        ctx = sm._store.get("u1")
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx["state"], SessionState.PAUSED.value)

    # T06a — user ends multi-turn then starts new topic (two-message flow)
    def test_user_ends_multi_turn_then_new_topic(self):
        sup, sm = self._setup()
        mock_quant = make_accepting_agent(continue_conversation=False)
        mock_analyst = make_accepting_agent()

        sm._store["u1"] = {
            "state": SessionState.MULTI_TURN.value,
            "active_agent": "quant",
            "expires_at": time.time() + 1800,
        }

        # Message 1: "算了不写策略了" → quant accepts, ends multi-turn
        with patch("apps.agent.registry.AgentRegistry.get", return_value=mock_quant):
            result1 = asyncio.run(
                sup.handle(
                    AgentMessage(payload={"text": "算了不写策略了"}, user_id="u1")
                )
            )

        self.assertTrue(result1.success)
        self.assertNotIn("u1", sm._store)  # session cleared

        # Message 2: "看下BTC行情" → fresh route to analyst
        sup._llm.chat = AsyncMock(return_value=llm_agent("analyst"))
        with patch("apps.agent.registry.AgentRegistry.get", return_value=mock_analyst):
            result2 = asyncio.run(
                sup.handle(AgentMessage(payload={"text": "看下BTC行情"}, user_id="u1"))
            )

        mock_analyst.handle.assert_called_once()
        self.assertTrue(result2.success)

    # T06b — agent rejects message (target mutation in single message)
    def test_agent_rejects_and_new_agent_takes_over(self):
        sup, sm = self._setup()
        mock_quant = make_rejecting_agent(suggestion="analyst", reason="not strategy")
        mock_analyst = make_accepting_agent()

        sm._store["u1"] = {
            "state": SessionState.MULTI_TURN.value,
            "active_agent": "quant",
            "expires_at": time.time() + 1800,
        }

        sup._llm.chat = AsyncMock(return_value=llm_agent("analyst"))

        with patch(
            "apps.agent.registry.AgentRegistry.get",
            side_effect=make_agent_getter(quant=mock_quant, analyst=mock_analyst),
        ):
            result = asyncio.run(
                sup.handle(
                    AgentMessage(
                        payload={"text": "算了不写策略了，看下BTC行情"},
                        user_id="u1",
                    )
                )
            )

        mock_quant.handle.assert_called_once()
        mock_analyst.handle.assert_called_once()
        self.assertTrue(result.success)
        ctx = sm._store.get("u1")
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx["state"], SessionState.MULTI_TURN.value)
        self.assertEqual(ctx["active_agent"], "analyst")


# ------------------------------------------------------------------ #
#  P0: Concurrent Sessions (T07-T08)                                  #
# ------------------------------------------------------------------ #


class TestConcurrentSessions(unittest.TestCase):
    def _setup(self):
        sup, sm, stop = create_supervisor_with_mocks()
        self.addCleanup(stop)
        return sup, sm

    # T07 — two messages arrive concurrently, last-write-wins
    def test_concurrent_messages_last_write_wins(self):
        sup, sm = self._setup()
        mock_quant = make_multi_turn_agent()
        mock_analyst = make_multi_turn_agent()

        msg1 = AgentMessage(payload={"text": "写RSI策略"}, user_id="u1")
        msg2 = AgentMessage(payload={"text": "分析BTC行情"}, user_id="u1")

        sup._llm.chat = AsyncMock()
        sup._llm.chat.side_effect = [llm_agent("quant"), llm_agent("analyst")]

        with patch(
            "apps.agent.registry.AgentRegistry.get",
            side_effect=make_agent_getter(quant=mock_quant, analyst=mock_analyst),
        ):

            async def _concurrent():
                return await asyncio.gather(sup.handle(msg1), sup.handle(msg2))

            results = asyncio.run(_concurrent())

        self.assertTrue(results[0].success)
        self.assertTrue(results[1].success)
        total_calls = mock_quant.handle.call_count + mock_analyst.handle.call_count
        self.assertEqual(total_calls, 2)
        ctx = sm._store.get("u1")
        self.assertIsNotNone(ctx)
        self.assertIn(ctx["active_agent"], ["quant", "analyst"])

    # T08 — alternating devices, session recovers via judge_resume
    def test_alternating_devices_session_recovers(self):
        sup, sm = self._setup()
        mock_quant = make_continuing_agent("RSI参数已更新")

        sm._store["u1"] = {
            "state": SessionState.MULTI_TURN.value,
            "active_agent": "quant",
            "expires_at": time.time() + 1800,
        }

        # Device A: strategy continuation
        with patch("apps.agent.registry.AgentRegistry.get", return_value=mock_quant):
            result1 = asyncio.run(
                sup.handle(
                    AgentMessage(payload={"text": "RSI参数改成14"}, user_id="u1")
                )
            )

        self.assertTrue(result1.success)
        self.assertEqual(sm._store["u1"]["state"], SessionState.MULTI_TURN.value)

        # Device B: risk question — quant rejects
        mock_quant_reject = make_rejecting_agent(
            suggestion="risk_advisor", reason="not strategy"
        )
        mock_risk = make_accepting_agent()

        sup._llm.chat = AsyncMock(return_value=llm_agent("risk_advisor"))

        with patch(
            "apps.agent.registry.AgentRegistry.get",
            side_effect=make_agent_getter(
                quant=mock_quant_reject, risk_advisor=mock_risk
            ),
        ):
            result2 = asyncio.run(
                sup.handle(
                    AgentMessage(payload={"text": "持仓风险怎么样"}, user_id="u1")
                )
            )

        self.assertTrue(result2.success)
        self.assertEqual(sm._store["u1"]["state"], SessionState.MULTI_TURN.value)
        self.assertEqual(sm._store["u1"]["active_agent"], "risk_advisor")

        # Device A: resumes strategy — risk_advisor rejects, re-parse routes to quant
        mock_risk_reject = make_rejecting_agent(suggestion="quant", reason="not risk")
        mock_quant_resume = make_accepting_agent(content="布林带已添加")
        sup._llm.chat = AsyncMock(return_value=llm_agent("quant"))

        with patch(
            "apps.agent.registry.AgentRegistry.get",
            side_effect=make_agent_getter(
                risk_advisor=mock_risk_reject, quant=mock_quant_resume
            ),
        ):
            result3 = asyncio.run(
                sup.handle(
                    AgentMessage(payload={"text": "再加一个布林带"}, user_id="u1")
                )
            )

        mock_quant_resume.handle.assert_called_once()
        self.assertTrue(result3.success)
        self.assertEqual(sm._store["u1"]["state"], SessionState.MULTI_TURN.value)
        self.assertEqual(sm._store["u1"]["active_agent"], "quant")


# ------------------------------------------------------------------ #
#  P0: Free-Chat Interspersion (T09-T10)                              #
# ------------------------------------------------------------------ #


class TestFreeChatInterspersion(unittest.TestCase):
    def _setup(self):
        sup, sm, stop = create_supervisor_with_mocks()
        self.addCleanup(stop)
        return sup, sm

    # T09 — free chat during multi-turn doesn't destroy session
    def test_free_chat_pauses_not_destroys_multi_turn(self):
        sup, sm = self._setup()
        mock_quant = make_rejecting_agent(reason="not strategy related")

        sm._store["u1"] = {
            "state": SessionState.MULTI_TURN.value,
            "active_agent": "quant",
            "expires_at": time.time() + 1800,
        }

        sup._llm.chat = AsyncMock(return_value=llm_free("哈哈，天气确实不错"))

        with patch(
            "apps.agent.registry.AgentRegistry.get",
            side_effect=make_agent_getter(quant=mock_quant),
        ):
            result = asyncio.run(
                sup.handle(
                    AgentMessage(payload={"text": "今天天气真不错"}, user_id="u1")
                )
            )

        mock_quant.handle.assert_called_once()
        self.assertTrue(result.success)
        self.assertIn("天气", str(result.data))
        ctx = sm._store.get("u1")
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx["state"], SessionState.PAUSED.value)
        self.assertEqual(ctx["active_agent"], "quant")

    # T10 — resume quant conversation after free chat
    def test_resume_after_free_chat(self):
        sup, sm = self._setup()

        sm._store["u1"] = {
            "state": SessionState.PAUSED.value,
            "active_agent": "quant",
            "paused_at": time.time() - 10,
            "pause_ttl": 300,
            "pause_context": "用户正在讨论RSI策略参数",
            "expires_at": time.time() + 290,
        }

        mock_quant = make_accepting_agent(content="止损已添加")
        sup._llm.chat = AsyncMock(return_value="true")

        with patch(
            "apps.agent.registry.AgentRegistry.get",
            side_effect=make_agent_getter(quant=mock_quant),
        ):
            result = asyncio.run(
                sup.handle(
                    AgentMessage(payload={"text": "刚才说的策略加个止损"}, user_id="u1")
                )
            )

        mock_quant.handle.assert_called_once()
        self.assertTrue(result.success)
        self.assertEqual(sm._store["u1"]["state"], SessionState.MULTI_TURN.value)
        self.assertEqual(sm._store["u1"]["active_agent"], "quant")


# ------------------------------------------------------------------ #
#  P1: Paused Session (T11-T13)                                       #
# ------------------------------------------------------------------ #


class TestPausedSession(unittest.TestCase):
    def _setup(self):
        sup, sm, stop = create_supervisor_with_mocks()
        self.addCleanup(stop)
        return sup, sm

    # T11 — judge says true, resume to paused agent
    def test_paused_judge_true_resumes_to_agent(self):
        sup, sm = self._setup()
        mock_analyst = make_accepting_agent(content="继续分析BTC")

        sm._store["u1"] = {
            "state": SessionState.PAUSED.value,
            "active_agent": "analyst",
            "paused_at": time.time() - 10,
            "pause_ttl": 300,
            "pause_context": "BTC日线分析",
            "expires_at": time.time() + 290,
        }

        sup._llm.chat = AsyncMock(return_value="true")

        with patch("apps.agent.registry.AgentRegistry.get", return_value=mock_analyst):
            result = asyncio.run(
                sup.handle(AgentMessage(payload={"text": "继续分析"}, user_id="u1"))
            )

        mock_analyst.handle.assert_called_once()
        self.assertTrue(result.success)
        self.assertIn("resume_session", [c[0] for c in sm.call_log])

    # T12 — judge says false, paused session stays paused, new intent routes
    def test_paused_judge_false_routes_new_intent(self):
        sup, sm = self._setup()
        mock_risk = make_accepting_agent()

        sm._store["u1"] = {
            "state": SessionState.PAUSED.value,
            "active_agent": "analyst",
            "paused_at": time.time() - 10,
            "pause_ttl": 300,
            "pause_context": "BTC日线分析",
            "expires_at": time.time() + 290,
        }

        sup._llm.chat = AsyncMock(side_effect=["false", llm_agent("risk_advisor")])

        with patch("apps.agent.registry.AgentRegistry.get", return_value=mock_risk):
            result = asyncio.run(
                sup.handle(AgentMessage(payload={"text": "评估仓位风险"}, user_id="u1"))
            )

        mock_risk.handle.assert_called_once()
        self.assertTrue(result.success)
        self.assertEqual(sm._store["u1"]["state"], SessionState.PAUSED.value)

    # T13 — paused session timeout (paused > PAUSE_TTL but expires_at still valid)
    def test_paused_expired_archives_to_l3(self):
        sup, sm = self._setup()
        mock_quant = make_accepting_agent()

        # expires_at must be in the future (get_session_context gate),
        # but paused_at must be old enough to trigger pause timeout.
        sm._store["u1"] = {
            "state": SessionState.PAUSED.value,
            "active_agent": "analyst",
            "paused_at": time.time() - 600,  # 10 min ago → paused timeout
            "pause_ttl": 300,
            "pause_context": "BTC日线级别分析进行中",
            "expires_at": time.time() + 600,  # still valid, won't be evicted
        }

        # LLM called twice: 1=archive summary, 2=parse intent
        sup._llm.chat = AsyncMock()
        sup._llm.chat.side_effect = ["BTC日线分析对话摘要", llm_agent("quant")]

        with patch("apps.agent.registry.AgentRegistry.get", return_value=mock_quant):
            result = asyncio.run(
                sup.handle(AgentMessage(payload={"text": "写策略"}, user_id="u1"))
            )

        self.assertTrue(result.success)
        mock_quant.handle.assert_called_once()
        self.assertIn("clear_session_context", [c[0] for c in sm.call_log])

    # T13a — empty pause_context skips LLM summary
    def test_paused_expired_empty_context_clears_directly(self):
        sup, sm = self._setup()
        mock_quant = make_accepting_agent()

        sm._store["u1"] = {
            "state": SessionState.PAUSED.value,
            "active_agent": "analyst",
            "paused_at": time.time() - 600,
            "pause_ttl": 300,
            "pause_context": "",
            "expires_at": time.time() + 600,
        }

        sup._llm.chat = AsyncMock(return_value=llm_agent("quant"))

        with patch("apps.agent.registry.AgentRegistry.get", return_value=mock_quant):
            asyncio.run(
                sup.handle(AgentMessage(payload={"text": "写策略"}, user_id="u1"))
            )

        # Only 1 chat call (parse_intent), no archive summary call
        self.assertEqual(sup._llm.chat.call_count, 1)


# ------------------------------------------------------------------ #
#  P1: LLM Fallback (T14-T15)                                         #
# ------------------------------------------------------------------ #


class TestLLMFallback(unittest.TestCase):
    def _setup(self):
        sup, sm, stop = create_supervisor_with_mocks()
        self.addCleanup(stop)
        return sup, sm

    # T14 — LLM returns FALLBACK_MARKER, regex hits
    def test_llm_fallback_marker_regex_hits(self):
        sup, sm = self._setup()

        sup._llm.chat = AsyncMock(return_value=FALLBACK_MARKER)

        result = asyncio.run(sup._parse_intent("分析BTC行情"))
        self.assertEqual(result, "analyze_market")

        result2 = asyncio.run(sup._parse_intent("回测策略效果"))
        self.assertEqual(result2, "run_backtest")

        result3 = asyncio.run(sup._parse_intent("仓位风控"))
        self.assertEqual(result3, "assess_risk")

    # T14a — fallback intent has no agent mapping
    def test_fallback_intent_no_agent_mapping(self):
        sup, sm = self._setup()

        sup._llm.chat = AsyncMock(return_value=FALLBACK_MARKER)

        msg = AgentMessage(
            payload={"text": "分析BTC行情"},
            intent="analyze_market",
            user_id="u1",
        )

        with patch(
            "apps.agent.registry.AgentRegistry.get",
            side_effect=KeyError("analyze_market"),
        ):
            result = asyncio.run(sup._normal_route(msg))

        self.assertFalse(result.success)

    # T15 — LLM returns garbage JSON, no regex match → free_chat
    def test_llm_garbage_json_no_regex_falls_back(self):
        sup, sm = self._setup()

        sup._llm.chat = AsyncMock(return_value="not valid json {{{")

        result = asyncio.run(sup._parse_intent("xyzzy random text"))

        self.assertIsInstance(result, dict)
        self.assertTrue(result.get("_free_chat"))
        self.assertIsNone(result.get("response"))


# ------------------------------------------------------------------ #
#  P2: Frame Intent Interrupting Multi-turn (T16)                     #
# ------------------------------------------------------------------ #


class TestFrameIntentInterrupt(unittest.TestCase):
    def _setup(self):
        sup, sm, stop = create_supervisor_with_mocks()
        self.addCleanup(stop)
        return sup, sm

    def test_frame_intent_replaces_normal_route(self):
        sup, sm = self._setup()

        sup._llm.chat = AsyncMock(return_value=llm_frame("start_trading"))

        mock_frame = AsyncMock()
        mock_frame.start = AsyncMock()
        with patch.object(sup, "_frame", mock_frame):
            result = asyncio.run(
                sup.handle(AgentMessage(payload={"text": "开始交易"}, user_id="u1"))
            )

        mock_frame.start.assert_called_once_with("trading")
        self.assertTrue(result.success)
        self.assertIn("启动", str(result.data))


# ------------------------------------------------------------------ #
#  State Machine Invariants                                           #
# ------------------------------------------------------------------ #


class TestStateMachineInvariants(unittest.TestCase):
    def _setup(self):
        sup, sm, stop = create_supervisor_with_mocks()
        self.addCleanup(stop)
        return sup, sm

    def test_normal_route_start_multi_turn_transitions_state(self):
        sup, sm = self._setup()
        mock_quant = make_multi_turn_agent()

        sup._llm.chat = AsyncMock(return_value=llm_agent("quant"))
        with patch("apps.agent.registry.AgentRegistry.get", return_value=mock_quant):
            asyncio.run(
                sup.handle(AgentMessage(payload={"text": "写策略"}, user_id="u1"))
            )

        ctx = sm._store.get("u1")
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx["state"], SessionState.MULTI_TURN.value)

    def test_multi_turn_ends_on_continue_false(self):
        sup, sm = self._setup()
        mock_quant = make_accepting_agent(continue_conversation=False)

        sm._store["u1"] = {
            "state": SessionState.MULTI_TURN.value,
            "active_agent": "quant",
            "expires_at": time.time() + 1800,
        }

        with patch("apps.agent.registry.AgentRegistry.get", return_value=mock_quant):
            asyncio.run(
                sup.handle(AgentMessage(payload={"text": "最后一句话"}, user_id="u1"))
            )

        self.assertNotIn("u1", sm._store)

    def test_max_reroute_not_exceeded(self):
        sup, sm = self._setup()
        mock_a = make_rejecting_agent(suggestion="agent_b")
        mock_b = make_rejecting_agent(suggestion="agent_c")
        mock_c = make_accepting_agent()

        sm._store["u1"] = {
            "state": SessionState.MULTI_TURN.value,
            "active_agent": "agent_a",
            "expires_at": time.time() + 1800,
        }

        sup._llm.chat = AsyncMock(return_value=llm_free("兜底回复"))

        with patch(
            "apps.agent.registry.AgentRegistry.get",
            side_effect=make_agent_getter(
                agent_a=mock_a, agent_b=mock_b, agent_c=mock_c
            ),
        ):
            asyncio.run(
                sup.handle(AgentMessage(payload={"text": "test"}, user_id="u1"))
            )

        self.assertLessEqual(mock_a.handle.call_count, 1)
        self.assertLessEqual(mock_b.handle.call_count, 1)

    def test_empty_text_does_not_call_llm(self):
        sup, sm = self._setup()

        result = asyncio.run(sup._parse_intent(""))
        self.assertEqual(result, "unknown")
        sup._llm.chat.assert_not_called()

    def test_pre_set_intent_skips_llm_parsing(self):
        sup, sm = self._setup()
        mock_quant = make_accepting_agent()

        msg = AgentMessage(payload={"text": "test"}, intent="quant", user_id="u1")

        with patch("apps.agent.registry.AgentRegistry.get", return_value=mock_quant):
            result = asyncio.run(sup._normal_route(msg))

        sup._llm.chat.assert_not_called()
        self.assertTrue(result.success)


if __name__ == "__main__":
    unittest.main()
