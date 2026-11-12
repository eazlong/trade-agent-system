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

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from django.conf import settings

DEFAULT_BUSINESS_TIMEZONE = "Asia/Shanghai"

#: 业务日的日界（业务时区里的小时数）。默认 08:00 —— 它恰好是 BTC 日线换线的时刻
#: （UTC 00:00），于是「业务日 = UTC 开盘日 = 北京同一自然日」这条口径在判定链路里
#: 不需要任何跨日推断。
#:
#: **做成常量而不是散在各处写字面量**：`business_day()` 与 `business_day_start()` 是
#: 互为反函数的一对，两处各写一个 8 会在有人改动时静默错开，而错开的表现是「判定结论
#: 整体平移一天」——那种错位处处自洽，最难查。
BUSINESS_DAY_START_HOUR = 8


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


def business_day(moment: datetime) -> date:
    """这个绝对时刻落在哪个**业务日**。

    业务日 ≠ 业务时区的日历日，两者差着业务日的日界（默认北京 08:00）。北京时间
    00:30 是「业务时区的今天」，但它是**业务日的昨天**——那一轮心跳（UTC 16:30）
    还在为昨天署名。

    这个区别在本仓库里已经制造过一次系统性错误：判定链路用
    `to_business(now).date()` 推 `run_day`（日历日），又用「此刻已收盘的日线」推
    `attribute_date`（跟着 UTC 00:00 换线走，也就是北京 08:00），两者在北京 00:00–08:00
    这 8 小时里对不上，于是每条判定记录的「运行日 → 签署日」都慢了一天，而它**永不
    自愈**（记录不更新，`effective_at` 又是运行日的函数）。错位处处自洽，只是每一天的
    结论都比行情晚两天。

    `to_business()` 只做时区换算，**回答不了「业务日」这个问题**——它给的日历日与日界
    无关。所以两者必须分开存在，不要用前者顶替后者。

    实现就是「先换算到业务时区、再看它是不是已经过了日界」：日界之前属于前一天。
    """
    local = to_business(moment)
    return (local - _business_day_offset()).date()


def _business_day_offset() -> timedelta:
    """业务日的日界在业务时区里的偏移（默认 08:00 → 减去 8 小时即得业务日）。

    刻意从 `BUSINESS_DAY_START_HOUR` 派生而不是写死 8：日界挪动时，`business_day`
    与 `business_midnight` 必须一起动，而它们读同一个常量才不会漂开。
    """
    return timedelta(hours=BUSINESS_DAY_START_HOUR)


def business_day_start(day: date) -> datetime:
    """业务日 `day` 的日界对应的绝对时刻（默认北京 08:00 == UTC 00:00）。

    与 `business_day()` 是同一口径的两面：那个把时刻折成日期，这个把日期摊成时刻。
    两者必须互为反函数（日界当刻归属它自己那一天），这一点由测试钉住。

    放在这里而不是 `apps/regime/models.py`：判定 / Shadow / 日报三处都要用同一句话，
    而 `models.business_midnight` 是它的历史位置——那里只有判定链路能 import，
    Shadow 与日报各自复制一份就会出现第二套口径。
    """
    return datetime.combine(day, time(BUSINESS_DAY_START_HOUR), tzinfo=business_tz())


def as_utc(moment: datetime) -> datetime:
    """把 DB 读回来的时刻归一成 UTC。

    与上面几个函数同一个主题的另一半：那几个负责「UTC ↔ 业务时区」的**展示与调度**口径，
    这一个负责**存储侧**的归一——DB 里所有绝对时刻都是 UTC，而 naive 时间戳交给
    `astimezone()` 会按**进程 TZ** 解释，进程 TZ 又是 Django 加载 settings 时按
    `TIME_ZONE` 覆写的产物（见本模块 docstring 的实测记录）。于是「切片按哪个时刻归属」
    这类口径会挂在别人的副作用上，宁可在这里显式钉死。

    两个消费方（`apps/regime/slicing.py` 的切片适配层、#35 的
    `capital_efficiency_slicing.py`）原本各有一份**逐字相同**的实现。放进这里而不是让
    一方 import 另一方：两个 app 之间没有既有的依赖边，任选一个方向都会凭空造出一条，
    而 `apps/common/` 本来就是「谁都不许拥有它、谁都可以用」的那一层。

    naive → UTC 而不是抛出，是因为下游的纯模块已经在构造处**拒绝** naive
    （`OccupancyInterval.__post_init__` 的那条 `ValueError` 说得更清楚）。两层都要：
    这一层负责**归一**，那一层负责**拒绝**——只归一不拒绝会让一个本该报错的时间戳
    静默变成一个看起来正常的结论。
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)
