---
name: Async Task Progress Monitoring Design
description: Unified task progress tracking and notification system for long-running async tasks (Celery + Redis Stream)
---

# 异步任务进度监控系统设计

## 问题

长时间异步任务（回测、数据拉取、策略分析等）在执行过程中不向用户推送任何信息。Telegram 用户发送指令后需等待数分钟，中间无反馈，容易误判任务卡死而重复发起。

## 目标

1. 实时推送任务进度到 Telegram，用户不再需要盲目等待
2. 统一的进度抽象，同时覆盖 Celery 任务和 Redis Stream Agent 任务
3. 低侵入性 — 现有任务只需添加少量 `tracker.milestone()` 调用
4. 支持历史任务追溯和审计

## 方案决策

| 维度 | 选择 | 理由 |
|------|------|------|
| 覆盖范围 | Celery + Redis Stream 统一 | 避免两套监控逻辑 |
| 推送模式 | 事件驱动（主动上报） | 实时性最佳 |
| 消息粒度 | 关键里程碑 + 心跳保活 | 语义清晰，避免消息轰炸 |
| 状态存储 | Redis Hash（运行中） + PG 归档（已完成） | 兼顾性能和历史追溯 |
| 侵入性 | ContextVar + 轻量回调 | 不改函数签名，最小侵入 |
| 心跳机制 | 自动后台线程 + 手动辅助 | 任务代码无感知 |

## 架构

```
┌─────────────────────┐
│   Task Code         │  (Celery task / Agent)
│   tracker.milestone()│
└──────────┬──────────┘
           │
    ┌──────▼──────┐
    │ TaskTracker  │  ← ContextVar 获取当前实例
    │              │
    │ • milestone() │   关键节点 → 立即推送（带节流）
    │ • heartbeat() │   心跳保活 → 自动线程，按间隔推送
    │ • complete()  │   完成 → 推送结果 + 归档到 PG
    │ • fail()      │   失败 → 推送错误 + 归档
    └──┬───────┬───┘
       │       │
  ┌────▼──┐ ┌──▼────────┐
  │ Redis  │ │ Channel   │
  │ Hash   │ │ (Telegram)│
  │ 运行中  │ │ notify()  │
  └────┬───┘ └───────────┘
       │
  ┌────▼────────┐
  │ PostgreSQL  │  TaskProgress 模型（异步归档）
  │ TaskProgress│
  └─────────────┘
```

## 核心组件

### 1. TaskTracker (`backend/apps/agent/task_tracker.py`)

```python
tracker_context = ContextVar("tracker", default=None)

class TaskTracker:
    def __init__(self, task_id, user_id, channel="telegram"):
        self.task_id = task_id
        self.user_id = user_id
        self.channel = channel
        self._last_push = 0
        self._milestones = []
        self._heartbeat_interval = 120  # 秒
        self._stop_event = Event()
        self._thread = None

    def start(self):
        """写 Redis + 推送开始通知 + 启动心跳线程"""

    def milestone(self, message: str, progress: float | None = None):
        """关键里程碑：节流后推送"""

    def heartbeat(self):
        """手动心跳（通常自动运行）"""

    def complete(self, result: str):
        """推送结果 + Redis 标记 + 触发异步归档"""

    def fail(self, error: str):
        """推送错误 + 归档"""

    def stop(self):
        """停止心跳线程"""
```

### 2. Redis Hash 结构

```
Key: task:progress:{task_id}
TTL: 24h

Fields: task_id, user_id, channel, status, step, progress,
        last_milestone, last_heartbeat, created_at, completed_at, result
```

### 3. PostgreSQL 模型 `TaskProgress`

```python
class TaskProgress(models.Model):
    id = UUIDField(primary_key=True)
    user = ForeignKey(User)
    task_type = CharField()   # "backtest", "agent", "data_fetch"
    status = CharField()      # running/completed/failed
    progress = FloatField()
    milestones = JSONField()  # [{time, message, progress}, ...]
    result = TextField()
    created_at / completed_at
```

### 4. Celery Signal 自动集成 (`backend/apps/agent/signals.py`)

利用 `task_prerun` / `task_postrun` / `task_failure` signals 自动处理生命周期，
任务代码只需在关键位置调用 `tracker.milestone()`。

### 5. 归档任务 (`backend/apps/agent/tasks.py` 新增)

`archive_task_progress(task_id)` — 异步将 Redis 中的完成态任务归档到 PostgreSQL。

## 使用示例

```python
@app.task(bind=True, track_started=True)
def run_backtest_task(self, ..., user_id=None):
    tracker = TaskTracker(task_id=self.request.id, user_id=user_id)
    token = tracker_context.set(tracker)
    tracker.start()
    try:
        # 数据下载阶段
        for i, day in enumerate(days):
            data = fetch_day(day)
            if i % 10 == 0:
                tracker.milestone(f"已下载 {i}/{len(days)} 天数据", progress=i/len(days))
        
        # 回测执行阶段
        tracker.milestone("开始执行回测...", progress=0.8)
        stats = runner.run_backtest(...)
        tracker.complete(f"回测完成：{stats['total_trades']} 笔交易")
    except Exception as e:
        tracker.fail(str(e))
    finally:
        tracker.stop()
        tracker_context.reset(token)
```

## 节流策略

- `milestone()` 调用时，如果距上次推送 < 120s，仅记录到 Redis 不推送 Telegram
- 心跳线程每 120s 检查，如果超过间隔无 milestone 推送则发"⏳ 任务仍在运行中..."
- 关键里程碑（start/complete/fail）不受节流限制，始终推送

## 异常处理

| 场景 | 处理 |
|------|------|
| Worker 进程崩溃 | 心跳停止，监控检测超时后推送"任务异常中断" |
| 推送失败（Telegram 离线） | warn 日志，不影响任务继续 |
| 嵌套任务 | ContextVar 栈式 push/pop |
| Redis 不可用 | 降级到仅日志，不阻塞任务 |

## 文件变更

### 新增文件
- `backend/apps/agent/task_tracker.py` — TaskTracker 核心类
- `backend/apps/agent/signals.py` — Celery signals 集成
- `backend/apps/agent/migrations/XXXX_taskprogress.py` — PG 模型迁移

### 修改文件
- `backend/apps/agent/tasks.py` — 添加归档任务 + 集成 tracker
- `backend/apps/backtest/tasks.py` — 集成 tracker milestone
- `backend/apps/agent/bus.py` — Stream 任务进度更新支持
- `backend/apps/channel/telegram.py` — 新增进度通知方法
