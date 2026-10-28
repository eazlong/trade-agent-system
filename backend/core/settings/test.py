"""Test settings — uses in-memory SQLite for fast, isolated tests."""

from .base import *  # noqa: F401, F403
import tempfile
import os

# Use a temp file for SQLite so multiple connections (from sync_to_async) can share data
_temp_db = os.path.join(tempfile.gettempdir(), f"tradeclaw_test_{os.getpid()}.db")
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": _temp_db,
        "TEST": {
            "SERIALIZE": False,
            "DEPENDENCIES": [],
        },
        # Allow multiple threads to access the same connection
        "OPTIONS": {
            "timeout": 20,
        },
    }
}

# Enable thread-sharing for SQLite
import sqlite3

sqlite3.threadsafety = 1  # Serialized mode

# Speed up tests
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# 测试环境的日志：**只求「不添乱」，绝不关别人的 logger**（#56）。
#
# 这里曾经是 `disable_existing_loggers: True`，后果是 31 条 `caplog` 用例在**全量跑**
# 时红、单目录跑却全绿：
#
# `dictConfig` 带这个标记时会把「配置那一刻已经存在」的每个 logger 的 `.disabled`
# 置为 True。而「哪些模块已导入」取决于 pytest 先收集/运行了哪个文件——`apps/agent/`
# 那批先把 `apps.riskguard.guard`、`apps.regime.slicing` 一类导进来，等本模块的
# LOGGING 生效时它们已被登记，于是全被禁用，`caplog` 从此抓不到任何一条。
#
# 为什么必须关掉（而不是逐模块解禁）：受影响的有 10 个模块、31 条用例，逐个枚举只会
# 在下次新增模块时再漏一批；而这条 bug 的形态正是「漏了也不报」——单目录跑永远绿。
#
# 不配 handler 是刻意的：日志经 `propagate` 走到 root，由 pytest 的 `caplog` 接管，
# 不会往 stdout 喷。要调终端输出请用 pytest 的 `--log-level` / `log_cli`，那是另一回事
# （它们管展示，不管 `disabled` 标记）。
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
}

REDIS_URL = "redis://localhost:6379/0"
TELEGRAM_BOT_TOKEN = "test-token"
