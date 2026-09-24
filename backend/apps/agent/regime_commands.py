"""事件熔断开关的斜杠命令（第②段单元 ②f）。

CONTEXT.md 第 160 条：**出 Shadow 切到执行态必须人工确认，不自动切换**。本模块是那个
「人工」的入口，`SupervisorAgent.handle()` 在最前面把命令截下来交给这里——**不经过
LLM**。这与 `/event` 同一条理由：判据认的是人敲的，而只要分发发生在意图解析之前，
「谁敲的」就还是人（`ActorKind.CHAT` + 平台 sender id）。

## 语法

    /regime            上线确认页（只读，不改变任何东西）
    /regime on         打开事件熔断（先回显确认页，再落一条切换流水）
    /regime off        关掉事件熔断（回到 Shadow）

`on` / `off` 也认「开 / 开启 / 打开 / open」与「关 / 关闭 / 关掉 / close」。

**打开与关闭都要过确认页**（第169 条），但**只有打开会回显整页**：这一步会让机制在没有
人的时候自动对市场动手（②d 的减仓执行器），所以「会不会拦住东西」必须当场看见。关闭是
撤防，回显的是「关掉之后什么变了、什么没变」——把整页再打一遍只会让人跳过它。

## 与 `/event` 的分工

`/event` 管**事件本身**（录入、改档、改期、取消），`/regime` 管**开关**。两者不共用
任何一行写路径：一条 `/event cancel` 不会顺手关掉开关，一次 `/regime off` 也不会抹掉
事件表里的任何一行（第②f 段 Q3 —— 窗口同步是 `halt_sync` 的事，它「写与开关无关」）。

## 报错一律是用户反馈

本模块**没有**自己的输入错误异常类，与 `event_commands.EventInputError` 不同：这里唯一
的输入错误是「多给了一个词」，它在解析那一步就知道，直接当回复返回（`success=True`，
打错一个字与系统坏掉在用户眼里不该长得一样）。写路径内部抛出的 `ValueError` 是程序 bug
（`reason` 由本模块自己生成，不可能为空），照旧往上抛给任务健康检查。

## 这一版不做的事

* **不收自由文本备注。** 切换原因就是确认页的摘要（第②f 段 Q8），摘要里已经有「打开
  时窗口相交几条、库里多少条、最近入库多久」。多一个 `--备注` 就是给「为什么开」造
  第二个说法，而两个说法分家时读的人分不出该信哪个；等有人能说出它比摘要多提供了什么
  再加。
* **不改天数、不改档位、不碰另外两个开关**（第183 条：`confirm_horizon_days` 不给
  Agent 传参；`MECHANISM` 与 `REGIME_GATE` 各自由第③段的入口管）。
* **不自动开关。** 自熔断退回 Shadow 之后再回执行态，必须由人重新敲一次（第161 条）。
"""

from __future__ import annotations

import logging
from datetime import datetime

from asgiref.sync import sync_to_async
from django.db import close_old_connections
from django.utils import timezone

from apps.regime import breaker_switch, events
from apps.regime.models import ActorKind, MechanismMode

from .base import AgentMessage, AgentResult

logger = logging.getLogger(__name__)

_USAGE = (
    "事件熔断开关：\n"
    "  /regime        上线确认页（只读，不改变任何东西）\n"
    "  /regime on     打开事件熔断（先回显确认页，再落一条切换流水）\n"
    "  /regime off    关掉事件熔断（回到 Shadow）"
)

#: 子命令别名。只认整词，不做前缀匹配——「/regime onx」不是「on」的笔误而是另一个词。
_ALIASES = {
    "on": "on",
    "开": "on",
    "开启": "on",
    "打开": "on",
    "open": "on",
    "off": "off",
    "关": "off",
    "关闭": "off",
    "关掉": "off",
    "close": "off",
}


def _actor(message: AgentMessage) -> str:
    return (message.user_id or "").strip()


# --------------------------------------------------------------------------- #
# 子命令。签名统一是同步的 `(actor, now) -> str`：同步跑，入口用 `sync_to_async` 把它
# 挪到线程里，ORM 因此永远不在 async 上下文里执行（CLAUDE.md 的硬规则）。
# --------------------------------------------------------------------------- #


