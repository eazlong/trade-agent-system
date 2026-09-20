"""回归测试：定时/周期意图不得被当成「立即执行的工作流」。

事故（2026-09-20）：用户提交「每天10点全面分析加密货币市场行情并发送结果」，
LLM 已正确解析出 {"agent": "supervisor", "task_type": "recurring", "schedule": ...,
"steps": [...]}，但 IntentParser.parse 的「裸 steps → _workflow_plan」分支先命中，
把它降级成一次性多 Agent 工作流当场执行 —— 用户只看到立刻分析了一次
（workflow_history: 1 步 analyst），每天10点的周期任务从未注册到 django_celery_beat。
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from apps.agent.intent_parser import IntentParser
from apps.agent.intent_router import IntentRouter


def _parser_with_llm_reply(payload: dict) -> IntentParser:
    llm = AsyncMock()
    llm.chat = AsyncMock(return_value=json.dumps(payload, ensure_ascii=False))
    return IntentParser(
        llm_client=llm,
        router=IntentRouter.get_instance(),
        system_prompt="supervisor prompt",
        agent_tools=["submit_recurring_task"],
    )


@pytest.fixture(autouse=True)
def reset_router():
    IntentRouter.reset()
    yield
    IntentRouter.reset()


async def _parse(payload: dict, text: str) -> object:
    return await _parser_with_llm_reply(payload).parse(text)


@pytest.mark.asyncio
async def test_recurring_intent_routes_to_supervisor_not_workflow():
    """事故原样复现：带 task_type=recurring 的意图必须交给 supervisor。"""
    parsed = await _parse(
        {
            "agent": "supervisor",
            "task_type": "recurring",
            "schedule": "daily at 10:00",
            "summary": "每天10点全面分析加密货币市场行情并发送结果",
            "steps": [
                {
                    "agent": "analyst",
                    "message": "全面分析当前加密货币市场行情并整理成报告发送给用户",
                }
            ],
        },
        "每天10点全面分析加密货币市场行情并发送结果",
    )

    assert parsed == "supervisor"


@pytest.mark.asyncio
async def test_scheduled_intent_without_agent_routes_to_supervisor():
    """只声明 task_type=scheduled（如 now+5m）也必须交给 supervisor。"""
    parsed = await _parse(
        {
            "task_type": "scheduled",
            "run_at": "now+5m",
            "steps": [{"agent": "analyst", "message": "5 分钟后分析 BTC"}],
        },
        "5分钟后分析BTC并告诉我",
    )

    assert parsed == "supervisor"


@pytest.mark.asyncio
async def test_supervisor_with_steps_routes_to_supervisor():
    """agent=supervisor + steps 的落点是调度工具，不是当天执行的工作流。"""
    parsed = await _parse(
        {
            "agent": "supervisor",
            "summary": "每小时研究并回测策略",
            "steps": [
                {"agent": "researcher", "message": "研究高胜率策略"},
                {"agent": "quant", "message": "实现并回测"},
            ],
        },
        "每一个小时研究高胜率策略并实现回测",
    )

    assert parsed == "supervisor"


@pytest.mark.asyncio
async def test_one_shot_workflow_plan_still_returns_workflow():
    """无时间语义的多 Agent 请求仍走工作流，不受本次修复影响。"""
    parsed = await _parse(
        {
            "summary": "研究并实现策略",
            "steps": [
                {"agent": "researcher", "message": "研究高胜率策略"},
                {"agent": "quant", "message": "实现并回测"},
            ],
        },
        "研究高胜率策略并实现回测",
    )

    assert isinstance(parsed, dict)
    assert len(parsed["_workflow_plan"]["steps"]) == 2
