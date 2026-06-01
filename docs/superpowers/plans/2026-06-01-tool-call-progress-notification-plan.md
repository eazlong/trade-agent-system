# Tool Call Progress Notification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为每次工具调用推送一行简要通知（工具名称 + 关键参数），实现完全透明的任务执行流程。

**Architecture:** 在 TaskTracker 新增 tool_call() 方法，通过现有 Redis pubsub 推送机制发送通知；在 base.py::_execute_tool_call() 开头调用该方法，确保工具执行前推送。

**Tech Stack:** Django + Redis pubsub + Telegram/Lark Bot API + pytest

---

## Task 1: 单元测试 - TaskTracker.tool_call() 基本功能

**Files:**
- Modify: `backend/apps/agent/tests/test_task_tracker.py`

**Prerequisites:**
- 理解现有 TaskTracker 测试结构（test_start/test_milestone/test_complete/test_fail）
- 理解 `_notify()` mock 机制

- [ ] **Step 1: Write the failing test - test_tool_call_basic**

在 `test_task_tracker.py` 中新增测试：

```python
def test_tool_call_basic():
    """Test basic tool_call notification with arguments."""
    from apps.agent.task_tracker import TaskTracker
    from unittest.mock import MagicMock, patch

    tracker = TaskTracker(task_id="test-123", user_id="user-1", channel="telegram")
    tracker.start("任务启动")

    # Mock _notify to capture notification text
    with patch.object(tracker, '_notify') as mock_notify:
        tracker.tool_call("get_kline_data", {"symbol": "BTCUSDT", "interval": "1h"})

        # Verify _notify was called
        mock_notify.assert_called_once()

        # Verify notification format
        notification_text = mock_notify.call_args[0][0]
        assert "🔧 执行工具：get_kline_data" in notification_text
        assert "symbol='BTCUSDT'" in notification_text
        assert "interval='1h'" in notification_text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DJANGO_SETTINGS_MODULE=core.settings.dev pytest backend/apps/agent/tests/test_task_tracker.py::test_tool_call_basic -v`

Expected: FAIL with "AttributeError: 'TaskTracker' object has no attribute 'tool_call'"

- [ ] **Step 3: Commit the failing test**

```bash
git add backend/apps/agent/tests/test_task_tracker.py
git commit -m "test: add test_tool_call_basic (failing)"
```

---

## Task 2: 实现 TaskTracker.tool_call() 方法

**Files:**
- Modify: `backend/apps/agent/task_tracker.py` (insert after `milestone()` method, before `alive()`)

- [ ] **Step 1: Write minimal implementation**

在 `task_tracker.py` 的 `milestone()` 方法后（约 line 168），插入新方法：

```python
    def tool_call(self, tool_name: str, arguments: dict) -> None:
        """Push tool execution notification (no throttle, always immediate).

        Args:
            tool_name: Tool name (e.g., "get_kline_data")
            arguments: Tool arguments dict (user_id will be filtered out)
        """
        # Filter out internal args
        display_args = {
            k: v for k, v in arguments.items()
            if k not in ("user_id", "agent_name")
        }

        # Format args: key=value, key=value
        args_str = ", ".join(
            f"{k}={repr(v)[:50]}"  # Truncate long values
            for k, v in display_args.items()
        )

        # Build notification text
        text = f"🔧 执行工具：{tool_name}（{args_str}）"

        # Push immediately (no throttle check)
        self._notify(text)

        # Update last_alive (existing behavior)
        self.alive()

        logger.debug(
            "[TaskTracker] tool_call pushed: %s(%s)",
            tool_name, args_str
        )
```

- [ ] **Step 2: Run test to verify it passes**

Run: `DJANGO_SETTINGS_MODULE=core.settings.dev pytest backend/apps/agent/tests/test_task_tracker.py::test_tool_call_basic -v`

Expected: PASS

- [ ] **Step 3: Commit implementation**

