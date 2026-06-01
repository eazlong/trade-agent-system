# 工具调用进度推送优化设计

**日期**: 2026-06-01
**状态**: Implemented ✓
**实现日期**: 2026-06-01
**作者**: Claude Code

---

## 背景

当前系统中，Agent 任务进度推送仅覆盖关键节点（启动、里程碑、完成、失败），用户无法实时了解工具调用细节。复杂任务可能调用 20-50 次工具，用户在 Telegram/Lark 端无法感知执行过程，体验不够透明。

## 目标

为每次工具调用推送一行简要通知，包含工具名称和关键参数，实现完全透明的任务执行流程。

## 需求

1. **推送内容**: 工具名称 + 关键参数（例如："正在执行：get_kline_data（symbol=BTCUSDT）"）
2. **推送时机**: 每次工具调用都推送，不节流
3. **推送渠道**: 沿用现有渠道（Telegram/Lark），与里程碑消息混合推送
4. **工具范围**: 所有工具都推送，包括辅助工具（load_skill、resolve_user），完全透明

---

## 设计方案

### 方案选择

**选定方案**: 在 TaskTracker 中新增 `tool_call()` 方法

**理由**:
- 语义清晰：工具调用独立于里程碑概念
- 实现简洁：仅修改 2 个文件
- 无节流干扰：新方法独立，不影响现有 milestone 节流逻辑
- 扩展性强：未来可增加 `tool_result()` 方法推送执行结果

---

## 架构设计

### 数据流

```
用户消息 → SupervisorAgent → _run_tool_loop
                              ↓
                         _execute_tool_call
                              ↓
                    检查 tracker_context
                              ↓
                  tracker.tool_call(name, args)
                              ↓
                    _notify(text) → Redis pubsub
                              ↓
                    ASGI consumer → Telegram/Lark
```

### 改动文件

1. `backend/apps/agent/task_tracker.py` - 新增 `tool_call()` 方法
2. `backend/apps/agent/base.py` - `_execute_tool_call()` 中调用 `tracker.tool_call()`

---

## 实现细节

### 1. TaskTracker 新增方法

**文件**: `backend/apps/agent/task_tracker.py`

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

**关键设计点**:
- 过滤内部参数（user_id、agent_name）避免泄露敏感信息
- 截断长参数值（`repr(v)[:50]`）防止消息过长
- 调用 `alive()` 保持现有存活检测逻辑
- 单独 logger.debug 便于调试

### 2. base.py 调用点修改

**文件**: `backend/apps/agent/base.py`

**位置**: `_execute_tool_call()` 方法开头

```python
async def _execute_tool_call(self, tc) -> str:
    """执行单个工具调用，返回结果字符串。"""
    try:
        # Push tool call notification
        from .task_tracker import tracker_context
        tracker = tracker_context.get(None)
        if tracker is not None:
            tracker.tool_call(tc.name, tc.arguments)

        # Existing execution logic...
        if tc.name == "load_skill":
            ...
```

**插入时机**: 在工具执行前推送，用户看到 "正在执行" 而不是 "已执行"。

---

## 错误处理

### 1. TaskTracker 未初始化

**防护**: 已在 base.py 中检查 `tracker is not None`

**行为**: 如果 tracker 未初始化（单元测试、非 Agent 任务），静默跳过，不报错。

### 2. 参数值过长

**处理**: 参数值超过 50 字符截断

**理由**: 避免 Telegram 消息超长（4096 字符限制）

### 3. Redis pubsub 推送失败

**处理**: `task_tracker.py::_notify()` 已捕获异常

**行为**: 推送失败不影响工具执行，仅 warning 日志

### 4. 特殊工具的参数过滤

**当前过滤**: `user_id`、`agent_name`

**扩展考虑**: 未来可在配置中声明过滤列表（如 API key、password）

**当前设计**: 硬编码过滤足够覆盖现有工具

### 5. 并发工具调用

**场景**: `_run_tool_loop` 可能一次响应包含多个 tool_calls

**行为**: 工具按执行顺序推送，不会并发推送（Redis pubsub 单线程消费）

---

## 测试策略

### 1. 单元测试（task_tracker.py）

**测试用例**:
- `test_tool_call_basic`: 基本通知推送 + 消息格式验证
- `test_tool_call_filters_user_id`: 验证过滤 user_id
- `test_tool_call_truncates_long_values`: 验证截断长参数值
- `test_tool_call_without_tracker_context`: 验证无 tracker 时静默跳过

### 2. 集成测试（base.py + TaskTracker）

**测试用例**:
- `test_execute_tool_call_pushes_notification`: 验证 `_execute_tool_call` 推送通知
- `test_multiple_tool_calls_push_sequentially`: 验证多工具顺序推送

### 3. E2E 测试（Supervisor → Telegram）

**测试用例**:
- `test_user_message_triggers_tool_notifications`: 验证用户消息触发完整推送流程

### 测试覆盖率目标

- `TaskTracker.tool_call()` 方法：100% 覆盖
- `base._execute_tool_call()` 通知逻辑：100% 覆盖
- 集成场景（tracker_context 传递）：关键路径覆盖

---

## 影响评估

### 用户体验

- **正面**: 任务执行过程完全透明，用户实时感知进度
- **潜在问题**: 工具调用频繁时消息量大（20-50 条）

### 系统性能

- **Redis pubsub**: 每次工具调用增加一次 publish 操作（轻量）
- **Telegram API**: 受消息频率限制，需监控是否触发限流

### 维护成本

- **低**: 仅修改 2 个文件，逻辑简单清晰

---

## 未来扩展

1. **可选推送粒度**: 通过环境变量配置（verbose/normal/minimal）
2. **工具执行结果推送**: 新增 `tool_result()` 方法
3. **敏感参数配置化**: 在 settings 中声明过滤列表
4. **Web UI 实时流**: WebSocket 推送详细执行日志

---

## 实现计划

**预估工作量**: 2-4 小时

**关键步骤**:
1. 新增 `TaskTracker.tool_call()` 方法
2. 修改 `base._execute_tool_call()` 调用点
3. 编写单元测试 + 集成测试
4. 手动验证 Telegram 推送效果
5. 监控生产环境消息频率

---

## 参考资料

- 现有实现: `backend/apps/agent/task_tracker.py` (milestone/complete/fail)
- 工具调用流程: `backend/apps/agent/base.py::_run_tool_loop`
- Redis pubsub: `backend/apps/agent/task_tracker.py::_send_notification_redis`