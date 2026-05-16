# Agent 流式进度推送 + 结构化任务确认 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Agent 处理链路中增加 `on_tool_result` 进度回调，实现工具调用进度的 WebSocket 实时推送和回测任务提交的结构化确认。

**Architecture:** 在 `_run_tool_loop` → `_LLMAgent.handle` → `Supervisor.handle` → `ChatConsumer._handle_chat` 链路中透传一个可选 `on_tool_result` 回调。Consumer 层创建此回调用于推送 `tool_progress` 消息，并在 Agent 返回后检测 `task_id` 字段推送 `task_submitted` 结构化确认。

**Tech Stack:** Python 3.12, Django Channels (AsyncWebsocketConsumer), asyncio

---

### Task 1: base.py — `_run_tool_loop` 增加 `on_tool_result` 回调参数

**Files:**
- Modify: `backend/apps/agent/base.py:176-182` (signature), `:226` (callback call site)
- Modify: `backend/apps/agent/base.py:280-282` (abstract handle signature)

- [ ] **Step 1: 修改 `_run_tool_loop` 签名，添加 `on_tool_result` 参数**

在 `backend/apps/agent/base.py` 第 176-182 行，将：
```python
    async def _run_tool_loop(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int = 2048,
    ) -> tuple[str, bool]:
```

改为：
```python
    async def _run_tool_loop(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int = 2048,
        on_tool_result=None,
    ) -> tuple[str, bool]:
```

- [ ] **Step 2: 在工具执行后调用 `on_tool_result` 回调**

在 `backend/apps/agent/base.py` 第 226 行 `result_text = await self._execute_tool_call(tc)` 之后，第 227 行 `tool_results.append(...)` 之前，插入回调调用：

```python
                result_text = await self._execute_tool_call(tc)
                if on_tool_result:
                    await on_tool_result(tc.name, result_text)
                tool_results.append(
```

- [ ] **Step 3: 修改 `BaseAgent.handle` 抽象方法签名**

在 `backend/apps/agent/base.py` 第 280-282 行，将：
```python
    @abstractmethod
    async def handle(self, message: AgentMessage) -> AgentResult:
        """处理一条消息，返回结果"""
```

改为：
```python
    @abstractmethod
    async def handle(self, message: AgentMessage, on_tool_result=None) -> AgentResult:
        """处理一条消息，返回结果"""
```

- [ ] **Step 4: Commit**

```bash
git add backend/apps/agent/base.py
git commit -m "feat(base): add on_tool_result callback to _run_tool_loop and handle signature"
```

---

### Task 2: sub_agents.py — `_LLMAgent.handle` 透传 `on_tool_result`

**Files:**
- Modify: `backend/apps/agent/sub_agents.py:91-92` (handle signature), `:123-125` (_run_tool_loop call)

- [ ] **Step 1: 修改 `_LLMAgent.handle` 签名**

在 `backend/apps/agent/sub_agents.py` 第 91-92 行，将：
```python
    async def handle(self, message: AgentMessage) -> AgentResult:
        text = message.payload.get("text", "")
```

改为：
```python
    async def handle(self, message: AgentMessage, on_tool_result=None) -> AgentResult:
        text = message.payload.get("text", "")
```

- [ ] **Step 2: 透传 `on_tool_result` 给 `_run_tool_loop`**

在 `backend/apps/agent/sub_agents.py` 第 123-125 行，将：
```python
        content, is_fb = await self._run_tool_loop(
            system, messages, tools, max_tokens=2048
        )
```

改为：
```python
        content, is_fb = await self._run_tool_loop(
            system, messages, tools, max_tokens=2048,
            on_tool_result=on_tool_result,
        )
```

- [ ] **Step 3: Commit**

```bash
git add backend/apps/agent/sub_agents.py
git commit -m "feat(sub_agents): pass on_tool_result callback through _LLMAgent.handle"
```

---

### Task 3: supervisor.py pt 1 — 修改 `handle`、`_self_execute`、`_free_chat`

**Files:**
- Modify: `backend/apps/agent/supervisor.py:266`, `:835-845`, `:856-884`

- [ ] **Step 1: 修改 `SupervisorAgent.handle` 签名并透传**

第 266 行，将：
```python
    async def handle(self, message: AgentMessage) -> AgentResult:
```

改为：
```python
    async def handle(self, message: AgentMessage, on_tool_result=None) -> AgentResult:
```

第 275-276 行：
```python
                return await self._handle_paused_session(
                    message, session_ctx, session_mgr, on_tool_result
                )
```

第 280 行：
```python
                return await self._handle_multi_turn(
                    message, session_ctx, session_mgr, on_tool_result
                )
```

第 283 行：
```python
        return await self._normal_route(message, on_tool_result)
```

- [ ] **Step 2: 修改 `_self_execute` 签名并透传**

第 835 行，将：
```python
    async def _self_execute(self, message: AgentMessage) -> AgentResult:
```