```bash
git add backend/apps/agent/task_tracker.py
git commit -m "feat: add TaskTracker.tool_call() method for tool execution notifications"
```

---

## Task 3: 单元测试 - 过滤 user_id 和 agent_name

**Files:**
- Modify: `backend/apps/agent/tests/test_task_tracker.py`

- [ ] **Step 1: Write the failing test - test_tool_call_filters_internal_args**

```python
def test_tool_call_filters_internal_args():
    """Test user_id and agent_name are filtered out."""
    from apps.agent.task_tracker import TaskTracker
    from unittest.mock import patch

    tracker = TaskTracker(task_id="test-123", user_id="user-1")
    tracker.start()

    with patch.object(tracker, '_notify') as mock_notify:
        tracker.tool_call("resolve_user", {
            "user_id": "secret-123",
            "agent_name": "supervisor",
            "query": "John"
        })

        notification_text = mock_notify.call_args[0][0]

        # Verify sensitive args NOT in notification
        assert "user_id" not in notification_text
        assert "agent_name" not in notification_text
        assert "secret-123" not in notification_text
        assert "supervisor" not in notification_text

        # Verify non-sensitive args ARE in notification
        assert "query='John'" in notification_text
```

- [ ] **Step 2: Run test to verify it passes**

Run: `DJANGO_SETTINGS_MODULE=core.settings.dev pytest backend/apps/agent/tests/test_task_tracker.py::test_tool_call_filters_internal_args -v`

Expected: PASS（已有实现已包含过滤逻辑）

- [ ] **Step 3: Commit test**

```bash
git add backend/apps/agent/tests/test_task_tracker.py
git commit -m "test: add test_tool_call_filters_internal_args"
```

---

## Task 4: 单元测试 - 截断长参数值

**Files:**
- Modify: `backend/apps/agent/tests/test_task_tracker.py`

- [ ] **Step 1: Write the failing test - test_tool_call_truncates_long_values**

```python
def test_tool_call_truncates_long_values():
    """Test long parameter values are truncated to 50 chars."""
    from apps.agent.task_tracker import TaskTracker
    from unittest.mock import patch

    tracker = TaskTracker(task_id="test-123", user_id="user-1")
    tracker.start()

    long_query = "a" * 100  # 100 characters

    with patch.object(tracker, '_notify') as mock_notify:
        tracker.tool_call("search", {"query": long_query})

        notification_text = mock_notify.call_args[0][0]

        # Verify value truncated to repr(v)[:50] -> 'aaaaaaaa...' (50 chars inside repr)
        # repr("aaa...") = "'aaa...'" (quotes included)
        assert len(notification_text) < 150  # Ensure not too long
        assert "query=" in notification_text
        # Check truncated value is present (first 50 chars of repr)
        assert "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'" in notification_text[:100]
```

- [ ] **Step 2: Run test to verify it passes**

Run: `DJANGO_SETTINGS_MODULE=core.settings.dev pytest backend/apps/agent/tests/test_task_tracker.py::test_tool_call_truncates_long_values -v`

Expected: PASS（已有实现已包含截断逻辑）

- [ ] **Step 3: Commit test**

```bash
git add backend/apps/agent/tests/test_task_tracker.py
git commit -m "test: add test_tool_call_truncates_long_values"
```

---

## Task 5: 单元测试 - 无 tracker_context 时静默跳过

**Files:**
- Modify: `backend/apps/agent/tests/test_task_tracker.py`

- [ ] **Step 1: Write the failing test - test_tool_call_without_tracker_context**

```python
def test_tool_call_without_tracker_context():
    """Test tool_call when tracker not in context (should not raise)."""
    from apps.agent.task_tracker import TaskTracker, tracker_context
    from unittest.mock import patch

    # Clear context
    tracker_context.set(None)

    tracker = TaskTracker(task_id="test-123", user_id="user-1")
    tracker.start()

    # Should not raise even though context is None
    with patch.object(tracker, '_notify') as mock_notify:
        tracker.tool_call("get_kline_data", {"symbol": "BTC"})

        # Notification should still be sent (tracker exists)
        mock_notify.assert_called_once()
```

