"""业务时区口径 —— 用户口语时间（"每天10点"）统一按北京时间解释。

背景（2026-09-20 实测）：
  - `TIME_ZONE` / `CELERY_TIMEZONE` 都是 UTC，且 Django 在加载 settings 时会把
    进程的 `TZ` 环境变量改成 `TIME_ZONE` 并调用 `time.tzset()`
    （见 `django/conf/__init__.py`）。所以**在 Django 进程内**
    `datetime.now()` 拿到的是 UTC，即使容器镜像的 `TZ=Asia/Shanghai`。
  - 结果是：用户说「每天10点」，LLM 生成 `0 10 * * *`，落到 `CrontabSchedule`
    的 `timezone` 是 UTC → 实际在北京时间 18:00 触发；提交确认里也只有
    `cron=0 10 * * *`，没有任何时区标注。

这里只统一「用户口语时间 → 调度时间」这一段口径：不带时区的时间一律按业务时区
（默认 `Asia/Shanghai`）解释，并把它标注给用户和 LLM 看。

**不要**改全局 `TIME_ZONE` / `CELERY_TIMEZONE` 来达到同样目的：那会平移已有
DB 时间戳，并让 celery-beat 用新时区重新解析 `PeriodicTask.last_run_at`，
可能触发补跑风暴。
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from django.conf import settings

DEFAULT_BUSINESS_TIMEZONE = "Asia/Shanghai"


def business_tz() -> ZoneInfo:
    """业务时区（默认北京时间）。可用 settings.BUSINESS_TIMEZONE 覆盖。"""
    return ZoneInfo(getattr(settings, "BUSINESS_TIMEZONE", DEFAULT_BUSINESS_TIMEZONE))


def business_tz_name() -> str:
    """业务时区的 IANA 名称，如 'Asia/Shanghai'（可直接存进 CrontabSchedule.timezone）。"""
    return business_tz().key


def business_now() -> datetime:
    """当前业务时区时间（aware）。"""
    return datetime.now(business_tz())


def business_tz_label() -> str:
    """'Asia/Shanghai UTC+08:00'，给用户/LLM 看的时区标注。"""
    offset = business_now().strftime("%z")
    pretty = f"UTC{offset[:3]}:{offset[3:]}" if offset else ""
    return f"{business_tz_name()} {pretty}".strip()


def to_business(dt: datetime) -> datetime:
    """转换到业务时区；naive datetime 视为「已经是业务时区时间」。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=business_tz())
    return dt.astimezone(business_tz())


def format_business(dt: datetime | None) -> str:
    """按业务时区格式化并带上时区标注，用于展示给用户/LLM。"""
    if dt is None:
        return "未执行"
    return f"{to_business(dt):%Y-%m-%d %H:%M}（{business_tz_label()}）"
