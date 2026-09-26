"""人工恢复豁免的管理命令（第①段单元 7 收尾，Q7）。

CONTEXT.md 第 120 条：恢复产生的是一条**有时效的人工豁免，不是一次性的开关动作**。
`DeactivationExemption` 是那张表，`deactivation_run.in_force_exemptions` 是它的读者。

## 本模块是一层薄壳

**状态判据、写路径与展示口径全在 `apps/regime/deactivation_run.py` 的「人工豁免」那一节**
（第③段 Q3：第③段给豁免开了第二个入口 `/regime exempt`，两个入口要做的判断逐条相同）。
本模块只剩三件只有命令行才有的东西：argparse 的旗标、`CommandError` 的旗标提示、stdout。

所以这里**从 `deactivation_run` import 并原样再用**：`state_of` / `STATE_*` /
`regime_display` 在本模块仍然解析得到（`manage.state_of(...)` 是既有调用方与测试的入口），
但它们只是名字，定义在共享层。共享层抛 `ExemptionError`（**只说事实**，因为它也服务
Telegram），这里补上「命令行该怎么改」再翻成 `CommandError`。

## 为什么写方必须是命令，而不是自动路径

豁免的全部意义是「人在知道结论的前提下，决定这一次不停」。任何自动写方（判定失败时
自动豁免、重算时自动补豁免）都会把这个开关变成机制给自己放行——那正是「Agent 对
halt/gate 无写权限」那条纪律要挡的东西。所以写豁免的入口只有人工的两条：本命令、
以及 `/regime exempt`（第③段）。两者最终都落到同一条 `INSERT`。

## 三个动作：列出 / 发出 / 收回

- **列出**（默认）——给出每条豁免现在处于哪个状态，并先给一句「此刻算不算数」
  （`exemption_standing`：保命档在拦时，在期豁免一条都不生效）。四种状态是**互斥**的，
  判据与 `in_force_exemptions` 的「三条一起」逐字相同（`state_of` 只有一处定义）。
- **发出**（`--grant`）——`expires_at` 在**写入那一刻**按 `config.DEACTIVATION.exemption_days`
  换算成绝对时刻存下（模型 docstring 那条：改配置不追溯已经发出的豁免）。不给 `--regime`
  时取当前生效阶段；**冷启动与保命档期间都拒绝猜**（Q4），只有显式点名才照落。
- **收回**（`--revoke`）——`closed_at` 写下去，`closed_reason=manual_revoke`。收回是
  **可逆**的（再发一条即可），所以与幽灵清理不同，这里**不需要 `--dry-run`**：
  两个动作都看得见、都能改回去，再设一道确认闸只是形式。

## 收回为什么必须存在

没有它，一条发错的豁免在 10 天内**没有出口**——它会让某条策略在某个阶段里持续跳过自动
停用，而这是本机制唯一一个「让人绕过机制」的开关。`close_left_regime_exemptions` 的
注释里那句「可能有别人（管理命令）关掉了同一条」写的就是这条路径，本命令把它补上。
两处都用 `closed_at__isnull=True` 做条件更新，所以**先到者为准**，后手不覆盖前手的
`closed_reason`（人撤的 / 阶段离开的，分得清）。

## 不动决策表

本命令只写 `DeactivationExemption`，绝不碰 `DeactivationDecision`。决策的唯一写方是
`deactivation_run.run_deactivation`（它冻的是「那一刻的依据」）；手工改决策等于伪造一份
不存在过的依据。收回豁免之后正确的收场是**等下一轮推导**——它会照常把那条策略判成
`target`，而决策行本来就在（`last_confirmed_at` 往前走）。
"""

from __future__ import annotations

import getpass
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

# 定义在 `deactivation_run`（唯一一份）的**状态判据与展示口径**，这里只是把它们转出到
# 本模块：`manage.state_of(...)` / `manage.STATE_DISPLAY` 这类既有写法必须继续解析得到
# （它们是第①段以来的入口）。下面那个 import 才是本模块自己要调的东西。
from apps.regime.deactivation_run import (  # noqa: F401
    STATE_CLOSED,
    STATE_DISPLAY,
    STATE_EXPIRED,
    STATE_IN_FORCE,
    STATE_PENDING,
    state_of,
)

from apps.regime.deactivation_run import (
    ExemptionError,
    blanket_grant_warning,
    find_strategy,
    grant,
    grant_summary,
    regime_display,
    resolve_regime,
    revoke,
    revoke_summary,
    roster_report,
    supersede_warnings,
)
from apps.regime.quant import BaseRegime
from apps.trading.models import Strategy