- [ ] **Step 2: Run test to verify it passes**

Run: `DJANGO_SETTINGS_MODULE=core.settings.dev pytest backend/apps/agent/tests/test_task_tracker.py::test_tool_call_without_tracker_context -v`

Expected: PASS

- [ ] **Step 3: Commit test**

```bash
git add backend/apps/agent/tests/test_task_tracker.py
git commit -m "test: add test_tool_call_without_tracker_context"
```

---

## Task 6: 集成测试 - base._execute_tool_call 推送通知

**Files:**
- Create: `backend/apps/agent/tests/test_tool_call_integration.py`

**Prerequisites:**
- 理解 `_execute_tool_call()` 方法签名和 ToolCallRequest mock
- 理解 `tracker_context` 设置方式

- [ ] **Step 1: Write the failing test - test_execute_tool_call_pushes_notification**

```python
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


@pytest.mark.asyncio
async def test_execute_tool_call_pushes_notification():
    """Test _execute_tool_call pushes tool_call notification."""
    from apps.agent.base import BaseAgent
    from apps.agent.task_tracker import TaskTracker, tracker_context

    # Create minimal agent
    class MockAgent(BaseAgent):
        name = "test_agent"
        async def handle(self, message, on_tool_result=None):
            pass

    agent = MockAgent()
    agent._current_user_id = "user-1"

    # Setup tracker in context
    tracker = TaskTracker(task_id="test-123", user_id="user-1")
    token = tracker_context.set(tracker)
    tracker.start()

    # Mock tool call
    tc = MagicMock()
    tc.name = "get_kline_data"
    tc.arguments = {"symbol": "BTCUSDT", "interval": "1h"}

    # Mock tool execution
    with patch.object(agent, 'run_tool', new_callable=AsyncMock) as mock_run_tool:
        mock_run_tool.return_value = MagicMock(success=True, data="K-line data retrieved")

        # Mock tracker.tool_call
        with patch.object(tracker, 'tool_call') as mock_tool_call:
            await agent._execute_tool_call(tc)

            # Verify tool_call was invoked with correct args
            mock_tool_call.assert_called_once_with("get_kline_data", {"symbol": "BTCUSDT", "interval": "1h"})

    # Cleanup
    tracker_context.reset(token)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DJANGO_SETTINGS_MODULE=core.settings.dev pytest backend/apps/agent/tests/test_tool_call_integration.py::test_execute_tool_call_pushes_notification -v`

Expected: FAIL with "AssertionError: tool_call not called" 或类似错误

- [ ] **Step 3: Commit failing test**

```bash
git add backend/apps/agent/tests/test_tool_call_integration.py
git commit -m "test: add test_execute_tool_call_pushes_notification (failing)"
```

---

## Task 7: 修改 base._execute_tool_call() 调用 tracker.tool_call()

**Files:**
- Modify: `backend/apps/agent/base.py` (insert at start of `_execute_tool_call()` method, line ~113)

- [ ] **Step 1: Write implementation**

在 `base.py::_execute_tool_call()` 方法开头（line 113 后）插入：

```python
    async def _execute_tool_call(self, tc) -> str:
        """执行单个工具调用，返回结果字符串。

        特殊处理 load_skill（传 agent_name）。
        自动注入 user_id（如果工具支持）。
        """
        try:
            # Push tool call notification
            from .task_tracker import tracker_context
            tracker = tracker_context.get(None)
            if tracker is not None:
                tracker.tool_call(tc.name, tc.arguments)

            # Existing execution logic starts here...
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
                # ... rest of existing code ...
```

**重要**: 仅在开头插入 4 行新代码，不修改后续逻辑。

