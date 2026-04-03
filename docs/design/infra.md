# 基础设施详细设计文档

> 设计师A | 版本：v1.0 | 基于 architecture_final.md (Final-R2)

---

## 目录

1. [Django项目结构](#一django项目结构)
2. [PostgreSQL + TimescaleDB + pgvector 配置](#二postgresql--timescaledb--pgvector-配置)
3. [Redis Stack配置](#三redis-stack配置)
4. [Docker Compose服务定义](#四docker-compose服务定义)
5. [FrameManager详细实现](#五framemanager详细实现)
6. [Celery任务配置](#六celery任务配置)
7. [环境变量清单](#七环境变量清单)

---

## 一、Django项目结构

### 1.1 Apps划分

```
backend/
├── core/                        # Django项目核心
│   ├── settings/
│   │   ├── __init__.py
│   │   ├── base.py              # 公共配置
│   │   ├── dev.py               # 开发环境（覆盖base）
│   │   └── prod.py              # 生产环境（覆盖base）
│   ├── urls.py                  # 主路由
│   ├── asgi.py                  # ASGI入口（Django Channels）
│   └── wsgi.py
│
├── apps/
│   ├── authentication/          # 用户认证（JWT + 本地登录）
│   │   ├── models.py            # User模型
│   │   ├── views.py
│   │   ├── serializers.py
│   │   ├── urls.py
│   │   └── tests/
│   │
│   ├── agent/                   # Agent管理框架（主框架，常驻）
│   │   ├── models.py            # AgentSession, AgentLog
│   │   ├── frame_manager.py     # FrameManager（核心，见§五）
│   │   ├── supervisor.py        # SupervisorAgent
│   │   ├── base_agent.py        # BaseAgent抽象类
│   │   ├── registry.py          # AgentRegistry
│   │   ├── views.py             # Agent状态查询API
│   │   ├── urls.py
│   │   └── consumers.py         # WebSocket Consumer（Agent推送）
│   │
│   ├── skill/                   # Skill系统
│   │   ├── base_skill.py        # BaseSkill抽象类
│   │   ├── registry.py          # SkillRegistry
│   │   ├── context.py           # SkillContext数据结构
│   │   └── skills/              # 具体Skill实现目录
│   │       ├── analysis_skill.py
│   │       ├── signal_skill.py
│   │       ├── strategy_gen_skill.py
│   │       ├── backtest_skill.py
│   │       ├── plan_skill.py
│   │       ├── review_skill.py
│   │       ├── summary_skill.py
│   │       ├── risk_assess_skill.py
│   │       └── channel_route_skill.py
│   │
│   ├── trading/                 # 交易框架（懒加载）
│   │   ├── models.py            # Order, Position, TradingPlan
│   │   ├── executor.py          # OrderExecutor
│   │   ├── adapters/
│   │   │   ├── base.py          # BaseExchangeAdapter
│   │   │   ├── binance.py
│   │   │   └── okx.py
│   │   ├── position_manager.py  # 持仓管理 + 5min对账
│   │   ├── views.py
│   │   └── urls.py
│   │
│   ├── riskguard/               # RiskGuard（随交易/辅助框架按需启动）
│   │   ├── models.py            # RiskEvent, CircuitBreakerLog
│   │   ├── guard.py             # RiskGuard核心
│   │   ├── rules.py             # 规则引擎
│   │   └── views.py
│   │
│   ├── backtest/                # 回测框架（懒加载）
│   │   ├── models.py            # BacktestTask, BacktestResult
│   │   ├── engine.py            # 回测引擎
│   │   ├── data_cursor.py       # DataCursor（防前视偏差）
│   │   ├── views.py
│   │   └── urls.py
│   │
│   ├── memory/                  # 分层记忆系统
│   │   ├── l1_cache.py          # L1：Redis TTL=2h
│   │   ├── l2_cache.py          # L2：Redis TTL=7d
│   │   ├── l3_store.py          # L3：pgvector持久化
│   │   └── memory_manager.py    # 统一读写入口
│   │
│   ├── channel/                 # 通知通道（Telegram等）
│   │   ├── base_channel.py      # BaseChannel抽象类
│   │   ├── telegram_channel.py  # TelegramChannel实现
│   │   ├── views.py             # Webhook接收端点
│   │   └── urls.py
│   │
│   └── notify/                  # 系统通知（WebSocket推送）
│       ├── consumers.py
│       └── urls.py
│
├── prompts/                     # Prompt版本管理（ADR-004）
│   ├── v1/
│   │   ├── supervisor.txt
│   │   ├── analyst.txt
│   │   └── coach.txt
│   └── v2/                      # 新版本必须新建目录
│
├── celery_app.py                # Celery实例
├── manage.py
├── requirements.txt
└── .env.example
```

### 1.2 Settings分层

```python
# core/settings/base.py
from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = os.environ['DJANGO_SECRET_KEY']

INSTALLED_APPS = [
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'rest_framework',
    'rest_framework_simplejwt',
    'channels',
    'corsheaders',
    # 项目Apps
    'apps.authentication',
    'apps.agent',
    'apps.skill',
    'apps.trading',
    'apps.riskguard',
    'apps.backtest',
    'apps.memory',
    'apps.channel',
    'apps.notify',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
]

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ['POSTGRES_DB'],
        'USER': os.environ['POSTGRES_USER'],
        'PASSWORD': os.environ['POSTGRES_PASSWORD'],
        'HOST': os.environ.get('POSTGRES_HOST', 'localhost'),
        'PORT': os.environ.get('POSTGRES_PORT', '5432'),
        'CONN_MAX_AGE': 60,
        'OPTIONS': {'connect_timeout': 10},
    }
}

CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            'hosts': [os.environ.get('REDIS_URL', 'redis://localhost:6379')],
        },
    },
}

CELERY_BROKER_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379')
CELERY_RESULT_BACKEND = os.environ.get('REDIS_URL', 'redis://localhost:6379')

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '20/min',
        'user': '200/min',
    },
}
```

```python
# core/settings/dev.py
from .base import *

DEBUG = True
ALLOWED_HOSTS = ['*']

INSTALLED_APPS += ['django_extensions']

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {'class': 'logging.StreamHandler'},
    },
    'root': {
        'handlers': ['console'],
        'level': 'DEBUG',
    },
}
```

```python
# core/settings/prod.py
from .base import *

DEBUG = False
ALLOWED_HOSTS = [os.environ['ALLOWED_HOST']]

SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'json': {'()': 'pythonjsonlogger.jsonlogger.JsonFormatter'},
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'json',
        },
    },
    'root': {'handlers': ['console'], 'level': 'INFO'},
}
```

### 1.3 URL路由

```python
# core/urls.py
from django.urls import path, include

urlpatterns = [
    path('api/auth/', include('apps.authentication.urls')),
    path('api/agent/', include('apps.agent.urls')),
    path('api/trading/', include('apps.trading.urls')),
    path('api/backtest/', include('apps.backtest.urls')),
    path('api/channel/', include('apps.channel.urls')),
]

# core/asgi.py
from django.urls import re_path
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack

from apps.agent.consumers import AgentConsumer
from apps.notify.consumers import NotifyConsumer

application = ProtocolTypeRouter({
    'http': get_asgi_application(),
    'websocket': AuthMiddlewareStack(
        URLRouter([
            re_path(r'^ws/agent/$', AgentConsumer.as_asgi()),
            re_path(r'^ws/notify/$', NotifyConsumer.as_asgi()),
        ])
    ),
})
```

---

## 二、PostgreSQL + TimescaleDB + pgvector 配置

### 2.1 初始化脚本

```sql
-- init.sql（Docker Compose entrypoint执行）

-- 启用扩展
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;
CREATE EXTENSION IF NOT EXISTS vector;  -- pgvector

-- ===== 用户表 =====
CREATE TABLE IF NOT EXISTS users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           VARCHAR(255) UNIQUE NOT NULL,
    username        VARCHAR(64) UNIQUE NOT NULL,
    password_hash   VARCHAR(255) NOT NULL,
    is_frozen       BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login_at   TIMESTAMPTZ
);

-- ===== 分层记忆 L3 =====
CREATE TABLE IF NOT EXISTS agent_memory (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id),
    agent_type  VARCHAR(64) NOT NULL,
    content     TEXT NOT NULL,
    embedding   vector(1536),  -- OpenAI text-embedding-3-small
    memory_type VARCHAR(16) NOT NULL CHECK (memory_type IN ('decision', 'preference', 'event')),
    importance  SMALLINT NOT NULL DEFAULT 1 CHECK (importance BETWEEN 1 AND 5),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_agent_memory_vector ON agent_memory
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX idx_agent_memory_user ON agent_memory(user_id, agent_type);

-- ===== K线时序数据 =====
CREATE TABLE IF NOT EXISTS klines (
    time        TIMESTAMPTZ NOT NULL,
    exchange    VARCHAR(32) NOT NULL,
    symbol      VARCHAR(32) NOT NULL,
    timeframe   VARCHAR(8) NOT NULL,
    open        NUMERIC(20, 8) NOT NULL,
    high        NUMERIC(20, 8) NOT NULL,
    low         NUMERIC(20, 8) NOT NULL,
    close       NUMERIC(20, 8) NOT NULL,
    volume      NUMERIC(30, 8) NOT NULL
);
SELECT create_hypertable('klines', 'time',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE
);
CREATE UNIQUE INDEX idx_klines_pk
    ON klines(time, exchange, symbol, timeframe);

-- 连续聚合（1h自动聚合）
CREATE MATERIALIZED VIEW IF NOT EXISTS klines_1h
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', time) AS bucket,
    exchange, symbol,
    first(open, time)  AS open,
    max(high)          AS high,
    min(low)           AS low,
    last(close, time)  AS close,
    sum(volume)        AS volume
FROM klines
WHERE timeframe = '1m'
GROUP BY bucket, exchange, symbol;

-- ===== 交易计划 =====
CREATE TABLE IF NOT EXISTS trading_plans (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id),
    symbol          VARCHAR(32) NOT NULL,
    direction       VARCHAR(8) NOT NULL CHECK (direction IN ('long', 'short')),
    entry_price     NUMERIC(20, 8),
    stop_loss       NUMERIC(20, 8),
    take_profit     NUMERIC(20, 8),
    risk_ratio      NUMERIC(6, 4),
    status          VARCHAR(16) NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft','active','closed','cancelled')),
    plan_text       TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ===== 订单 =====
CREATE TABLE IF NOT EXISTS orders (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id),
    plan_id         UUID REFERENCES trading_plans(id),
    exchange        VARCHAR(32) NOT NULL,
    symbol          VARCHAR(32) NOT NULL,
    order_type      VARCHAR(16) NOT NULL,
    side            VARCHAR(8) NOT NULL CHECK (side IN ('buy', 'sell')),
    quantity        NUMERIC(30, 8) NOT NULL,
    price           NUMERIC(20, 8),
    exchange_order_id VARCHAR(128),
    status          VARCHAR(16) NOT NULL DEFAULT 'pending',
    filled_qty      NUMERIC(30, 8) DEFAULT 0,
    avg_fill_price  NUMERIC(20, 8),
    fee             NUMERIC(20, 8) DEFAULT 0,
    risk_approved   BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_orders_user_status ON orders(user_id, status);
CREATE INDEX idx_orders_exchange ON orders(exchange, exchange_order_id);

-- ===== 风控事件 =====
CREATE TABLE IF NOT EXISTS risk_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id),
    event_type      VARCHAR(32) NOT NULL,
    severity        VARCHAR(8) NOT NULL CHECK (severity IN ('info','warn','critical')),
    detail          JSONB NOT NULL DEFAULT '{}',
    triggered_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_risk_events_user ON risk_events(user_id, triggered_at DESC);
```

### 2.2 连接池配置

```python
# Django DATABASES（base.py）已配置 CONN_MAX_AGE=60
# 生产环境推荐使用 pgBouncer（Transaction模式）
# pgBouncer pool_size = 20 per host, max_client_conn = 100

---

## 三、Redis Stack配置

### 3.1 Redis用途分区

```
DB 0 — Django Channels Layer（WebSocket组管理）
DB 1 — Django Cache（通用缓存，TTL=5min默认）
DB 2 — Celery Broker / Result Backend
DB 3 — 分层记忆 L1（TTL=2h）
DB 4 — 分层记忆 L2（TTL=7d）
DB 5 — 持仓缓存（position:{user_id}:{symbol}）
DB 6 — Redis Stream（消息总线）
DB 7 — RiskGuard 熔断开关（circuit_breaker:{user_id}）
```

### 3.2 Redis Stream键名约定

```
agent:tasks          → Agent任务队列，SupervisorAgent消费
trading:orders       → 订单队列，OrderConsumer消费
trading:positions    → 持仓更新事件，前端WebSocket订阅
risk:events          → 风控事件广播
system:heartbeat     → 各服务心跳（TTL=30s）
```

### 3.3 消费者组配置

```python
import redis.asyncio as aioredis

REDIS_STREAM_GROUPS = [
    ('agent:tasks',    'agents'),
    ('trading:orders', 'executor'),
    ('risk:events',    'risk_monitor'),
]

async def ensure_stream_groups(r: aioredis.Redis):
    """启动时幂等创建Stream消费者组"""
    for stream_key, group_name in REDIS_STREAM_GROUPS:
        try:
            await r.xgroup_create(stream_key, group_name, id='0', mkstream=True)
        except aioredis.ResponseError as e:
            if 'BUSYGROUP' not in str(e):
                raise
```

### 3.4 AOF持久化（redis.conf）

```conf
aof-use-rdb-preamble yes
appendonly yes
appendfsync everysec
auto-aof-rewrite-percentage 100
auto-aof-rewrite-min-size 64mb

# 内存上限与淘汰策略（非持久化DB可淘汰）
maxmemory 2gb
maxmemory-policy allkeys-lru
```

---

## 四、Docker Compose服务定义

```yaml
# docker-compose.yml
version: '3.9'

services:
  db:
    image: timescale/timescaledb-ha:pg16-latest  # 包含pgvector
    environment:
      POSTGRES_DB: ${POSTGRES_DB}
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - postgres_data:/home/postgres/pgdata/data
      - ./init.sql:/docker-entrypoint-initdb.d/init.sql
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis/redis-stack:latest
    command: redis-server /usr/local/etc/redis/redis.conf
    volumes:
      - redis_data:/data
      - ./redis.conf:/usr/local/etc/redis/redis.conf
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      retries: 5

  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile
    command: >-
      sh -c "python manage.py migrate &&
             daphne -b 0.0.0.0 -p 8000 core.asgi:application"
    environment:
      DJANGO_SETTINGS_MODULE: core.settings.prod
      DATABASE_URL: postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@db:5432/${POSTGRES_DB}
      REDIS_URL: redis://redis:6379
    env_file: .env
    ports:
      - "8000:8000"
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/api/health/"]
      interval: 30s
      retries: 3

  celery_worker:
    build:
      context: ./backend
    command: celery -A celery_app worker -Q default,agent,trading -c 4 --loglevel=info
    env_file: .env
    environment:
      DJANGO_SETTINGS_MODULE: core.settings.prod
      REDIS_URL: redis://redis:6379
    depends_on:
      - backend
      - redis

  celery_beat:
    build:
      context: ./backend
    command: celery -A celery_app beat --loglevel=info --scheduler django_celery_beat.schedulers:DatabaseScheduler
    env_file: .env
    environment:
      DJANGO_SETTINGS_MODULE: core.settings.prod
      REDIS_URL: redis://redis:6379
    depends_on:
      - backend
      - redis

  frontend:
    build:
      context: ./frontend
    ports:
      - "3000:80"
    depends_on:
      - backend

  minio:
    image: minio/minio:latest
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: ${MINIO_ACCESS_KEY}
      MINIO_ROOT_PASSWORD: ${MINIO_SECRET_KEY}
    volumes:
      - minio_data:/data
    ports:
      - "9000:9000"
      - "9001:9001"

volumes:
  postgres_data:
  redis_data:
  minio_data:
```

---

## 五、FrameManager详细实现

FrameManager 是懒加载机制的核心，负责按需启动/停止交易框架、辅助框架、回测框架，并同步管理 RiskGuard 生命周期。

```python
# apps/agent/frame_manager.py
from __future__ import annotations

import asyncio
import logging
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class FrameType(str, Enum):
    TRADING  = 'trading'
    AUXILIARY = 'auxiliary'
    BACKTEST  = 'backtest'


class FrameState(str, Enum):
    STOPPED  = 'stopped'
    STARTING = 'starting'
    RUNNING  = 'running'
    STOPPING = 'stopping'


class FrameManager:
    """
    管理交易框架、辅助框架、回测框架的生命周期。
    所有框架预置在系统中，初始化时不启动，Agent按需调用 start/stop。
    RiskGuard 随交易框架或辅助框架一同启动/停止（架构R1约束）。
    """

    def __init__(self):
        self._states: dict[FrameType, FrameState] = {
            FrameType.TRADING:   FrameState.STOPPED,
            FrameType.AUXILIARY: FrameState.STOPPED,
            FrameType.BACKTEST:  FrameState.STOPPED,
        }
        self._riskguard_active = False
        self._lock = asyncio.Lock()
        # 延迟导入，避免启动时加载
        self._trading_executor  = None
        self._auxiliary_monitor = None
        self._backtest_engine   = None
        self._riskguard         = None

    async def start_frame(self, frame_type: FrameType) -> None:
        async with self._lock:
            state = self._states[frame_type]
            if state == FrameState.RUNNING:
                return
            if state == FrameState.STARTING:
                logger.warning(f'{frame_type} already starting, skip')
                return

            self._states[frame_type] = FrameState.STARTING
            try:
                await self._do_start(frame_type)
                self._states[frame_type] = FrameState.RUNNING
                logger.info(f'Frame {frame_type} started')
            except Exception as e:
                self._states[frame_type] = FrameState.STOPPED
                logger.error(f'Frame {frame_type} start failed: {e}')
                raise

    async def stop_frame(self, frame_type: FrameType) -> None:
        async with self._lock:
            if self._states[frame_type] != FrameState.RUNNING:
                return
            self._states[frame_type] = FrameState.STOPPING
            try:
                await self._do_stop(frame_type)
                self._states[frame_type] = FrameState.STOPPED
                logger.info(f'Frame {frame_type} stopped')
            except Exception as e:
                logger.error(f'Frame {frame_type} stop error: {e}')
                self._states[frame_type] = FrameState.STOPPED
                raise

    async def _do_start(self, frame_type: FrameType) -> None:
        if frame_type == FrameType.TRADING:
            from apps.trading.executor import OrderExecutor
            from apps.riskguard.guard import RiskGuard
            self._trading_executor = OrderExecutor()
            await self._trading_executor.initialize()
            self._riskguard = RiskGuard(mode='trading')
            await self._riskguard.start()
            self._riskguard_active = True

        elif frame_type == FrameType.AUXILIARY:
            from apps.trading.position_manager import PositionMonitor
            from apps.riskguard.guard import RiskGuard
            self._auxiliary_monitor = PositionMonitor()
            await self._auxiliary_monitor.start()
            if not self._riskguard_active:
                self._riskguard = RiskGuard(mode='monitor')
                await self._riskguard.start()
                self._riskguard_active = True

        elif frame_type == FrameType.BACKTEST:
            from apps.backtest.engine import BacktestEngine
            self._backtest_engine = BacktestEngine()
            await self._backtest_engine.initialize()
            # 回测不启动 RiskGuard

    async def _do_stop(self, frame_type: FrameType) -> None:
        if frame_type == FrameType.TRADING:
            if self._trading_executor:
                await self._trading_executor.shutdown()
                self._trading_executor = None
            await self._stop_riskguard_if_idle()

        elif frame_type == FrameType.AUXILIARY:
            if self._auxiliary_monitor:
                await self._auxiliary_monitor.stop()
                self._auxiliary_monitor = None
            await self._stop_riskguard_if_idle()

        elif frame_type == FrameType.BACKTEST:
            if self._backtest_engine:
                await self._backtest_engine.teardown()
                self._backtest_engine = None

    async def _stop_riskguard_if_idle(self) -> None:
        """仅在交易框架和辅助框架都已停止时才停止RiskGuard"""
        trading_stopped  = self._states[FrameType.TRADING]  in (FrameState.STOPPED, FrameState.STOPPING)
        auxiliary_stopped = self._states[FrameType.AUXILIARY] in (FrameState.STOPPED, FrameState.STOPPING)
        if trading_stopped and auxiliary_stopped and self._riskguard_active:
            await self._riskguard.stop()
            self._riskguard = None
            self._riskguard_active = False
            logger.info('RiskGuard stopped (no active frames)')

    def get_status(self) -> dict:
        return {
            'frames': {ft.value: fs.value for ft, fs in self._states.items()},
            'riskguard': 'active' if self._riskguard_active else 'inactive',
        }


# 单例（每个Django进程一个实例）
frame_manager = FrameManager()
```

---

## 六、Celery任务配置

```python
# celery_app.py
from celery import Celery
import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings.prod')

app = Celery('trade_agent')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

# 队列路由
app.conf.task_routes = {
    'apps.agent.tasks.*':   {'queue': 'agent'},
    'apps.trading.tasks.*': {'queue': 'trading'},
    '*':                    {'queue': 'default'},
}

# 定时任务（ADR-001：5分钟持仓对账）
app.conf.beat_schedule = {
    'sync-positions-every-5min': {
        'task': 'apps.trading.tasks.sync_positions_from_exchange',
        'schedule': 300.0,  # 5分钟
    },
    'archive-memories-daily': {
        'task': 'apps.memory.tasks.archive_l2_to_l3',
        'schedule': 86400.0,  # 每日凌晨
    },
    'heartbeat-broadcast': {
        'task': 'apps.agent.tasks.broadcast_heartbeat',
        'schedule': 30.0,  # 30秒
    },
}
```

---

## 七、环境变量清单

```bash
# .env.example

# Django
DJANGO_SECRET_KEY=change-me-in-production
DJANGO_SETTINGS_MODULE=core.settings.prod
ALLOWED_HOST=yourdomain.com

# PostgreSQL
POSTGRES_DB=trade_agent
POSTGRES_USER=trade_user
POSTGRES_PASSWORD=secure_password
POSTGRES_HOST=db
POSTGRES_PORT=5432

# Redis
REDIS_URL=redis://redis:6379

# OpenAI
OPENAI_API_KEY=sk-...
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
OPENAI_CHAT_MODEL=gpt-4o

# 交易所 API（Fernet加密后存DB，此处仅用于初始化）
EXCHANGE_FERNET_KEY=base64-fernet-key==

# Telegram Bot
TELEGRAM_BOT_TOKEN=123456:ABC-...
TELEGRAM_WEBHOOK_SECRET=random-secret-string

# MinIO
MINIO_ACCESS_KEY=minio_access
MINIO_SECRET_KEY=minio_secret
MINIO_ENDPOINT=minio:9000

# JWT
JWT_ACCESS_TOKEN_LIFETIME_MINUTES=60
JWT_REFRESH_TOKEN_LIFETIME_DAYS=7
```

---

*设计师A | infra.md v1.0 | 基础设施详细设计完成*
