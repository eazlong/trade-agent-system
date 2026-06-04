"""Tests for SupervisorAgent multi-step workflow execution."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import unittest

from apps.agent.supervisor import (
    IntentRouter,
    SupervisorAgent,
    SessionState,
    AgentMessage,
    AgentResult,
)


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
    def __init__(self):
        self._store: dict = {}

    async def get_session_context(self, user_id):
        return None

    async def set_session_context(self, user_id, state, active_agent=None, ttl=1800):
        pass

    async def clear_session_context(self, user_id):
        pass


class MockMemoryManager:
    def __init__(self, agent_type="supervisor", user_id="test"):
        self._l1 = {}

    async def write_l2(self, *args, **kwargs):
        pass

    async def get_conv_history(self, max_turns=5):
        return self._l1.get("conv_history", [])

    async def save_conv_history(self, history):
        self._l1["conv_history"] = list(history)


def make_accepting_agent(content="ok"):
    agent = AsyncMock()
    agent.handle = AsyncMock(
        return_value=AgentResult(
            success=True,
            data={"content": content, "continue_conversation": False},
        )
    )
    return agent


def make_rejecting_agent(reason="not my domain", suggestion=""):
    agent = AsyncMock()
    agent.handle = AsyncMock(
        return_value=AgentResult(
            success=False,
            error=reason,
            need_reroute=True,
            reroute_reason=reason,
            reroute_suggestion=suggestion,
        )
    )
    return agent


def make_agent_getter(**agents):
    def getter(name):
        if name in agents:
            return agents[name]
        raise KeyError(f"Agent not registered: {name!r}")

    return getter


def create_supervisor_with_mocks():
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

    supervisor = SupervisorAgent()
    supervisor._llm = mock_llm

    def stopper():
        patch_sm.stop()
        patch_mm.stop()
        patch_redis.stop()
        patch_llm.stop()

    return supervisor, mock_session_mgr, stopper


class TestExecuteWorkflow(unittest.TestCase):
    """Test _execute_workflow method."""

    def _setup(self):
        sup, sm, stop = create_supervisor_with_mocks()
        self.addCleanup(stop)
        return sup, sm

    def test_two_step_workflow_researcher_then_quant(self):
        """Researcher returns findings, quant receives them as context."""
        sup, sm = self._setup()

        mock_researcher = make_accepting_agent(
            "## 策略研究\nRSI+EMA超短线策略：RSI(14)<30买入，>70卖出。"
        )
        mock_quant = make_accepting_agent("策略代码已实现。")

        workflow_plan = {
            "summary": "研究并实现策略",
            "steps": [
                {
                    "agent": "researcher",
                    "message": "研究一个BTC 5分钟线的RSI超短线策略。",
                },
                {
                    "agent": "quant",
                    "message": "基于研究结果实现策略代码。",
                },
            ],
        }

        with patch(
            "apps.agent.registry.AgentRegistry.get",
            side_effect=make_agent_getter(
                researcher=mock_researcher,
                quant=mock_quant,
            ),
        ):
            result = asyncio.run(
                sup._execute_workflow(
                    workflow_plan,
                    AgentMessage(user_id="u1"),
                )
            )

        self.assertTrue(result.success)
        mock_researcher.handle.assert_called_once()
        mock_quant.handle.assert_called_once()

        # Verify context injection
        quant_call = mock_quant.handle.call_args[0][0]
        self.assertEqual(
            quant_call.payload.get("previous_agent_name"),
            "researcher",
        )
        self.assertIn("RSI", quant_call.payload.get("previous_agent_response", ""))

    def test_workflow_step_fails_fast(self):
        """If step 1 fails, step 2 should not execute."""
        sup, sm = self._setup()

        mock_failing = make_rejecting_agent(reason="无法完成研究")
        mock_quant = make_accepting_agent("不应被执行")

        workflow_plan = {
            "summary": "研究并实现",
            "steps": [
                {"agent": "researcher", "message": "做研究"},
                {"agent": "quant", "message": "实现代码"},
            ],
        }

        with patch(
            "apps.agent.registry.AgentRegistry.get",
            side_effect=make_agent_getter(
                researcher=mock_failing,
                quant=mock_quant,
            ),
        ):
            result = asyncio.run(
                sup._execute_workflow(
                    workflow_plan,
                    AgentMessage(user_id="u1"),
                )
            )

        self.assertFalse(result.success)
        self.assertIn("步骤 1", result.error)
        self.assertIn("失败", result.error)
        mock_failing.handle.assert_called_once()
        mock_quant.handle.assert_not_called()

    def test_empty_workflow_returns_error(self):
        """Empty steps list returns failure."""
        sup, sm = self._setup()

        result = asyncio.run(
            sup._execute_workflow(
                {"summary": "空工作流", "steps": []},
                AgentMessage(user_id="u1"),
            )
        )

        self.assertFalse(result.success)
        self.assertIn("空", result.error)

    def test_three_step_workflow_with_summary(self):
        """Three steps all complete, result includes step summary."""
        sup, sm = self._setup()

        mock_a = make_accepting_agent("结果A")
        mock_b = make_accepting_agent("结果B")
        mock_c = make_accepting_agent("结果C")

        workflow_plan = {
            "summary": "三步工作流",
            "steps": [
                {"agent": "researcher", "message": "步骤A"},
                {"agent": "quant", "message": "步骤B"},
                {"agent": "researcher", "message": "步骤C"},
            ],
        }

        dispatches = []

        def mock_get(name):
            dispatches.append(name)
            if name == "quant":
                return mock_b
            count = sum(1 for d in dispatches if d == "researcher")
            return mock_a if count == 1 else mock_c

        with patch(
            "apps.agent.registry.AgentRegistry.get",
            side_effect=mock_get,
        ):
            result = asyncio.run(
                sup._execute_workflow(
                    workflow_plan,
                    AgentMessage(user_id="u1"),
                )
            )

        self.assertTrue(result.success)
        self.assertEqual(result.data["step_results"][0]["agent"], "researcher")
        self.assertEqual(result.data["step_results"][1]["agent"], "quant")
        self.assertEqual(result.data["step_results"][2]["agent"], "researcher")

    def test_missing_agent_field_returns_error(self):
        """Step without agent field returns error."""
        sup, sm = self._setup()

        workflow_plan = {
            "summary": "无效工作流",
            "steps": [
                {"message": "没有指定agent"},
            ],
        }

        result = asyncio.run(
            sup._execute_workflow(
                workflow_plan,
                AgentMessage(user_id="u1"),
            )
        )

        self.assertFalse(result.success)
        self.assertIn("缺少 agent", result.error)

    def test_single_step_workflow_succeeds(self):
        """Single step workflow completes successfully."""
        sup, sm = self._setup()

        mock_quant = make_accepting_agent("策略已创建")

        workflow_plan = {
            "summary": "创建策略",
            "steps": [
                {"agent": "quant", "message": "创建一个RSI策略"},
            ],
        }

        with patch(
            "apps.agent.registry.AgentRegistry.get",
            return_value=mock_quant,
        ):
            result = asyncio.run(
                sup._execute_workflow(
                    workflow_plan,
                    AgentMessage(user_id="u1"),
                )
            )

        self.assertTrue(result.success)
        self.assertEqual(len(result.data["step_results"]), 1)
        self.assertEqual(result.data["step_results"][0]["agent"], "quant")
        self.assertIn("策略已创建", result.data["workflow_summary"])


class TestNormalRouteWorkflowDetection(unittest.TestCase):
    """Test that _normal_route detects and executes workflow plans."""

    def _setup(self):
        sup, sm, stop = create_supervisor_with_mocks()
        self.addCleanup(stop)
        return sup, sm

    def test_parse_intent_returns_workflow_plan(self):
        """_parse_intent detects multi-step task and returns workflow plan."""
        sup, sm = self._setup()

        workflow_json = json.dumps({
            "_workflow_plan": {
                "summary": "研究并实现策略",
                "steps": [
                    {"agent": "researcher", "message": "做研究"},
                    {"agent": "quant", "message": "实现代码"},
                ],
            }
        })

        sup._llm.chat = AsyncMock(return_value=workflow_json)

        result = asyncio.run(sup._parse_intent("先研究BTC策略然后实现它"))

        self.assertIsInstance(result, dict)
        self.assertIn("_workflow_plan", result)
        self.assertEqual(len(result["_workflow_plan"]["steps"]), 2)

    def test_normal_route_executes_workflow(self):
        """_normal_route detects workflow plan and calls _execute_workflow."""
        sup, sm = self._setup()

        mock_researcher = make_accepting_agent("研究完成")
        mock_quant = make_accepting_agent("实现完成")

        workflow_json = json.dumps({
            "_workflow_plan": {
                "summary": "研究并实现",
                "steps": [
                    {"agent": "researcher", "message": "研究BTC策略"},
                    {"agent": "quant", "message": "实现策略代码"},
                ],
            }
        })

        sup._llm.chat = AsyncMock(return_value=workflow_json)

        with patch(
            "apps.agent.registry.AgentRegistry.get",
            side_effect=make_agent_getter(
                researcher=mock_researcher,
                quant=mock_quant,
            ),
        ):
            result = asyncio.run(
                sup.handle(
                    AgentMessage(
                        payload={"text": "先研究BTC策略然后实现它"},
                        user_id="u1",
                    )
                )
            )

        self.assertTrue(result.success)
        mock_researcher.handle.assert_called_once()
        mock_quant.handle.assert_called_once()
        result_data = result.data
        self.assertIn("step_results", result_data)
        self.assertIn("workflow_summary", result_data)

    def test_single_step_task_unaffected(self):
        """Single agent task still routes normally, not through workflow."""
        sup, sm = self._setup()

        mock_quant = make_accepting_agent("策略完成")

        # Non-workflow response
        sup._llm.chat = AsyncMock(return_value=json.dumps({"agent": "quant"}))

        with patch("apps.agent.registry.AgentRegistry.get", return_value=mock_quant):
            result = asyncio.run(
                sup.handle(
                    AgentMessage(
                        payload={"text": "帮我写一个RSI策略"},
                        user_id="u1",
                    )
                )
            )

        self.assertTrue(result.success)
        mock_quant.handle.assert_called_once()
        result_data = result.data
        if isinstance(result_data, dict):
            self.assertNotIn("step_results", result_data)