- [ ] **Step 2: Run test to verify it passes**

Run: `DJANGO_SETTINGS_MODULE=core.settings.dev pytest backend/apps/agent/tests/test_tool_call_integration.py::test_execute_tool_call_pushes_notification -v`

Expected: PASS

- [ ] **Step 3: Commit implementation**

```bash
git add backend/apps/agent/base.py
git commit -m "feat: call tracker.tool_call() in _execute_tool_loop to push notifications"
```

---

## Task 8: 集成测试 - 多工具顺序推送

**Files:**
- Modify: `backend/apps/agent/tests/test_tool_call_integration.py`

- [ ] **Step 1: Write the failing test - test_multiple_tool_calls_push_sequentially**

```python
@pytest.mark.asyncio
async def test_multiple_tool_calls_push_sequentially():
    """Test multiple tool calls in sequence push notifications in order."""
    from apps.agent.base import BaseAgent
    from apps.agent.task_tracker import TaskTracker, tracker_context

    class MockAgent(BaseAgent):
        name = "test_agent"
        async def handle(self, message, on_tool_result=None):
            pass

    agent = MockAgent()
    agent._current_user_id = "user-1"

    tracker = TaskTracker(task_id="test-123", user_id="user-1")
    token = tracker_context.set(tracker)
    tracker.start()

    # Mock multiple tool calls
    tc1 = MagicMock()
    tc1.name = "get_kline_data"
    tc1.arguments = {"symbol": "BTC"}

    tc2 = MagicMock()
    tc2.name = "get_balance"
    tc2.arguments = {"user_id": "user-1"}

    with patch.object(agent, 'run_tool', new_callable=AsyncMock) as mock_run_tool:
        mock_run_tool.return_value = MagicMock(success=True, data="result")

        with patch.object(tracker, 'tool_call') as mock_tool_call:
            await agent._execute_tool_call(tc1)
            await agent._execute_tool_call(tc2)

            # Verify two calls in correct order
            assert mock_tool_call.call_count == 2
            calls = mock_tool_call.call_args_list

            # First call
            assert calls[0][0][0] == "get_kline_data"
            assert calls[0][0][1] == {"symbol": "BTC"}

            # Second call
            assert calls[1][0][0] == "get_balance"

    tracker_context.reset(token)
```

- [ ] **Step 2: Run test to verify it passes**

Run: `DJANGO_SETTINGS_MODULE=core.settings.dev pytest backend/apps/agent/tests/test_tool_call_integration.py::test_multiple_tool_calls_push_sequentially -v`

Expected: PASS

- [ ] **Step 3: Commit test**

```bash
git add backend/apps/agent/tests/test_tool_call_integration.py
git commit -m "test: add test_multiple_tool_calls_push_sequentially"
```

---

## Task 9: 运行完整测试套件验证

- [ ] **Step 1: Run all task_tracker tests**

Run: `DJANGO_SETTINGS_MODULE=core.settings.dev pytest backend/apps/agent/tests/test_task_tracker.py -v`

Expected: All tests PASS (包括新增的 4 个 tool_call tests)

- [ ] **Step 2: Run all integration tests**

Run: `DJANGO_SETTINGS_MODULE=core.settings.dev pytest backend/apps/agent/tests/test_tool_call_integration.py -v`

Expected: All tests PASS (2 tests)

- [ ] **Step 3: Run full agent test suite**

Run: `DJANGO_SETTINGS_MODULE=core.settings.dev pytest backend/apps/agent/tests/ -v`

Expected: All tests PASS (无回归)

- [ ] **Step 4: Check test coverage**

Run: `DJANGO_SETTINGS_MODULE=core.settings.dev pytest backend/apps/agent/tests/test_task_tracker.py backend/apps/agent/tests/test_tool_call_integration.py --cov=apps/agent/task_tracker --cov=apps/agent/base --cov-report=term-missing`