改为：
```python
    async def _self_execute(self, message: AgentMessage, on_tool_result=None) -> AgentResult:
```

第 843-844 行 `_run_tool_loop` 调用增加 `on_tool_result=on_tool_result` 参数。

- [ ] **Step 3: 修改 `_free_chat` 签名并透传**

第 856 行，将：
```python
    async def _free_chat(self, message: AgentMessage) -> AgentResult:
```

改为：
```python
    async def _free_chat(self, message: AgentMessage, on_tool_result=None) -> AgentResult:
```

第 882-883 行 `_run_tool_loop` 调用增加 `on_tool_result=on_tool_result` 参数。

- [ ] **Step 4: Commit**

```bash
git add backend/apps/agent/supervisor.py
git commit -m "feat(supervisor): add on_tool_result to handle, _self_execute, _free_chat"
```

---

### Task 4: supervisor.py pt 2 — 修改路由方法

**Files:**
- Modify: `backend/apps/agent/supervisor.py:285-306`, `:358-422`, `:459-554`, `:606-639`

- [ ] **Step 1: 修改 `_handle_paused_session` 签名并透传**

第 285-287 行，增加 `on_tool_result=None` 参数。第 295、303、306 行透传 `on_tool_result`。

- [ ] **Step 2: 修改 `_handle_multi_turn` 签名并透传**

第 358-360 行，增加 `on_tool_result=None` 参数。第 365、374、415、419、422 行透传 `on_tool_result`。

- [ ] **Step 3: 修改 `_normal_route` 签名并透传**

第 459 行，增加 `on_tool_result=None` 参数。第 490、501、506、515-516、519、554 行透传 `on_tool_result`。

- [ ] **Step 4: 修改 `_route_to_agent` 签名并透传**

第 606-608 行，增加 `on_tool_result=None` 参数。第 639 行改为 `agent.handle(message, on_tool_result=on_tool_result)`。

- [ ] **Step 5: Commit**

```bash
git add backend/apps/agent/supervisor.py
git commit -m "feat(supervisor): add on_tool_result to routing methods"
```

---

### Task 5: consumers.py — 进度回调 + 结构化任务确认

**Files:**
- Modify: `backend/apps/agent/consumers.py:93-154` (_handle_chat)

- [ ] **Step 1: 重写 `_handle_chat` 方法，加入三阶段推送**

将 `_handle_chat` 方法（第 93-154 行）替换为：

```python
    async def _handle_chat(self, data):
        text = data.get("text", "").strip()
        if not text:
            await self.send(
                text_data=json.dumps({"type": "error", "error": "text is required"})
            )
            return

        logger.info("[ChatWS] User %s chat: %s", self.user_id, text[:100])

        await self.send(
            text_data=json.dumps({"type": "status", "status": "processing"})
        )

        async def on_tool_result(tool_name, result_text):
            """工具执行进度回调 — 实时推送给前端"""
            try:
                await self.send(text_data=json.dumps({
                    "type": "tool_progress",
                    "tool": tool_name,
                    "result": result_text[:2000],
                }))
            except Exception:
                pass

        try:
            supervisor = SupervisorAgent.get_instance()
            result: AgentResult = await supervisor.handle(
                AgentMessage(
                    sender="user",
                    recipient="supervisor",
                    payload={"text": text},
                    user_id=self.user_id,
                ),
                on_tool_result=on_tool_result,
            )

            # 检测回测/异步任务提交 → 推送结构化确认
            if (
                isinstance(result.data, dict)
                and result.data.get("task_id")
                and result.success
            ):
                await self.send(text_data=json.dumps({
                    "type": "task_submitted",
                    "task_id": result.data["task_id"],
                    "status": "success",
                }))

            if result.success:
                await self.send(
                    text_data=json.dumps(
                        {
                            "type": "chat_response",
                            "data": (
                                result.data.get("content", str(result.data))
                                if isinstance(result.data, dict)
                                else result.data
                            ),
                            "task_id": result.task_id,
                            "status": "done",
                        }
                    )
                )
            else:
                await self.send(
                    text_data=json.dumps(
                        {
                            "type": "chat_response",
                            "error": result.error or "处理请求时发生错误，请稍后重试",
                            "task_id": result.task_id,
                            "status": "error",
                        }
                    )
                )

        except Exception as e:
            logger.error(
                "[ChatWS] Supervisor handle error: %s\n%s", e, traceback.format_exc()
            )
            await self.send(
                text_data=json.dumps(
                    {
                        "type": "chat_response",
                        "error": str(e),
                        "status": "error",
                    }
                )
            )
```

- [ ] **Step 2: Commit**

```bash
git add backend/apps/agent/consumers.py
git commit -m "feat(consumer): add tool_progress streaming and task_submitted confirmation"
```

---

### Task 6: 测试 — `_run_tool_loop` 的 `on_tool_result` 回调

