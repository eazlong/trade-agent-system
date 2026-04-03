# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

这是一个 **AI Agent 驱动的量化交易辅助系统**，用户通过 Telegram Bot 与 SupervisorAgent 交互，Agent 解析意图后路由到专业子 Agent（分析、策略、风控、教练）执行任务。所有子 Agent 和交易框架均为懒加载。

## 开发命令

### 环境初始化
```bash
cd backend
uv venv
uv pip install -r requirements.txt
cp .env.example .env
# 填入 .env 中的必填项（OPENAI_API_KEY, TELEGRAM_BOT_TOKEN, FERNET_KEY 等）
```

### 数据库
```bash
# 开发环境使用 SQLite（dev settings 已配置）
DJANGO_SETTINGS_MODULE=core.settings.dev uv run python manage.py migrate

# 生产环境（PostgreSQL）
python manage.py migrate
```

### 运行服务（开发需要同时跑多个进程）
```bash
# 终端 1: Django ASGI 服务器
DJANGO_SETTINGS_MODULE=core.settings.dev uv run python manage.py runserver

# 终端 2: Celery Worker
DJANGO_SETTINGS_MODULE=core.settings.dev uv run celery -A celery_app worker -l info

# 终端 3: Celery Beat（定时调度）
DJANGO_SETTINGS_MODULE=core.settings.dev uv run celery -A celery_app beat -l info
```

### 测试
```bash
# 运行所有测试
DJANGO_SETTINGS_MODULE=core.settings.dev uv run pytest

# 运行单个测试文件
DJANGO_SETTINGS_MODULE=core.settings.dev uv run pytest apps/agent/tests/test_xxx.py

# 带覆盖率
DJANGO_SETTINGS_MODULE=core.settings.dev uv run pytest --cov=apps --cov-report=term-missing
```

### Docker 完整栈
```bash
docker-compose up --build
```

## 关键架构模式

### 1. 注册中心模式（Agent / Skill / SkillRegistry）

**Agent 注册** — `AgentRegistry` 使用类装饰器实现懒加载单例：
```python
# backend/apps/agent/registry.py
@AgentRegistry.register_class
class MyAgent(_LLMAgent):
    name = 'my_agent'
```
首次调用 `AgentRegistry.get('my_agent')` 时才实例化。

**Skill 注册** — `SkillRegistry` 使用类装饰器：
```python
# backend/apps/skill/registry.py
@SkillRegistry.register
class MySkill(BaseSkill):
    name = 'MySkill'
```
只需在 `apps/skill/skills/__init__.py` 中 import，装饰器自动注册。

**意图路由** — `apps/agent/supervisor.py` 中 `INTENT_TO_AGENT` 字典映射意图到 Agent 名称，新增 Agent 需同时更新此映射。

### 2. 分层记忆架构

| 层 | 存储 | TTL | 管理 |
|----|------|-----|------|
| L1 | 进程内 `OrderedDict`（LRU，50条/agent） | 进程生命周期 | `apps/memory/l1.py` |
| L2 | Redis LIST | 24h | `apps/memory/l2.py` |
| L3 | PostgreSQL + pgvector（向量检索） | 永久 | `apps/memory/l3.py` |

### 3. LLM 降级链

```
OpenAI GPT-4o → (超时/429/错误) → Anthropic Claude Opus → (失败) → FALLBACK_MARKER
```
实现在 `apps/agent/llm_client.py`，Skill 内检测 `FALLBACK_MARKER` 走规则引擎兜底。

### 4. Redis Stream 消息总线

各 Stream 使用独立 DB：
- `agent:tasks` (DB3) — Agent 任务队列
- `trading:orders` (DB4) — 订单执行
- `trading:positions` (DB4) — 持仓同步
- `risk:events` (DB4) — 风险事件

### 5. Prompt 版本管理

所有 Prompt 文件在 `backend/prompts/v{N}/` 下，变更必须新建版本目录。通过 `PromptLoader`（`apps/agent/prompt_loader.py`）加载。

### 6. 设置分层

| 文件 | 用途 |
|------|------|
| `core/settings/base.py` | 通用配置（所有环境共享） |
| `core/settings/dev.py` | 开发覆盖（SQLite fallback、verbose 日志） |
| `core/settings/prod.py` | 生产覆盖（强制 PostgreSQL、严格安全设置） |

切换方式：`DJANGO_SETTINGS_MODULE=core.settings.dev`（或 `prod`）。

## 新增功能指南

### 新增 Skill
1. 创建 `apps/skill/skills/my_skill.py`，继承 `BaseSkill`
2. 用 `@SkillRegistry.register` 装饰
3. 在 `apps/skill/skills/__init__.py` 中 import

### 新增 Agent
1. 在 `apps/agent/sub_agents.py` 中继承 `_LLMAgent`
2. 用 `@AgentRegistry.register_class` 装饰
3. 在 `INTENT_TO_AGENT` 映射中添加路由规则

### 新增 Celery 定时任务
在 `backend/celery_app.py` 的 `beat_schedule` 中添加条目。

## 核心依赖说明

- **LLM**: `openai>=1.35.0`, `anthropic>=0.29.0` — 通过 `llm_client.py` 统一调用
- **交易所**: `ccxt>=4.3.50` — 支持 Binance/OKX/Bybit
- **消息队列**: Celery + Redis Stream — 非 Celery 的高吞吐任务走 Stream 直连
- **向量检索**: pgvector — 用于 L3 语义记忆的相似度搜索
- **加密**: `cryptography` Fernet — 交易所 API Key 加密存储
