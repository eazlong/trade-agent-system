"""回归测试：熔断按**连续**失败计数，成功一次即清零。

Bug：``_run_tool_loop`` 的注释与对外提示都写「同一工具**连续**失败 N 次」，
但 ``tool_failure_counts`` 只增不减。于是 fail/ok/fail/ok/fail 这种**从未连续
失败**的序列（每次失败之间都有真实成功）也会累计到阈值并把工具禁用，提示语
还照样说「已连续失败 3 次」——把间歇性故障误判成持续故障，并让用户看到与事实
不符的失败次数。

修复期望：真实成功执行清零该工具的连续失败计数；只有连续失败达阈值才熔断。
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from apps.agent.base import AgentMessage, AgentResult, BaseAgent

THRESHOLD = BaseAgent._TOOL_FAILURE_DISABLE_THRESHOLD


class _LoopAgent(BaseAgent):
    name = "test_consecutive_agent"
    _agent_tools = ["web_search"]
    _max_tool_rounds = 32

    async def handle(self, message, on_tool_result=None):
        content, is_fb = await self._run_tool_loop(
            "You are a test agent.",
            [{"role": "user", "content": "x"}],
            self._get_tools_schema(),
            on_tool_result=on_tool_result,
        )
        return AgentResult(task_id=message.task_id, success=not is_fb, data=content)


def _resp_tool(i: int):
    tc = type(
        "ToolCall",
        (),
        {"call_id": f"call_{i}", "name": "web_search", "arguments": {"query": "q"}},
    )
    return type(
        "Resp",
        (),
        {
            "has_tool_calls": True,
            "content": "",
            "tool_calls": [tc],
            "reasoning_content": "",
            "degraded": False,
        },
    )()


def _resp_done():
    return type(
        "Resp",
        (),
        {
            "has_tool_calls": False,
            "content": "done",
            "tool_calls": [],
            "reasoning_content": "",
            "degraded": False,
        },
    )()


async def _run(sequence: list[str]) -> list[str]:
    """Run the loop with the given fail/ok sequence; return tool result texts."""
    agent = _LoopAgent()
    state = {"i": 0}
    seen: list[str] = []

    def llm_tools(system, messages, tools, max_tokens=8192, **kw):
        i = state["i"]
        state["i"] += 1
        return _resp_done() if i >= len(sequence) else _resp_tool(i)

    async def fake_execute(tc):
        return "Error: boom" if sequence[len(seen)] == "fail" else "ok result"

    async def on_result(name, text):
        seen.append(text)

    agent._execute_tool_call = AsyncMock(side_effect=fake_execute)

    with patch("apps.agent.llm_client.LLMClient.get_instance") as mock_get:
        m = MagicMock()
        m.chat_with_tools = AsyncMock(side_effect=llm_tools)
        m.chat = AsyncMock(return_value="summary")
        mock_get.return_value = m
        await agent.handle(
            AgentMessage(sender="user", recipient="test_consecutive_agent", payload={}),
            on_tool_result=on_result,
        )
    return seen


@pytest.mark.asyncio
async def test_interleaved_failures_never_trigger_breaker():
    """fail/ok 交替：累计失败达到阈值，但从未连续 → 不得熔断。"""
    seq = ["fail", "ok", "fail", "ok", "fail", "ok"]
    seen = await _run(seq)

    assert len(seen) == len(seq), f"工具被提前熔断，只执行了 {len(seen)} 次：{seen}"
    assert not any("已被系统禁用" in t for t in seen), (
        f"从未连续失败却被禁用（把间歇故障误判为持续故障）：{seen}"
    )


@pytest.mark.asyncio
async def test_consecutive_failures_still_trip_the_breaker():
    """连续失败达到阈值仍必须熔断（原行为不能被这次修复弄丢）。"""
    seq = ["fail"] * THRESHOLD + ["ok"]
    seen = await _run(seq)

    real = [t for t in seen if not t.startswith("【系统通知】")]
    assert len(real) == THRESHOLD, (
        f"连续失败 {THRESHOLD} 次应恰好真实执行 {THRESHOLD} 次，实际 {len(real)}：{seen}"
    )
    assert any("已被系统禁用" in t for t in seen), "连续失败达阈值却未熔断"