**Files:**
- Create: `backend/apps/agent/tests/test_streaming_progress.py`

- [ ] **Step 1: 编写测试文件**

```python
"""Tests for on_tool_result callback in agent tool loop."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import unittest

from apps.agent.base import AgentMessage, AgentResult


class OnToolResultCallbackTests(unittest.TestCase):
    """Test that on_tool_result callback is invoked correctly."""

    def setUp(self):
        from apps.agent.base import BaseAgent

        class _TestAgent(BaseAgent):
            name = "test_agent"
            _agent_tools = ["submit_backtest"]
            _max_tool_rounds = 1

            async def handle(self, message, on_tool_result=None):
                system = "You are a test agent."
                messages = [{"role": "user", "content": message.payload.get("text", "")}]
                tools = self._get_tools_schema()
                content, is_fb = await self._run_tool_loop(
                    system, messages, tools, max_tokens=256,
                    on_tool_result=on_tool_result,
                )
                if is_fb:
                    return AgentResult(task_id=message.task_id, success=False, error="fb")
                return AgentResult(task_id=message.task_id, success=True, data=content)

        self.agent = _TestAgent()

    @patch("apps.agent.llm_client.LLMClient.get_instance")
    async def test_on_tool_result_called_on_tool_execution(self, mock_get_llm):
        callback = AsyncMock()

        mock_llm = MagicMock()
        mock_llm.chat_with_tools = AsyncMock()
        ToolCall = type("ToolCall", (), {
            "call_id": "call_1", "name": "submit_backtest",
            "arguments": {"strategy_name": "macd", "symbol": "BTCUSDT", "timeframe": "1h"}
        })
        Resp1 = type("Resp", (), {"has_tool_calls": True, "content": "", "tool_calls": [ToolCall]})
        Resp2 = type("Resp", (), {"has_tool_calls": False, "content": "done"})
        mock_llm.chat_with_tools.side_effect = [Resp1, Resp2]
        mock_get_llm.return_value = mock_llm

        self.agent._execute_tool_call = AsyncMock(
            return_value='{"task_id": "abc123", "status": "PENDING"}'
        )

        msg = AgentMessage(sender="user", recipient="test_agent",
                           payload={"text": "run backtest"})
        result = await self.agent.handle(msg, on_tool_result=callback)

        callback.assert_called_once()
        call_args = callback.call_args[0]
        self.assertEqual(call_args[0], "submit_backtest")
        self.assertIn("abc123", call_args[1])
        self.assertTrue(result.success)

    async def test_on_tool_result_not_called_when_no_tools(self):
        callback = AsyncMock()

        from apps.agent.llm_client import LLMClient
        with patch.object(LLMClient, "get_instance") as mock_get_llm:
            mock_llm = MagicMock()
            Resp = type("Resp", (), {"has_tool_calls": False, "content": "hello"})
            mock_llm.chat_with_tools = AsyncMock(return_value=Resp)
            mock_get_llm.return_value = mock_llm

            msg = AgentMessage(sender="user", recipient="test_agent",
                               payload={"text": "hi"})
            result = await self.agent.handle(msg, on_tool_result=callback)

            callback.assert_not_called()
            self.assertTrue(result.success)

    async def test_without_callback_still_works(self):
        from apps.agent.llm_client import LLMClient
        with patch.object(LLMClient, "get_instance") as mock_get_llm:
            mock_llm = MagicMock()
            Resp = type("Resp", (), {"has_tool_calls": False, "content": "hello"})
            mock_llm.chat_with_tools = AsyncMock(return_value=Resp)
            mock_get_llm.return_value = mock_llm

            msg = AgentMessage(sender="user", recipient="test_agent",
                               payload={"text": "hi"})
            result = await self.agent.handle(msg)

            self.assertTrue(result.success)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试验证**

```bash
DJANGO_SETTINGS_MODULE=core.settings.dev uv run pytest apps/agent/tests/test_streaming_progress.py -v
```

Expected: 3 tests PASS

- [ ] **Step 3: Commit**

```bash
git add backend/apps/agent/tests/test_streaming_progress.py
git commit -m "test: add on_tool_result callback tests"
```

---

### Task 7: 回归验证 — 运行全部已有测试

**Files:** None (test run only)

- [ ] **Step 1: 运行完整 agent 测试套件**

```bash
DJANGO_SETTINGS_MODULE=core.settings.dev uv run pytest apps/agent/tests/ -v
```

Expected: 所有已有测试 PASS（新增 3 个）

- [ ] **Step 2: 如有失败，排查修复**

检查失败原因，可能因为 `BaseAgent.handle` 或 `_LLMAgent.handle` 签名变更导致 mock 不匹配。

- [ ] **Step 3: Commit 修复（如有）**

```bash
git add -A
git commit -m "fix: update tests for on_tool_result signature changes"
```
