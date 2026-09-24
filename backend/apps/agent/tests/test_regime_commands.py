"""事件熔断开关的**斜杠命令**（第②段单元 ②f）。

`test_breaker_switch.py` 钉的是判定与渲染（`breaker_switch.py` 那一层）；这个文件钉的是
**命令被敲下来的那一刻**：谁被认成命令、参数有没有被丢掉、落款写的是谁、写了几行。两层
分开，因为坏掉的方式不同——判定层坏掉会算出错的数（那一层看得见），而这一层坏掉的表现
多半是**什么都没发生，或者悄悄多写了一行**：

1. **命令词之后的那一段不许被丢掉**（第②f 段加 `/regime` 的由来）。`/regime on` 一旦
   被当成裸 `/regime` 执行，屏幕上回来的确认页与「开关本来就是关的」长得一模一样：人
   以为打开了，什么都没变，而且日报里也一样正常。
2. **落款是人。** 命令路径写进流水的是 `chat` + 平台 sender id（与 `/event` 同一条）。
   这条流水是事后回答「当时它到底开没开、谁开的」的唯一材料，而开这个开关意味着机制
   会在没有人的时候自动对市场动手（②d）。
3. **认不出「谁敲的」是故障，不是手滑。** 走 `success=False`（`apps/agent/consumer.py`
   把它渲染成 `[错误] …`）；而「多给了一个词」是手滑，走 `success=True` 的普通回复。
4. **打字打错不写库。** 未知子命令、多余的词、认不出身份，三种情况都必须一行不落——
   多写一行 `RegimeMechanismSwitch` 就是一次没人审过的机制状态变更。
5. **重复按同一个按钮不动手。** 第二次 `/regime on` 不写第二行流水（`from_mode` 与
   `to_mode` 都是执行态的行没有任何信息，却会让「切过几次」永久多算一次）。
6. **两个方向回显的东西不一样。** 打开回显整页（它会让机制自动动手），关闭只回一句
   「关掉之后什么变了」——但**关闭那一刻正压在窗口里**必须被看见，否则外面完全看不出来。
7. **`kind` 只有一个值。** 这条命令只碰 `event_breaker`，另外两个开关的档位不受影响。

异步与数据库：入口 `handle_regime_command` 是 `async`，里面的 ORM 是 `sync_to_async`
挪到**别的线程**里跑的，那个连接必须看得见本用例造的行——而 `django.test.TestCase` 的
外层事务恰好挡住这件事。所以这里用**普通类 + `django_db(transaction=True)`**，与
`test_event_commands.py` 同一条约定（代价是每个用例约 70 秒）。
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone as django_timezone

from apps.agent.base import AgentMessage, AgentResult
from apps.agent.regime_commands import handle_regime_command
from apps.agent.supervisor import _split_command
from apps.regime import breaker_switch, halt
from apps.regime.models import (
    ActorKind,
    EventImpact,
    EventScope,
    EventStatus,
    HaltDeclaration,
    HaltTrigger,
    MajorEvent,
    MajorEventChange,
    MechanismKind,
    MechanismMode,
    RegimeMechanismSwitch,
)

SENDER = "tg:42"

#: 确认页正文第一行。两个入口都渲染这一页，用它当「整页回显过了」的锚。
PAGE_TITLE = "事件熔断 · 上线确认"


def _run(coro):
    return asyncio.run(coro)


def _msg(text: str, user_id: str = SENDER) -> AgentMessage:
    return AgentMessage(
        sender="channel",
        recipient="supervisor",
        user_id=user_id,
        payload={"text": text},
    )


async def _call(text: str, user_id: str = SENDER) -> AgentResult:
    """按生产路径调用：命令词切下来，剩下那一段交给 `handle_regime_command`。"""
    cmd, args = _split_command(text)
    assert cmd == "/regime", text
    return await handle_regime_command(_msg(text, user_id), args)


def _open_window(name: str = "正在开的窗口") -> MajorEvent:
    """造一条**此刻正压在窗口里**的高影响事件（跨过 `timezone.now()`）。"""
    now = django_timezone.now()
    return MajorEvent.objects.create(
        name=name,
        scope_kind=EventScope.MARKET.value,
        symbols=[],
        event_time=now,
        impact=EventImpact.HIGH.value,
        halt_at=now - timedelta(hours=2),
        resume_at=now + timedelta(hours=2),
        status=EventStatus.SCHEDULED.value,
        created_by="tester",
    )


def _only_row() -> RegimeMechanismSwitch:
    """流水的**唯一**一行；多出或少掉都是本文件要抓的错，所以用 `get()` 而不是 `first()`。"""
    return RegimeMechanismSwitch.objects.get()


def _latest_row() -> RegimeMechanismSwitch:
    """切换过不止一次时，最后落下的那一行（模型的 `ordering` 是 `-at, -id`）。"""
    return RegimeMechanismSwitch.objects.first()


@pytest.mark.django_db(transaction=True)
class TestTheCommandAgainstARealDatabase:
    # ---------------- 只读那一档 ---------------- #

    def test_a_bare_command_shows_the_page_and_writes_nothing(self):
        result = _run(_call("/regime"))
        assert result.success, result.error
        assert PAGE_TITLE in result.data
        # 只读：连一条流水都不写（第②f 段 Q7）。
        assert not RegimeMechanismSwitch.objects.exists()

    def test_the_page_is_read_only_even_when_a_window_is_open(self):
        # 页面里有「此刻正压在…」这句话，但它仍然不改变任何东西——这句话是最容易被
        # 顺手写成一次动作的地方。
        _open_window()
        result = _run(_call("/regime"))
        assert result.success, result.error
        assert "此刻正压在" in result.data
        assert not RegimeMechanismSwitch.objects.exists()

    # ---------------- 打字打错：普通回复，且不写库 ---------------- #

    def test_an_unknown_subcommand_is_feedback_not_a_failure(self):
        result = _run(_call("/regime 上线"))
        assert result.success, result.error
        assert "未知的子命令：上线" in result.data
        assert "/regime on" in result.data
        assert not RegimeMechanismSwitch.objects.exists()

    def test_an_extra_word_is_refused_and_named(self):
        # 绝不静默忽略多余的词：人以为写进去的东西没写进去，流水里就少了一条原因。
        result = _run(_call("/regime on --备注 因为要开会"))
        assert result.success, result.error
        assert "这条命令不吃参数" in result.data
        assert "--备注" in result.data
        assert not RegimeMechanismSwitch.objects.exists()

    def test_the_subcommand_is_matched_as_a_whole_word(self):
        # 别名只认整词，与 `/event` 同一条纪律：`onx` 不是 `on` 的笔误，是另一个词。
        result = _run(_call("/regime onx"))
        assert result.success, result.error
        assert "未知的子命令" in result.data
        assert not RegimeMechanismSwitch.objects.exists()

    def test_an_unknown_operator_is_a_failure_not_feedback(self):
        # 认不出「谁敲的」是管道出了问题，不是人打错了字——它该被当成故障看见。
        result = _run(_call("/regime on", user_id=""))
        assert not result.success
        assert "无法确定操作者身份" in result.error
        assert not RegimeMechanismSwitch.objects.exists()

    # ---------------- 打开 ---------------- #

    def test_opening_echoes_the_page_and_writes_one_row(self):
        result = _run(_call("/regime on"))
        assert result.success, result.error
        # 打开要回显整页：这一步会让机制在没人的时候自动对市场动手。
        assert PAGE_TITLE in result.data
        assert "事件熔断已打开" in result.data

        row = _only_row()
        assert row.kind == MechanismKind.EVENT_BREAKER.value
        assert row.from_mode == MechanismMode.SHADOW.value
        assert row.to_mode == MechanismMode.EXECUTING.value

    def test_the_actor_is_the_person_who_typed_it(self):
        _run(_call("/regime on"))
        row = _only_row()
        assert row.actor_kind == ActorKind.CHAT.value
        assert row.actor_name == SENDER

    def test_the_reason_is_the_page_s_own_summary(self):
        # 流水里的原因与屏幕上读到的数是**同一次快照**（`breaker_switch.page`）：各算
        # 一遍的话，两次查询之间有人录了一条事件，两边就会互相矛盾而没有任何东西会红。
        _open_window()
        _run(_call("/regime on"))
        reason = _only_row().reason
        assert reason.startswith("人工打开事件熔断")
        assert "窗口相交 1 条" in reason
        assert "库里高影响事件 1 条" in reason

    def test_the_reason_says_never_when_the_library_is_empty(self):
        # 写成「距今 0 天」会让空库与「今天刚录过」看起来一样。
        _run(_call("/regime on"))
        assert "从未录入过事件" in _only_row().reason

    def test_pressing_the_same_button_again_writes_nothing(self):
        _run(_call("/regime on"))
        result = _run(_call("/regime on"))
        assert result.success, result.error
        assert "本来就是执行态" in result.data
        assert RegimeMechanismSwitch.objects.count() == 1

    def test_the_aliases_for_opening_work(self):
        for word in ("开", "开启", "打开", "open", "ON"):
            RegimeMechanismSwitch.objects.all().delete()
            result = _run(_call(f"/regime {word}"))
            assert result.success, result.error
            assert _only_row().to_mode == MechanismMode.EXECUTING.value, word

    # ---------------- 关闭 ---------------- #

    def test_closing_does_not_re_echo_the_whole_page(self):
        _run(_call("/regime on"))
        result = _run(_call("/regime off"))
        assert result.success, result.error
        # 关闭是撤防：把整页再打一遍只会让人跳过它。但「关掉之后什么变了」要说。
        assert PAGE_TITLE not in result.data
        assert "事件熔断已关闭" in result.data

        row = _latest_row()
        assert row.from_mode == MechanismMode.EXECUTING.value
        assert row.to_mode == MechanismMode.SHADOW.value

    def test_the_closing_reason_answers_a_different_question(self):
        _run(_call("/regime on"))
        _run(_call("/regime off"))
        reason = _latest_row().reason
        assert reason.startswith("人工关闭事件熔断")
        assert "关闭时无窗口开启" in reason

    def test_the_closing_reason_names_the_window_that_gets_released(self):
        _open_window("FOMC 议息")
        _run(_call("/regime on"))
        _run(_call("/regime off"))
        assert "关闭时正压在「FOMC 议息」的窗口内" in _latest_row().reason

    def test_closing_inside_a_window_says_the_window_stops_blocking(self):
        # 这是最需要被看见的一种关法，而且外面看不出来：表里窗口还在、日报照旧，
        # 只是下单不再被拦。
        _open_window("FOMC 议息")
        _run(_call("/regime on"))
        result = _run(_call("/regime off"))
        assert result.success, result.error
        assert "⚠️" in result.data
        assert "FOMC 议息" in result.data
        assert "不再拦人" in result.data

    def test_closing_when_already_shadow_writes_nothing(self):
        result = _run(_call("/regime off"))
        assert result.success, result.error
        assert "本来就是 Shadow" in result.data
        assert not RegimeMechanismSwitch.objects.exists()

    def test_the_aliases_for_closing_work(self):
        for word in ("关", "关闭", "关掉", "close", "OFF"):
            RegimeMechanismSwitch.objects.all().delete()
            _run(_call("/regime on"))
            result = _run(_call(f"/regime {word}"))
            assert result.success, result.error
            assert RegimeMechanismSwitch.objects.count() == 2, word
            assert _latest_row().to_mode == MechanismMode.SHADOW.value, word

    # ---------------- 与别处的关系 ---------------- #

    def test_it_touches_only_the_event_breaker_switch(self):
        # 三个开关各有各的档；这条命令只碰一个。
        _run(_call("/regime on"))
        _run(_call("/regime off"))
        for kind in (MechanismKind.MECHANISM, MechanismKind.REGIME_GATE):
            assert RegimeMechanismSwitch.current(kind) is MechanismMode.SHADOW, kind
        assert not RegimeMechanismSwitch.objects.exclude(
            kind=MechanismKind.EVENT_BREAKER.value
        ).exists()

    def test_it_does_not_write_a_window(self):
        # 窗口同步是 `halt_sync` 的事；开关只决定「拦不拦」，不凭空多出窗口，也不抹掉
        # 已经写好的声明。开关一开就顺手补一条声明的话，这里会多出一行。
        window = _open_window()
        _run(_call("/regime on"))
        _run(_call("/regime off"))
        window.refresh_from_db()
        assert window.status == EventStatus.SCHEDULED.value
        assert not HaltDeclaration.objects.exists()
        assert not MajorEventChange.objects.exists()

    def test_the_flip_is_what_the_halt_side_reads(self):
        # 端到端：命令写下的那一行正是 `halt.switch_open` 读的那一行。中间任何一处
        # 名字对不上，这里就是 False。
        driver = HaltTrigger.EVENT
        assert halt.switch_open(driver) is False
        _run(_call("/regime on"))
        assert halt.switch_open(driver) is True
        _run(_call("/regime off"))
        assert halt.switch_open(driver) is False

    def test_the_read_only_path_agrees_with_the_module(self):
        # 裸 `/regime` 与 `breaker_switch.confirmation_body` 必须是同一段文字：两处各
        # 渲染一遍，就会有两份可以互相矛盾的确认页。时钟钉住，否则两次渲染之间跨过
        # 一分钟就会看到两个不同的「截至」时刻。
        at = django_timezone.now()
        with patch("apps.agent.regime_commands.timezone.now", return_value=at):
            text = _run(_call("/regime")).data
        assert text == breaker_switch.confirmation_body(now=at)
