# Agent 流式进度推送 + 结构化任务确认

**日期**: 2026-05-16
**状态**: 设计已批准

## 问题

通过 Agent 对话触发回测任务时：
1. 中间工具调用（write_file、submit_backtest 等）的进度对用户不可见
2. 最终回复只有 LLM 的文本，前端无法解析结构化的"提交成功/失败"状态

## 设计概览

在 Agent 处理链路中增加 `on_tool_result` 进度回调，透传到 WebSocket Consumer，实现工具调用进度的实时推送和结构化任务确认。

## 新增消息协议

| type | 触发时机 | 关键字段 |
|---|---|---|
| `tool_progress` | 每个工具执行完 | `tool`, `result` |
| `task_submitted` | 回测提交成功时 | `task_id`, `status` |

已有消息类型不变：`status`, `chat_response`, `error`。

## 改动文件

### 1. `backend/apps/agent/base.py`

`_run_tool_loop` 新增可选参数 `on_tool_result: Callable | None = None`。

每次 `_execute_tool_call` 完成后，若 `on_tool_result` 存在则调用：
```python
if on_tool_result:
    await on_tool_result(tc.name, result_text)
```

### 2. `backend/apps/agent/sub_agents.py`

`_LLMAgent.handle` 新增可选参数 `on_tool_result=None`，透传给 `_run_tool_loop`。

为保持与 `BaseAgent` 抽象接口的兼容，`handle` 签名改为：
```python
async def handle(self, message: AgentMessage, on_tool_result=None) -> AgentResult
```

### 3. `backend/apps/agent/supervisor.py`

所有路由方法增加 `on_tool_result` 透传：
- `handle(message, on_tool_result=None)` — 入口
- `_normal_route(message, on_tool_result)` — 正常路由
- `_handle_multi_turn(message, ctx, session_mgr, on_tool_result)` — 多轮对话
- `_self_execute(message, on_tool_result)` → `_run_tool_loop(..., on_tool_result)`
- `_free_chat(message, on_tool_result)` → `_run_tool_loop(..., on_tool_result)`
- `_route_to_agent(agent_name, message, on_tool_result)` → `agent.handle(message, on_tool_result)`

`BaseAgent.handle` 抽象方法签名加可选参数（默认 None），不破坏现有子类。

### 4. `backend/apps/agent/consumers.py`

`ChatConsumer._handle_chat` 变为三阶段：

**阶段 1 — 进度回调**
```python
async def on_tool_result(tool_name, result_text):
    await self.send(json.dumps({
        "type": "tool_progress",
        "tool": tool_name,
        "result": result_text[:2000],
    }))
```

**阶段 2 — 处理后检测回测提交**
```python
result = await supervisor.handle(message, on_tool_result=on_tool_result)

if isinstance(result.data, dict) and result.data.get("task_id"):
    await self.send(json.dumps({
        "type": "task_submitted",
        "task_id": result.data["task_id"],
        "status": "success" if result.success else "error",
    }))
```

**阶段 3 — 最终回复**（保持现有逻辑）

## 前端适配要点

- `tool_progress` 消息：在聊天流中展示进度指示器
- `task_submitted` 消息：渲染任务提交状态卡片，包含 task_id 和跳转链接
- 上述消息均为增量推送，不打断已有聊天流
- 前端对未知 `type` 应忽略（渐进增强）

## 风险与回滚

- `on_tool_result` 是可选参数（默认 None），不影响已有调用链
- WebSocket 推送频率受 `_max_tool_rounds` 限制（默认 5），不会造成消息风暴
- 前端对未知 type 忽略即可，旧版前端无适配也能正常工作