Expected: tool_call() method 100% coverage, _execute_tool_call notification logic 100% coverage

---

## Task 10: 手动验证 Telegram 推送效果

**Prerequisites:**
- Telegram Bot 已配置并运行
- Redis 已启动
- 可发送测试消息到 Bot

- [ ] **Step 1: 启动完整服务栈**

Run: `docker-compose up -d --build`

Expected: backend + redis + celery worker + telegram bot 启动成功

- [ ] **Step 2: 发送触发工具调用的消息**

通过 Telegram 发送: "查询 BTCUSDT 的最近 10 条 K 线数据"

Expected:
1. 收到 "任务已启动" 消息
2. 收到 "🔧 执行工具：get_kline_data（symbol='BTCUSDT', limit=10）" 消息
3. 收到 "任务完成" 消息

- [ ] **Step 3: 验证消息格式**

检查 Telegram 消息:
- ✓ 工具名称正确
- ✓ 参数显示正确
- ✓ 无 user_id 泄露
- ✓ 长参数值截断合理

- [ ] **Step 4: 停止服务**

Run: `docker-compose down`

---

## Task 11: 最终提交和文档更新

- [ ] **Step 1: Run final test suite**

Run: `DJANGO_SETTINGS_MODULE=core.settings.dev pytest backend/apps/agent/tests/ -v`

Expected: All PASS

- [ ] **Step 2: Update design doc status**

修改 `docs/superpowers/specs/2026-06-01-tool-call-progress-notification-design.md`:

```markdown
**状态**: Implemented ✓
**实现日期**: 2026-06-01
```

- [ ] **Step 3: Final commit**

```bash
git add docs/superpowers/specs/2026-06-01-tool-call-progress-notification-design.md
git commit -m "docs: mark tool call notification design as implemented"
```

- [ ] **Step 4: Push to remote**

```bash
git push origin main
```

---

## Self-Review Checklist

### 1. Spec Coverage

| Spec Requirement | Task Coverage |
|-----------------|---------------|
| 新增 tool_call() 方法 | Task 2 ✓ |
| 过滤 user_id/agent_name | Task 3 ✓ (verified) |
| 截断长参数值（50字符） | Task 4 ✓ (verified) |
| 无节流推送 | Task 2 ✓ (直接调用 _notify) |
| base.py 调用点 | Task 7 ✓ |
| 单元测试（4个） | Tasks 1, 3, 4, 5 ✓ |
| 集成测试（2个） | Tasks 6, 8 ✓ |
| Telegram 验证 | Task 10 ✓ |

**结论**: 所有需求已覆盖。

### 2. Placeholder Scan

- ✓ 无 TBD/TODO
- ✓ 无 "implement later"
- ✓ 无 "add validation" 模糊描述
- ✓ 所有代码步骤均有完整代码块
- ✓ 所有测试步骤均有完整测试代码
- ✓ 所有命令步骤均有具体命令和预期输出

### 3. Type Consistency

- ✓ `tool_call(tool_name: str, arguments: dict)` 签名在 Task 2 定义
- ✓ Task 7 调用签名一致：`tracker.tool_call(tc.name, tc.arguments)`
- ✓ 测试中 MagicMock 的 tc.name/tc.arguments 类型匹配
- ✓ `_notify(text: str)` 调用类型正确

**结论**: 无类型不一致。

---

## 执行成本预估

**Token消耗**:
- 单元测试编写: ~3k tokens per test × 4 tests = ~12k
- 实现编写: ~2k tokens × 2 files = ~4k
- 集成测试编写: ~4k tokens × 2 tests = ~8k
- 运行验证: ~1k tokens × 10 runs = ~10k
- 手动验证: ~2k tokens

**总计**: ~36k tokens（约 $0.20）

**时间预估**:
- TDD 循环（8个任务）: 1.5-2 小时
- 手动验证: 0.5 小时
- 总计: 2-2.5 小时