class Command(BaseCommand):
    help = (
        "人工恢复豁免的写方：列出（默认）/ 发出（--grant）/ 收回（--revoke）。"
        "豁免让（策略 × 阶段）在生效期内跳过自动停用"
    )

    def add_arguments(self, parser):
        action = parser.add_mutually_exclusive_group()
        action.add_argument(
            "--grant",
            action="store_true",
            help="发出一条豁免（需要 --strategy；--regime 不给则取当前生效阶段）",
        )
        action.add_argument(
            "--revoke",
            action="append",
            default=None,
            metavar="ID",
            help="按 id 收回，可重复。收回是可逆的（再发一条即可）",
        )
        parser.add_argument(
            "--strategy",
            default="",
            metavar="NAME",
            help="策略名（`Strategy.name` 精确匹配）。同名多条时请改用 --strategy-id",
        )
        parser.add_argument(
            "--strategy-id",
            default="",
            metavar="UUID",
            help="策略主键。与 --strategy 二选一",
        )
        parser.add_argument(
            "--regime",
            default="",
            choices=["", *(m.value for m in BaseRegime)],
            help="阶段。--grant 时不给则取**当前生效阶段**，并把取到的值打出来",
        )
        parser.add_argument(
            "--note",
            default="",
            help="备注（为什么恢复它）",
        )
        parser.add_argument(
            "--actor",
            default="",
            metavar="NAME",
            help="记进豁免的发出人（`granted_by`），默认取当前系统用户",
        )

    def handle(self, *args, **options):
        now = timezone.now()

        if options["grant"]:
            self._grant(options, now)
            # 发完立刻把这份清单打出来——发出人要在同一屏里看见「现在在期的是什么」，
            # 否则他下一步要自己去猜一条 id。
            self.stdout.write("")
        elif options["revoke"]:
            self._revoke(options["revoke"], now)
            self.stdout.write("")

        self._list(now)

    # -- 动作 -------------------------------------------------------------- #

    def _grant(self, options, now: datetime) -> None:
        strategy = self._resolve_strategy(options)
        choice = self._resolve_regime(options["regime"], now)
        if choice.taken_from_current:
            self.stdout.write(
                f"未给 --regime，取当前生效阶段：{regime_display(choice.regime)}"
                f"（自 {choice.effective_at:%Y-%m-%d %H:%M} 生效）"
            )

        outcome = grant(
            strategy=strategy,
            regime=choice.regime,
            actor=options["actor"] or getpass.getuser(),
            note=options["note"],
            now=now,
        )
        self.stdout.write(self.style.SUCCESS(grant_summary(outcome)))
        # 同一格原有在期豁免被盖住 / 显式点名了保命档：两句话都不是「顺带的提示」，
        # 而是这次写入的实际后果，必须在同一屏里说清。
        for line in supersede_warnings(outcome.superseded):
            self.stdout.write(line)
        warning = blanket_grant_warning(outcome.exemption)
        if warning:
            self.stdout.write(warning)

    def _revoke(self, raw_ids, now: datetime) -> None:
        # 收回与发出的方向相反，两者都说「怎么改」反而是噪音：id 不存在 / 不是整数这两句
        # 共享层的措辞已经能照着改。
        outcome = self._shared(revoke, raw_ids, now=now)
        lines = revoke_summary(outcome)
        self.stdout.write(self.style.SUCCESS(lines[0]))
        for line in lines[1:]:
            self.stdout.write(line)

    # -- 列出 -------------------------------------------------------------- #

    def _list(self, now: datetime) -> None:
        for line in roster_report(now=now):
            self.stdout.write(line)

    # -- 取数 -------------------------------------------------------------- #

    def _resolve_strategy(self, options) -> Strategy:
        return self._shared(
            find_strategy,
            name=options["strategy"],
            strategy_id=options["strategy_id"],
            hint="（--strategy 与 --strategy-id 恰好给一个）",
        )

    def _resolve_regime(self, raw: str, now: datetime):
        """冷启动 / 保命档的拒绝来自共享层，**旗标提示由这里补**：共享层同时服务 Telegram，
        它不该知道「--regime」这个名字。

        `hint` 里**不重复枚举可选阶段**：共享层那三句话的末尾已经带着 `regime_options_text()`
        （两个入口共用那一份），这里再抄一遍就是给「阶段有哪几档」造第二处答案。
        """
        return self._shared(
            resolve_regime,
            raw,
            now=now,
            hint="（--grant 用 --regime 显式点名阶段）",
        )

    def _shared(self, fn, *args, hint: str = "", **kwargs):
        """跑一次共享层，把 `ExemptionError`（只说事实）翻成 `CommandError`。

        `hint` 是旗标措辞，**只在这里补**：共享层的措辞对两个入口都成立，这一句只对命令行
        成立。`from exc` 保留原始异常链——调试时要能看出它是从判据还是从 ORM 出来的。
        """
        try:
            return fn(*args, **kwargs)
        except ExemptionError as exc:
            raise CommandError(f"{exc}{hint}") from exc
