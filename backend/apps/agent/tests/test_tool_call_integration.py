"""Integration tests for tool_call notification in _execute_tool_call."""

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
            # Note: base.py injects user_id after tool_call notification
            mock_tool_call.assert_called_once()
            call_args = mock_tool_call.call_args[0]
            assert call_args[0] == "get_kline_data"
            assert "symbol" in call_args[1]
            assert "interval" in call_args[1]

    # Cleanup
    tracker_context.reset(token)