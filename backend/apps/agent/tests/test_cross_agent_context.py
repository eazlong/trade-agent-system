"""Tests for cross-agent context injection in _route_to_agent and _build_context."""

import pytest

from apps.agent.supervisor import IntentRouter
from apps.agent.sub_agents import _LLMAgent
from apps.agent.base import AgentMessage


@pytest.fixture(autouse=True)
def reset_router():
    """Reset IntentRouter singleton between tests."""
    IntentRouter.reset()
    yield
    IntentRouter.reset()


class TestBuildContextWithCrossAgentInjection:
    """Verify that _build_context includes previous_agent_response when present."""

    def test_build_context_includes_previous_agent_response(self):
        """When payload has previous_agent_response, it's included in context."""
        agent = _LLMAgent()
        agent._agent_tools = []
        agent._context_fields = []

        message = AgentMessage(
            sender="supervisor",
            recipient="quant",
            payload={
                "text": "实现这个策略",
                "previous_agent_response": "我找到了 RSI+MACD 超短线策略...",
                "previous_agent_name": "researcher",
            },
        )

        context = agent._build_context(message)
        assert "来自 researcher Agent 的上一轮回复" in context
        assert "RSI+MACD 超短线策略" in context

    def test_build_context_empty_without_previous_agent(self):
        """When payload has no previous_agent_response, context is empty (no context_fields)."""
        agent = _LLMAgent()
        agent._agent_tools = []
        agent._context_fields = []

        message = AgentMessage(
            sender="supervisor",
            recipient="quant",
            payload={"text": "实现这个策略"},
        )

        context = agent._build_context(message)
        assert context == ""

    def test_build_context_includes_both_previous_and_fields(self):
        """Context includes both previous_agent_response AND context_fields."""
        agent = _LLMAgent()
        agent._agent_tools = []
        agent._context_fields = ["topic", "depth"]

        message = AgentMessage(
            sender="supervisor",
            recipient="quant",
            payload={
                "text": "实现这个策略",
                "previous_agent_response": "RSI+MACD 策略",
                "previous_agent_name": "researcher",
                "topic": "BTC",
                "depth": "detailed",
            },
        )

        context = agent._build_context(message)
        assert "来自 researcher Agent 的上一轮回复" in context
        assert "RSI+MACD 策略" in context
        assert "topic: BTC" in context
        assert "depth: detailed" in context

    def test_build_context_ignores_partial_context(self):
        """If only previous_agent_name is present (no response), context stays clean."""
        agent = _LLMAgent()
        agent._agent_tools = []
        agent._context_fields = []

        message = AgentMessage(
            sender="supervisor",
            recipient="quant",
            payload={
                "text": "实现这个策略",
                "previous_agent_name": "researcher",
            },
        )

        context = agent._build_context(message)
        assert context == ""
