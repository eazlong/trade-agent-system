"""人工恢复豁免的写方（第①段单元 7 收尾，Q7）。

CONTEXT.md 第 120 条：恢复产生的是一条**有时效的人工豁免，不是一次性的开关动作**。
`DeactivationExemption` 是那张表，`deactivation_run.in_force_exemptions` 是它的读者——
本命令是它**唯一**的写方。

## 为什么写方必须是命令，而不是自动路径

豁免的全部意义是「人在知道结论的前提下，决定这一次不停」。任何自动写方（判定失败时
自动豁免、重算时自动补豁免）都会把这个开关变成机制给自己放行——那正是「Agent 对
halt/gate 无写权限」那条纪律要挡的东西。所以写豁免的入口只有人工的两条：本命令、
以及将来的 slash 命令（第②段）。两者最终都落到同一条 `INSERT`。

## 三个动作：列出 / 发出 / 收回

- **列出**（默认）——给出每条豁免现在处于哪个状态。四种状态是**互斥**的，判据与
  `in_force_exemptions` 的「三条一起」逐字相同：`closed_at` 空 **且** `granted_at <= now`
  **且** `expires_at > now` 才叫「在期」。这里不另写一套判据：两处各判一次，迟早会漂，
  而漂的表现是「命令说在期、推导说不在期」——最难查的一类不一致。
- **发出**（`--grant`）——`expires_at` 在**写入那一刻**按 `config.DEACTIVATION.exemption_days`
  换算成绝对时刻存下（模型 docstring 那条：改配置不追溯已经发出的豁免）。
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
from datetime import datetime, timedelta
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.regime import config
from apps.regime.deactivation_run import (
    CLOSE_REASON_DISPLAY,
    CLOSE_REASON_MANUAL,
    current_regime_state,
)
from apps.regime.models import DeactivationExemption
from apps.regime.quant import BaseRegime
from apps.trading.models import Strategy

#: 豁免在展示层的四种状态（互斥，判据见模块 docstring）。
STATE_IN_FORCE = "in_force"
STATE_PENDING = "pending"
STATE_EXPIRED = "expired"
STATE_CLOSED = "closed"

STATE_DISPLAY = {
    STATE_IN_FORCE: "在期",
    STATE_PENDING: "未生效",
    STATE_EXPIRED: "已过期",
    STATE_CLOSED: "已关闭",
}


def state_of(exemption: DeactivationExemption, *, now: datetime) -> str:
    """一条豁免现在处于哪个状态。**收到豁免行本身**，不收四个字段。

    收行是为了让判据只有一处：谁想知道状态都调这个函数，而不是各自 `filter(...)` 一遍。
    """
    if exemption.closed_at is not None:
        return STATE_CLOSED
    if exemption.granted_at > now:
        return STATE_PENDING
    if exemption.expires_at <= now:
        return STATE_EXPIRED
    return STATE_IN_FORCE


def regime_display(value: str) -> str:
    """阶段取值 → 中文。**认不出来就原样返回**，不回落成某一档。

    模型上的 `choices` 只在表单校验里管用，手工写进去的行绕得过它。一条脏行不该让整个
    清单命令炸掉；但它也绝不能被显示成「下行趋势」——那会让人以为豁免覆盖的是另一档。
    """
    try:
        return BaseRegime(value).display
    except ValueError:
        return f"{value}（认不出的阶段）"


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
        regime = options["regime"] or self._current_regime()
        days = config.DEACTIVATION.exemption_days
        actor = options["actor"] or getpass.getuser()

        # 同一个（策略 × 阶段）上**允许**有多条历史豁免（表上刻意没有唯一约束），
        # 因为在期的那条被关闭之后还要留痕。但在期时再发一条是「延长/覆盖」的语义，
        # 而 `in_force_exemptions` 取的是**最近发出的**那条——所以必须说出来，
        # 否则人会以为两条都在生效、豁免期被叠加了。
        superseded = [
            row
            for row in self._rows_for(strategy, regime)
            if state_of(row, now=now) == STATE_IN_FORCE
        ]

        exemption = DeactivationExemption.objects.create(
            strategy=strategy,
            regime=regime,
            granted_at=now,
            # 生效期在这里、也**只在这里**换算成绝对时刻（`config` 的 docstring：
            # 改配置不追溯已经发出的豁免）。
            expires_at=now + timedelta(days=days),
            granted_by=actor,
            note=options["note"],
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"已发出豁免 #{exemption.id}：{strategy.name} × "
                f"{regime_display(regime)}，至 {exemption.expires_at:%Y-%m-%d %H:%M} "
                f"（{days} 个自然日），发出人 {actor}"
            )
        )
        for row in superseded:
            self.stdout.write(
                f"  注意：同一格原有在期豁免 #{row.id}（至 {row.expires_at:%Y-%m-%d}），"
                "推导只认最近发出的那条，它仍在表里留痕但已不再生效"
            )

    def _revoke(self, ids, now: datetime) -> None:
        """按 id 收回。条件更新 + 先到者为准，与 `close_left_regime_exemptions` 同形。"""
        wanted = [self._parse_exemption_id(raw) for raw in ids]
        found = set(
            DeactivationExemption.objects.filter(id__in=wanted).values_list(
                "id", flat=True
            )
        )
        missing = [str(i) for i in wanted if i not in found]
        if missing:
            raise CommandError(
                f"这些豁免 id 不存在：{'、'.join(missing)}。"
                "不带参数跑一次可以看到现有清单里的 id。"
            )

        # 还没关的才关：已经关掉的（阶段离开 / 早先撤过）保持原样，连原因为都不覆盖。
        closed = DeactivationExemption.objects.filter(
            id__in=wanted, closed_at__isnull=True
        ).update(closed_at=now, closed_reason=CLOSE_REASON_MANUAL)

        already = len(wanted) - closed
        self.stdout.write(
            self.style.SUCCESS(f"已收回 {closed} 条豁免（manual_revoke）")
        )
        if already:
            self.stdout.write(
                f"  另外 {already} 条本来就已关闭，保持原样"
                "（先到者为准，不覆盖先手的 closed_reason）"
            )

    # -- 列出 -------------------------------------------------------------- #

    def _list(self, now: datetime) -> None:
        rows = list(
            DeactivationExemption.objects.select_related("strategy").order_by(
                "-granted_at", "id"
            )
        )
        if not rows:
            self.stdout.write("没有任何豁免记录")
            return

        counts: dict[str, int] = {state: 0 for state in STATE_DISPLAY}
        self.stdout.write(f"豁免共 {len(rows)} 条：")
        for row in rows:
            state = state_of(row, now=now)
            counts[state] += 1
            self.stdout.write(
                f"  #{row.id} {row.strategy.name} × {regime_display(row.regime)}"
                f"  [{STATE_DISPLAY[state]}]"
                f"  {row.granted_at:%Y-%m-%d} → {row.expires_at:%Y-%m-%d}"
                f"  由 {row.granted_by}"
                + (f"  关闭原因：{CLOSE_REASON_DISPLAY.get(row.closed_reason, row.closed_reason)}"
                   if state == STATE_CLOSED else "")
                + (f"  备注：{row.note}" if row.note else "")
            )
        self.stdout.write(
            "汇总："
            + "，".join(f"{STATE_DISPLAY[s]} {counts[s]}" for s in STATE_DISPLAY)
        )

    # -- 取数 -------------------------------------------------------------- #

    def _rows_for(self, strategy, regime: str):
        """同一格已有的豁免。`ordering` 在模型的 `Meta` 上，这里不重排。"""
        return DeactivationExemption.objects.filter(
            strategy=strategy, regime=regime
        ).order_by("-granted_at", "id")

    def _resolve_strategy(self, options) -> Strategy:
        raw_id = options["strategy_id"].strip()
        name = options["strategy"].strip()
        if not raw_id and not name:
            raise CommandError("--grant 需要 --strategy 或 --strategy-id 指明给谁发豁免")
        if raw_id and name:
            raise CommandError("--strategy 与 --strategy-id 只能给一个")

        if raw_id:
            strategy = Strategy.objects.filter(id=self._parse_uuid(raw_id)).first()
            if strategy is None:
                raise CommandError(f"找不到策略 id {raw_id}")
            return strategy

        matches = list(Strategy.objects.filter(name=name))
        if not matches:
            raise CommandError(f"找不到策略名 {name!r}（精确匹配 `Strategy.name`）")
        if len(matches) > 1:
            raise CommandError(
                f"策略名 {name!r} 对应 {len(matches)} 行，请用 --strategy-id 指明其中一个："
                + "、".join(str(row.id) for row in matches)
            )
        return matches[0]

    def _current_regime(self) -> str:
        """当前生效阶段。取不到就要求人显式给 `--regime`。

        **不兜底成某个默认阶段**：豁免有一个 10 天的实际效力，拿一个猜出来的阶段落库
        是「替人做决定」里最难发现的那种。冷启动时人本来就该自己说是哪个阶段。
        """
        state = current_regime_state()
        if state.regime is None:
            raise CommandError(
                "当前没有生效中的阶段判定（冷启动），无法推断 --regime，请显式给出 "
                f"（可选：{'、'.join(m.value for m in BaseRegime)}）"
            )
        self.stdout.write(
            f"未给 --regime，取当前生效阶段：{regime_display(state.regime)}"
            f"（自 {state.effective_at:%Y-%m-%d %H:%M} 生效）"
        )
        return state.regime

    @staticmethod
    def _parse_exemption_id(raw: str) -> int:
        """豁免 id 是自增整数（策略主键才是 UUID），但让错误在参数解析这一层就响，
        而不是等一个 `ValueError` 从 ORM 里冒出来。"""
        text = str(raw).strip()
        try:
            return int(text)
        except ValueError:
            raise CommandError(f"豁免 id 必须是整数，收到 {text!r}")

    @staticmethod
    def _parse_uuid(raw: str) -> UUID:
        """策略主键的解析。**不能复用上面那个**：拿一个 UUID 去 `int()` 会把
        「格式不对」报成「豁免 id 必须是整数」，而这是给人看的命令行。

        也不把解析交给 ORM：`Strategy.objects.filter(id="x")` 抛的是 `ValidationError`，
        在管理命令里它会显示成一段堆栈，而不是一句能照做的提示。
        """
        text = str(raw).strip()
        try:
            return UUID(text)
        except ValueError:
            raise CommandError(f"策略 id 必须是 UUID，收到 {text!r}")
