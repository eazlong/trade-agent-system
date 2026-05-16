# SessionWatchdog — Session 状态定时检测与自动恢复

## 概述

新增 **SessionWatchdog** 组件，实现对 Agent 任务和用户 Session 的双级定时检测。当检测到任务超时、Consumer 崩溃、LLM 超时、工具卡死或 Session 静默过期时，自动触发混合恢复策略（优先自动重试 1-2 次，超出后降级通知用户），解决 session 任务中断不给回复的问题。

## 组件职责

### 新增：SessionWatchdog (`apps/agent/session_watchdog.py`)

ASGI lifespan 后台协程，独立于 Celery。三个扫描循环，15s 间隔：

| 方法 | 目标 | 触发条件 | 动作 |
|------|------|----------|------|
| `_scan_tasks()` | `task:progress:*` (status=running) | `age > expected_duration * 1.5` | 自动重试或降级通知 |
| `_scan_sessions()` | `session:*:context` | TTL 剩余 < 5min 且 state=MULTI_TURN | 推送续期预警 |
| `_scan_dlq()` | `agent:tasks:dlq` Stream | 有 pending 消息 | 消费后重新 `publish(agent:tasks)` |

### 修改现有组件

**AgentTaskConsumer._dispatch()** (`consumer.py:97`)
- 加 `asyncio.wait_for(supervisor.handle(), timeout=280)` 从源头防止卡死
- 捕获 `TimeoutError` → 先 `nack_and_retry` 再通知用户

**check_task_health / _handle_zombie_task** (`tasks.py:308`)
- 增加对 Stream 任务的重试分支（当前只处理 Celery 任务）
- 从 `original_task` 提取 stream payload → `publish(agent:tasks)`

**TaskTracker** (`task_tracker.py`)
- `start()` 新增 `expected_duration` 字段（默认 240s）
- 新增 `alive()` 方法更新 `last_alive` 时间戳

**nack_and_retry** (`bus.py`)
- 写入 DLQ 前确保 `original_task` 已存储到 Redis Hash

**ASGI lifespan** (`core/asgi.py`)
- 启动/停止 SessionWatchdog

## 恢复状态机

| 场景 | 检测方式 | 重试 1 | 重试 2 | 超过上限 |
|------|----------|--------|--------|----------|
| LLM 超时 | `asyncio.wait_for(280s)` | nack_and_retry(1s) | nack_and_retry(5s) | DLQ + 通知 |
| 工具卡死 | `asyncio.wait_for` 覆盖 + Watchdog `expected * 1.5` | 同 LLM | 同 LLM | 降级回复 |
| Consumer 崩溃 | `check_task_health`(300s) + Watchdog(15s) | republish | republish | 通知重新发送 |
| Session 过期 | Watchdog TTL < 5min 预警 | — | — | 走正常意图识别 |

## 数据模型

### Redis Key 新增字段（task:progress:{task_id} Hash）

| 字段 | 类型 | 说明 |
|------|------|------|
| `expected_duration` | str(int) | 预期执行时长（秒），默认 240 |
| `last_alive` | str(iso8601) | TaskTracker.alive() 更新 |

### 新增 Redis Key

| Key | 类型 | TTL | 说明 |
|-----|------|-----|------|
| `watchdog:heartbeat` | String | 30s | Watchdog 自检存活 |
| `watchdog:last_scan` | Hash | 60s | 各扫描器上次运行时间 |

## 配置常量

```python
TASK_EXPECTED_DURATION = 240       # 默认预期任务执行时间（秒）
TASK_HARD_TIMEOUT = 280            # asyncio.wait_for 硬超时（秒）
TASK_WATCHDOG_TIMEOUT = 360        # Watchdog 判定超时 = expected * 1.5（秒）
WATCHDOG_MAX_AUTO_RETRY = 2        # Watchdog 自动重试上限
WATCHDOG_RETRY_DELAYS = [10, 60]   # Watchdog 重试退避（秒）
WATCHDOG_SCAN_INTERVAL = 15        # 扫描间隔（秒）
WATCHDOG_SESSION_WARN_BEFORE = 300 # TTL 剩余预警阈值（秒）
```

## SessionWatchdog 类接口

```python
class SessionWatchdog:
    _scan_interval: int = 15
    _running: bool
    _scan_task: asyncio.Task | None

    async def start(self) -> None       # ASGI lifespan 中启动
    async def stop(self) -> None
    async def _scan_loop(self) -> None  # 主循环，每 15s 调用三个扫描器
    async def _scan_tasks(self) -> int   # 返回发现的问题任务数
    async def _scan_sessions(self) -> int # 返回预警的 session 数
    async def _scan_dlq(self) -> int     # 返回消费的 DLQ 消息数
    async def _recover_task(self, task_id, payload) -> None
```

### _scan_tasks() 逻辑

1. `SCAN task:progress:*` 过滤 `status=running`
2. 计算 `age = now - (last_alive or updated_at)`
3. 若 `age > expected_duration * 1.5`：
   - `retry_count < 2`: `_recover_task()` 重试
   - `retry_count >= 2`: 降级通知，不再重试

### _recover_task() 逻辑

1. 读取 `original_task`
2. Stream payload → `publish(agent:tasks, payload)`
3. Celery 任务 → `app.send_task(...)`
4. 更新 `retry_count += 1` + 通知用户

### _scan_dlq() 逻辑

1. `XREADGROUP agent:tasks:dlq CG_AGENTS (count=4)`
2. 提取原始 payload, reset `retry_count=0`
3. `publish(agent:tasks)` + `XACK` + 通知

## 文件改动清单

| 文件 | 操作 | 内容 |
|------|------|------|
| `apps/agent/session_watchdog.py` | 新建 | SessionWatchdog 完整实现 |
| `apps/agent/consumer.py` | 改 `_dispatch()` | 加 `asyncio.wait_for(280s)` |
| `apps/agent/task_tracker.py` | 改 `start()` + 加 `alive()` | `expected_duration` 和 `last_alive` |
| `apps/agent/tasks.py` | 改 `_handle_zombie_task()` | 加 Stream 任务重试分支 |
| `apps/agent/bus.py` | 改 `nack_and_retry()` | DLQ 前写 `original_task` |
| `apps/agent/supervisor.py` | 改工具调用路径 | 长工具后调用 `tracker.alive()` |
| `core/asgi.py` | 改 lifespan | 启动/停止 Watchdog |
| `apps/agent/tests/test_session_watchdog.py` | 新建 | 单元测试 |

### 不变的部分

- `SessionManager` / `session_manager.py`
- `TelegramChannel` / `telegram.py`（通知走现有 `_send_notification_redis` 通道）
- `check_task_health` 保留现有逻辑，仅增加 Stream 分支
- `AgentTaskConsumer._poll_loop` / `_handle` 外层结构不变

## 测试策略

### 单元测试

- `_scan_tasks()` 正确识别超时任务（Mock Redis 注入假数据）
- `_recover_task()` 正确重试 stream/celery 任务（Mock bus.publish）
- `_scan_sessions()` TTL 预警阈值计算正确
- `_scan_dlq()` 消费并重新发布正确
- `asyncio.wait_for` 超时触发 TimeoutError（Mock supervisor.handle 为 sleep）
- `_handle_zombie_task` Stream 分支正确

### 集成测试

- Watchdog 启动 → 检测 → 恢复 → 用户收到通知全链路
- Consumer 崩溃 → Watchdog 兜底重试
- DLQ 消息 → 自动重新排队
