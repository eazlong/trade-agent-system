"""回归测试：_run_tool_loop 防虚构守卫。

背景 Bug（任务 ID 9067ad1a-4753-4944-8479-08440783183c）：
- 用户要求"回测 box_time_range_breakout_strategy BNB最近四年的数据"；
- quant Agent 的 LLM 在**未调用 submit_backtest** 的情况下直接输出
  "回测任务已提交！任务ID 9067ad1a-..."（has_tool_calls=False）；
- 该任务 ID 在 Celery/Redis/PostgreSQL 中均不存在 → 前端"回测记录"里
  自然没有这条记录（回测根本没提交过）。
- 随后"查询回测结果"同样未调用 get_task_result，直接伪造了一份报告。

修复：未执行任何工具时，若 LLM 答复声称"任务已提交 / 已出回测报告"，
拒绝直接转发，强制追加纠错轮要求其真实调用工具；纠错超限则返回
"无法确认"的诚实答复，而不是转发虚构内容。
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from apps.agent.base import AgentMessage, AgentResult, BaseAgent


class _GuardAgent(BaseAgent):
    """带防虚构守卫的测试 Agent。"""

    name = "test_agent"
    _agent_tools = ["submit_backtest", "get_task_result"]
    _max_tool_rounds = 6

    async def handle(self, message, on_tool_result=None):
        system = "You are a test agent."
        messages = [{"role": "user", "content": message.payload.get("text", "")}]
        tools = self._get_tools_schema()
        content, is_fb = await self._run_tool_loop(
            system,
            messages,
            tools,
            max_tokens=256,
            on_tool_result=on_tool_result,
        )
        if is_fb:
            return AgentResult(task_id=message.task_id, success=False, error="fb")
        return AgentResult(task_id=message.task_id, success=True, data=content)


def _resp(has_tool_calls, content="", tool_calls=None):
    return type(
        "Resp",
        (),
        {
            "has_tool_calls": has_tool_calls,
            "content": content,
            "tool_calls": tool_calls or [],
            "reasoning_content": "",
        },
    )


def _tool(name="submit_backtest", **kwargs):
    return type(
        "ToolCall",
        (),
        {"call_id": f"call_{name}", "name": name, "arguments": kwargs},
    )()


@pytest.mark.asyncio
async def test_fabricated_submission_is_not_relayed_and_triggers_correction():
    """核心回归：LLM 未调工具却声称"任务已提交 + 任务ID" → 不得转发，
    必须追加纠错轮；真实调用工具后返回的总结才被采纳。"""
    agent = _GuardAgent()
    agent._execute_tool_call = AsyncMock(
        return_value='{"task_id": "real-task-abc", "status": "PENDING"}'
    )

    fabricated = (
        "## 📊 回测任务已提交！\n| 任务ID | `9067ad1a-4753-4944-8479-08440783183c` |"
    )
    Resp1 = _resp(False, fabricated)
    Resp2 = _resp(
        True,
        "",
        [
            _tool(
                "submit_backtest",
                strategy_name="box_time_range_breakout_strategy",
                symbol="BNB/USDT",
                timeframe="4h",
            )
        ],
    )
    Resp3 = _resp(False, "回测已完成，结果如下：总收益率 +0.03%")

    with patch("apps.agent.llm_client.LLMClient.get_instance") as mock_get:
        mock_llm = MagicMock()
        mock_llm.chat_with_tools = AsyncMock(side_effect=[Resp1, Resp2, Resp3])
        mock_get.return_value = mock_llm

        msg = AgentMessage(
            sender="user",
            recipient="test_agent",
            payload={"text": "回测box_time_range_breakout_strategy BNB最近四年的数据"},
        )
        result = await agent.handle(msg)

    # 虚构内容绝不能作为最终答复
    assert result.success
    assert "9067ad1a-4753-4944-8479-08440783183c" not in result.data
    assert result.data == "回测已完成，结果如下：总收益率 +0.03%"
    # 确实执行了一次真实工具调用
    agent._execute_tool_call.assert_called_once()
    # 第二轮的 messages 里包含了纠错指令（chat_with_tools 以关键字参数调用）
    second_call_msgs = mock_llm.chat_with_tools.call_args_list[1].kwargs["messages"]
    assert any(
        "系统校验" in m.get("content", "") and m.get("role") == "user"
        for m in second_call_msgs
    )


@pytest.mark.asyncio
async def test_persistent_fabrication_returns_honest_refusal():
    """LLM 反复编造"已提交"且拒绝调工具 → 达到纠错上限后返回
    “无法确认”的拒绝文本，而不是转发虚构的任务 ID。"""
    agent = _GuardAgent()
    agent._execute_tool_call = AsyncMock(return_value="unused")

    fabricated = "回测任务已提交，task_id=fake-999"
    Resp1 = _resp(False, fabricated)
    Resp2 = _resp(False, fabricated)
    Resp3 = _resp(False, fabricated)

    with patch("apps.agent.llm_client.LLMClient.get_instance") as mock_get:
        mock_llm = MagicMock()
        mock_llm.chat_with_tools = AsyncMock(side_effect=[Resp1, Resp2, Resp3])
        mock_get.return_value = mock_llm

        msg = AgentMessage(
            sender="user",
            recipient="test_agent",
            payload={"text": "回测test_strategy BTC"},
        )
        result = await agent.handle(msg)

    assert result.success
    assert "fake-999" not in result.data
    assert "未能确认" in result.data
    assert "submit_backtest" in result.data or "get_task_result" in result.data


@pytest.mark.asyncio
async def test_normal_chat_unaffected():
    """非任务型对话（问候/闲聊）不受守卫影响。"""
    agent = _GuardAgent()
    agent._execute_tool_call = AsyncMock(return_value="unused")

    Resp = _resp(False, "你好，有什么可以帮你？")

    with patch("apps.agent.llm_client.LLMClient.get_instance") as mock_get:
        mock_llm = MagicMock()
        mock_llm.chat_with_tools = AsyncMock(return_value=Resp)
        mock_get.return_value = mock_llm

        msg = AgentMessage(
            sender="user", recipient="test_agent", payload={"text": "你好"}
        )
        result = await agent.handle(msg)

    assert result.success
    assert result.data == "你好，有什么可以帮你？"


@pytest.mark.asyncio
async def test_legitimate_summary_after_tool_execution_not_blocked():
    """已执行工具后的正常总结（含“回测已完成”）不能被误伤。"""
    agent = _GuardAgent()
    agent._execute_tool_call = AsyncMock(
        return_value='{"task_id": "real-task-abc", "status": "SUCCESS"}'
    )

    Resp1 = _resp(
        True,
        "",
        [_tool("submit_backtest", strategy_name="x", symbol="BTCUSDT", timeframe="1h")],
    )
    Resp2 = _resp(False, "回测已完成，结果如下：总收益率 +1.2%")

    with patch("apps.agent.llm_client.LLMClient.get_instance") as mock_get:
        mock_llm = MagicMock()
        mock_llm.chat_with_tools = AsyncMock(side_effect=[Resp1, Resp2])
        mock_get.return_value = mock_llm

        msg = AgentMessage(
            sender="user", recipient="test_agent", payload={"text": "回测x BTCUSDT 1h"}
        )
        result = await agent.handle(msg)

    assert result.success
    assert result.data == "回测已完成，结果如下：总收益率 +1.2%"


def test_claim_detector_cases():
    """_looks_like_unverified_task_claim 的判定矩阵。"""
    # 命中：任务型请求 + 未调工具 + 声称已提交
    assert BaseAgent._looks_like_unverified_task_claim(
        "回测任务已提交！任务ID fake-1",
        [{"role": "user", "content": "回测box_time_range BNB"}],
        had_tool_results=False,
    )
    # 命中：声称回测报告但无工具
    assert BaseAgent._looks_like_unverified_task_claim(
        "回测报告：box_time_range（BNB/USDT）",
        [{"role": "user", "content": "查询回测结果"}],
        had_tool_results=False,
    )
    # 不命中：已执行过工具（正常总结）
    assert not BaseAgent._looks_like_unverified_task_claim(
        "回测已完成，结果如下",
        [{"role": "user", "content": "回测x BTC"}],
        had_tool_results=True,
    )
    # 不命中：普通问答无任务声明特征
    assert not BaseAgent._looks_like_unverified_task_claim(
        "你好，欢迎咨询",
        [{"role": "user", "content": "你好"}],
        had_tool_results=False,
    )
    # 不命中：任务型请求但答复没有“已提交/报告”等完成声明
    assert not BaseAgent._looks_like_unverified_task_claim(
        "我需要先确认策略参数，请稍等。",
        [{"role": "user", "content": "回测x BTC"}],
        had_tool_results=False,
    )
    # 不命中：空内容
    assert not BaseAgent._looks_like_unverified_task_claim(
        "", [{"role": "user", "content": "回测x"}], had_tool_results=False
    )