def _status(actor: str, now: datetime) -> str:
    """裸 `/regime`：确认页本身。**只读**——连一条流水都不写。"""
    return breaker_switch.confirmation_body(now=now)


def _on(actor: str, now: datetime) -> str:
    """打开事件熔断。**确认页与流水取自同一次快照**（`breaker_switch.page`）。"""
    briefing = breaker_switch.page(now=now)
    body = briefing.body
    row = breaker_switch.flip_event_breaker(
        MechanismMode.EXECUTING,
        actor_kind=ActorKind.CHAT,
        actor_name=actor,
        reason=briefing.summary,
        now=now,
    )
    if row is None:
        # 已经是执行态：不写第二条流水（第②f 段 Q7）。仍把整页回显出来——人敲 on 的
        # 时候想知道的是「现在拦不拦得住」，而不是「有没有新写一行」。
        return body + "\n\n事件熔断**本来就是执行态**，没有写第二条流水。"

    tail = (
        f"✅ 事件熔断已打开：{MechanismMode(row.from_mode).display} → "
        f"{MechanismMode(row.to_mode).display}（{events.format_moment(row.at)}）\n"
        "停止声明表**未改动**：打开开关不会凭空多出窗口，它只决定已有的窗口拦不拦人。"
    )
    return body + "\n\n" + tail


def _off(actor: str, now: datetime) -> str:
    """关掉事件熔断。**撤防也要留痕，但不重复整页。**"""
    data = breaker_switch.confirmation(now=now)
    row = breaker_switch.flip_event_breaker(
        MechanismMode.SHADOW,
        actor_kind=ActorKind.CHAT,
        actor_name=actor,
        reason=breaker_switch.closing_summary(data),
        now=now,
    )
    if row is None:
        return (
            "事件熔断**本来就是 Shadow**（只记录、不真拦），没有写第二条流水。\n"
            "要看当前状态：/regime"
        )

    lines = [
        f"✅ 事件熔断已关闭：{MechanismMode(row.from_mode).display} → "
        f"{MechanismMode(row.to_mode).display}（{events.format_moment(row.at)}）",
        "停止声明表**未改动**：窗口仍在表里，只是不再拦住下单；保命档（高波动）不受影响。",
    ]
    if data.open_now is not None:
        # 关掉的那一刻正压在窗口里：这是最需要被看见的一种关法，而且外面看不出来。
        lines.append(
            f"⚠️ 此刻正压在「{data.open_now.name}」的熔断窗口里"
            f"（{events.format_moment(data.open_now.halt_at)} → "
            f"{events.format_moment(data.open_now.resume_at)}）："
            "关掉之后这个窗口**不再拦人**。"
        )
    return "\n".join(lines)


_SUBCOMMANDS = {"on": _on, "off": _off}


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #


async def handle_regime_command(message: AgentMessage, args: str) -> AgentResult:
    """`/regime …` 的入口。`args` 是命令词之后的那一段（可能为空）。"""
    actor = _actor(message)
    if not actor:
        return AgentResult(
            task_id=message.task_id,
            success=False,
            error="无法确定操作者身份，开关切换必须记下是谁操作的（聊天渠道的 sender id）",
        )

    tokens = args.split()
    if not tokens:
        handler = _status
    else:
        word = tokens[0].lower()
        name = _ALIASES.get(word)
        if name is None:
            return AgentResult(
                task_id=message.task_id,
                success=True,
                data=f"未知的子命令：{tokens[0]}\n\n{_USAGE}",
            )
        if len(tokens) > 1:
            # 绝不静默忽略多余的词（与 `/event` 同一条纪律）。开关切换尤其不能容忍：
            # 「我明明写了 --备注 X」而它被丢掉，流水里就少了一条人以为写进去了的原因。
            return AgentResult(
                task_id=message.task_id,
                success=True,
                data=f"这条命令不吃参数，多出来的词：{' '.join(tokens[1:])}\n\n{_USAGE}",
            )
        handler = _SUBCOMMANDS[name]

    now = timezone.now()

    def _work() -> str:
        close_old_connections()
        return handler(actor, now)

    text = await sync_to_async(_work)()
    logger.info("[regime] %s by %s", tokens[0] if tokens else "(页)", actor)
    return AgentResult(task_id=message.task_id, success=True, data=text)
