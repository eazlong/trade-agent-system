# AGENTS.md

## WHY (Purpose)
本系统是一个 AI Agent 驱动的量化交易辅助系统，旨在将自然语言交易意图转化为受控的 Django/Celery 执行逻辑，通过 Telegram Bot 与用户交互。

## WHAT (Stack & Map)

### 技术栈
- **Backend**: Django 5.x (REST API + ASGI WebSocket)
- **Task Queue**: Celery + Redis Stream (异步撮合与回测)
- **Database**: PostgreSQL + pgvector (交易指令、K线数据、向量记忆)
- **LLM**: OpenAI GPT-4o / Anthropic Claude (降级链)
- **Exchange**: ccxt (Binance/OKX/Bybit)

### 核心模块
- `apps/agent/` - SupervisorAgent + 子 Agent（分析、策略、风控、教练）
- `apps/skill/` - 技能注册中心（分析、策略、风控、教练技能）
- `apps/trading/` - 交易执行框架（懒加载）
- `apps/risk/` - 风控引擎
- `apps/backtest/` - 回测框架
- `apps/memory/` - 分层记忆（L1/L2/L3）
- `apps/exchange/` - 交易所连接

### 架构约束
- **依赖层级**: Types → Config → Repo → Service → API → UI（单向依赖）
- **懒加载**: 所有子 Agent 和交易框架首次调用时才实例化
- **注册中心模式**: AgentRegistry / SkillRegistry 使用类装饰器

## HOW (Commands & Standards)

### 环境初始化
```bash
cd backend
uv venv
uv pip install -r requirements.txt
cp .env.example .env
```

### 数据库
```bash
# 开发环境 (SQLite)
DJANGO_SETTINGS_MODULE=core.settings.dev uv run python manage.py migrate

# 生产环境 (PostgreSQL)
python manage.py migrate
```

### 运行服务
```bash
# Django ASGI
DJANGO_SETTINGS_MODULE=core.settings.dev uv run python manage.py runserver

# Celery Worker
DJANGO_SETTINGS_MODULE=core.settings.dev uv run celery -A celery_app worker -l info

# Celery Beat
DJANGO_SETTINGS_MODULE=core.settings.dev uv run celery -A celery_app beat -l info
```

### 测试（强制执行）
```bash
# 全量测试 + 覆盖率
DJANGO_SETTINGS_MODULE=core.settings.dev uv run pytest --cov=apps --cov-report=term-missing

# 单文件测试
DJANGO_SETTINGS_MODULE=core.settings.dev uv run pytest apps/agent/tests/test_xxx.py

# DST 仿真测试
SIMULATION_MODE=True DJANGO_SETTINGS_MODULE=core.settings.dev uv run pytest apps/trading/tests/dst/
```

### 代码质量
```bash
# Lint
ruff check backend/

# 格式化
ruff format backend/

# 类型检查
pyright backend/
```

## VERIFICATION PYRAMID (Pointers)

验证协议详见：
- **L1 语义比对**: [docs/SHADOW_EVAL.md](docs/SHADOW_EVAL.md)
- **L2 确定性仿真**: [docs/DST_TESTING.md](docs/DST_TESTING.md)
- **L3 形式化规范**: [docs/TLA_SPECS.md](docs/TLA_SPECS.md)

### 自动验证机制

验证技能通过以下方式自动运行：

#### 1. PostToolUse Hook（编辑后触发）
当编辑 `backend/apps/*.py` 文件时，自动运行：
- 格式化检查
- Lint 检查
- 验证技能

#### 2. Stop Hook（会话结束时触发）
会话结束时自动运行完整验证：
- 代码变更验证
- 不变量检查

#### 3. CI/CD（提交时强制执行）
GitHub Actions 自动运行验证金字塔各层检查。

#### 4. 手动触发
```bash
# 代码变更验证
python3 .agents/skills/code-change-verification/run.py

# DST 仿真测试
python3 .agents/skills/dst-testing/run.py --seeds 100

# 不变量检查
cd backend && python scripts/check_invariants.py
```

## DONE CRITERIA (硬性契约)

智能体完成任务必须满足：
1. ✅ 所有 pytest 测试通过
2. ✅ 覆盖率 ≥ 80%
3. ✅ Ruff Lint 无错误
4. ✅ 类型检查通过
5. ✅ 无 console.log / print 调试语句
6. ✅ 无硬编码密钥/凭证

## AGENT LOOP (自主修复循环)

```
思考 (Think) → 行动 (Act) → 观察 (Observe) → 验证 (Verify)
     ↑                                              ↓
     └──────────── 失败时自动重试 ──────────────────┘
```

验证失败时，系统自动捕获 Traceback 并反馈至上下文，智能体进入自主迭代修复。

## META-LOOP (人类监管)

人类工程师职责：
- ❌ 不审查每行 Diff
- ✅ 收紧验证不变量
- ✅ 扩大仿真覆盖范围
- ✅ 审查"垃圾回收"智能体提交的重构 PR

## NEW AGENT/SKILL GUIDE

### 新增 Skill
1. 创建 `apps/skill/skills/my_skill.py`，继承 `BaseSkill`
2. 用 `@SkillRegistry.register` 装饰
3. 在 `apps/skill/skills/__init__.py` 中 import

### 新增 Agent
1. 在 `apps/agent/sub_agents.py` 中继承 `_LLMAgent`
2. 用 `@AgentRegistry.register_class` 装饰
3. 在 `INTENT_TO_AGENT` 映射中添加路由规则