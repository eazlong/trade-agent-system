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

## 第二级：行情阶段 gate（第③段单元 ③b）

`/regime gate [on|off]` 是同一个入口下的第二组子命令（`/regime 阶段 …` 等价）。上面七条
对它的**打开**逐条成立（不许丢词、落款是人、打错不写库、重复按不动手、只碰自己那一档），
另有三条是它自己的：

8. **回显口径刻意与事件熔断相反。** 事件熔断的**关闭**不回显整页（撤防不必重打一遍），
   而 gate 的**关闭回显整页**——那一页里有「关掉就安全了」在保命档（高波动）上是错的这句
   话（那一层没有开关可翻），而它是撤防方向上唯一拦得住这个要命误读的东西。同一时刻 CLI
   入口 `manage.py regime_gate` 两个方向都印整页：聊天里少印一半，就是同一个动作两个说法。
9. **两个方向都要说「表没对上」。** `gate_switch.reconcile_warning` 说的那件事与 CLI 入口
   是同一句（阶段说不清时 `gate.derive` 的 blocked 分支排在 Shadow 分支之前，本轮连对账
   都不发起，活行一条都没被解除）。**包括「本来就是这一档」那条路**——那时屏幕上唯一的
   成功信号是「对了一次账」，而这一轮恰好可能什么都没对；不说出来，读的人会把「本来就
   是 Shadow」当成「表已经干净了」。
10. **打开的那一刻说不出「该停哪些」也必须说出来。** 页面与尾部都要报 `skipped` 的后果，
    且此时一条声明都不许凭空写出来（那是 `gate_run` 的事，不是开关的事）。

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
from apps.regime import breaker_switch, gate_switch, halt
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

#: 行情阶段 gate 那一页的两个方向的第一行。取模块自己的常量而不是抄字面量：这两句换词时
#: 用例该**跟着换锚**，而不是变成一条「页面文案改了」的红灯。
GATE_PAGE_TITLE = gate_switch._OPENING
GATE_CLOSING_TITLE = gate_switch._CLOSING

#: 「档位翻了，但这一轮没对上账」那句警告的开头。措辞在渲染层
#: （`gate_switch.reconcile_warning`），这里只认它有没有出现——两个入口说的是同一句话，
#: 所以这句话本身只该有一个定义处。
RECONCILE_WARNING_HEAD = "⚠️ 档位已经切过去"


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


def _live_gate_declaration() -> HaltDeclaration:
    """一条**活着的**策略档声明行（`trigger=deactivation`，没有 `closed_at`）。

    用来把「档位翻了、但这一轮没对上账」那句警告逼出来：那句话的判据是「有活行 ∧ 本轮
    `skipped`」，而本文件造的世界里**一代池化表都没有**（`plan_round` 于是返回
    `SKIPPED_NO_GENERATION`），所以只要这条行在，两个条件就都成立。
    """
    return HaltDeclaration.objects.create(
        trigger=HaltTrigger.DEACTIVATION.value,
        scope=halt.strategy_scope("11111111-1111-1111-1111-111111111111"),
        label="行情阶段 gate：测试策略",
        opened_at=django_timezone.now() - timedelta(hours=5),
        reason="上一轮写的",
        actor_kind=ActorKind.TASK.value,
        actor_name="任务",
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

    # ---------------- 第二级：行情阶段 gate（③b） ---------------- #

    def test_the_gate_page_is_read_only_and_agrees_with_the_module(self):
        # 与上一节同一条纪律，第二级照旧：页面由模块渲染，命令自己不拼一个字的正文。
        # 组名有中英两个（`gate` / `阶段`），两个都得走到**同一页**而不是两个说法。
        at = django_timezone.now()
        expected = gate_switch.page(now=at).body
        with patch("apps.agent.regime_commands.timezone.now", return_value=at):
            for word in ("gate", "阶段"):
                result = _run(_call(f"/regime {word}"))
                assert result.success, result.error
                assert GATE_PAGE_TITLE in result.data, word
                assert result.data == expected, word
        assert not RegimeMechanismSwitch.objects.exists()

    def test_opening_the_gate_echoes_the_page_and_writes_one_row(self):
        result = _run(_call("/regime gate on"))
        assert result.success, result.error
        # 打开回显整页（与事件熔断同一口径，而且这边更重：它会把一批策略**停掉**）。
        assert GATE_PAGE_TITLE in result.data
        assert "行情阶段 gate 已打开" in result.data
        # 这个世界里一代池化表都没有 ⇒ `gate_run.sync` 走 blocked 那一支。页面必须说出来，
        # 否则「已打开」会被读成「已经在拦了」——而这一轮连对账都没发起。
        assert "本轮一个字都不会动" in result.data
        assert not HaltDeclaration.objects.exists()

        row = _only_row()
        assert row.kind == MechanismKind.REGIME_GATE.value
        assert row.from_mode == MechanismMode.SHADOW.value
        assert row.to_mode == MechanismMode.EXECUTING.value
        # 落款是人（聊天渠道的 sender id），与事件熔断同一条。
        assert row.actor_kind == ActorKind.CHAT.value
        assert row.actor_name == SENDER

    def test_closing_the_gate_re_echoes_the_whole_page(self):
        # **与 `/regime off` 刻意相反**：事件熔断的关闭只回一句「关掉之后什么变了」，gate
        # 的关闭照印整页。理由只有一条——那一页里有「关掉就安全了」在保命档（高波动）上
        # 是错的这句警告，而它是撤防方向上唯一拦得住这个要命误读的东西。
        _run(_call("/regime gate on"))
        result = _run(_call("/regime gate off"))
        assert result.success, result.error
        assert GATE_CLOSING_TITLE in result.data
        assert "行情阶段 gate 已关闭" in result.data
        assert "保命档（高波动）毫无影响" in result.data
        assert "关掉就安全了" in result.data

        row = _latest_row()
        assert row.from_mode == MechanismMode.EXECUTING.value
        assert row.to_mode == MechanismMode.SHADOW.value

    def test_pressing_the_same_gate_button_again_writes_nothing(self):
        _run(_call("/regime gate on"))
        result = _run(_call("/regime gate on"))
        assert result.success, result.error
        assert "本来就是执行态" in result.data
        assert "仍然对了一次账" in result.data
        assert RegimeMechanismSwitch.objects.count() == 1

        _run(_call("/regime gate off"))
        result = _run(_call("/regime gate off"))
        assert result.success, result.error
        assert "本来就是 Shadow" in result.data
        assert "仍然对了一次账" in result.data
        assert RegimeMechanismSwitch.objects.count() == 2

    def test_the_gate_aliases_work(self):
        # 两级各有一套词：组名（`gate` / `阶段`，不进别名表）与动作（开 / 关 那两个表）。
        # 两级的匹配纪律都是**整词**，所以组名的大小写也得认（`GATE` 与 `gate` 同义）。
        for group in ("gate", "GATE", "阶段"):
            for word in ("开", "开启", "打开", "open", "ON"):
                RegimeMechanismSwitch.objects.all().delete()
                result = _run(_call(f"/regime {group} {word}"))
                assert result.success, result.error
                assert _only_row().to_mode == MechanismMode.EXECUTING.value, (group, word)

            for word in ("关", "关闭", "关掉", "close", "OFF"):
                RegimeMechanismSwitch.objects.all().delete()
                _run(_call(f"/regime {group} on"))
                result = _run(_call(f"/regime {group} {word}"))
                assert result.success, result.error
                assert RegimeMechanismSwitch.objects.count() == 2, (group, word)
                assert _latest_row().to_mode == MechanismMode.SHADOW.value, (group, word)

    def test_the_gate_reason_is_its_own_page_s_summary(self):
        # 流水里的原因与屏幕上读到的数是**同一次快照**（`gate_switch.page`）：各算一遍的话，
        # 两次求值之间阶段变了一次，两边就会互相矛盾而没有任何东西会红。时钟钉住，两次
        # 求值才看到同一个「此刻」。
        #
        # 关闭方向那一次要在**翻之前**取：`closing_summary` 里有「那些活行此刻还拦不拦人」
        # 一句，它按**翻之前**的档位说（翻完之后再算就是另一个答案）。
        at = django_timezone.now()
        with patch("apps.agent.regime_commands.timezone.now", return_value=at):
            _run(_call("/regime gate on"))
            reason = _only_row().reason
            assert reason == gate_switch.page(now=at).summary
            assert reason.startswith("人工打开行情阶段 gate")

            expected_closing = gate_switch.page(closing=True, now=at).summary
            _run(_call("/regime gate off"))

        closing_reason = _latest_row().reason
        assert closing_reason == expected_closing
        assert closing_reason.startswith("人工关闭行情阶段 gate")

    def test_a_bad_gate_subcommand_is_feedback_and_names_the_group(self):
        # 「多给一个词」与「认不出的词」都是手滑：`success=True` 的普通回复，且一行不写。
        # 报错里的那个词要带上组名——`未知的子命令：上线` 会让人以为 `/regime 上线` 才是
        # 要敲的那条命令。
        for text, expected in (
            ("/regime gate 上线", "未知的子命令：gate 上线"),
            ("/regime 阶段 上线", "未知的子命令：阶段 上线"),
            ("/regime gate onx", "未知的子命令：gate onx"),
        ):
            result = _run(_call(text))
            assert result.success, result.error
            assert expected in result.data, text
            assert "/regime gate on" in result.data, text

        result = _run(_call("/regime gate on --备注 因为要开会"))
        assert result.success, result.error
        assert "这条命令不吃参数" in result.data
        assert "--备注" in result.data
        assert not RegimeMechanismSwitch.objects.exists()

    def test_the_gate_command_touches_only_the_gate_switch(self):
        _run(_call("/regime gate on"))
        _run(_call("/regime gate off"))
        for kind in (MechanismKind.EVENT_BREAKER, MechanismKind.MECHANISM):
            assert RegimeMechanismSwitch.current(kind) is MechanismMode.SHADOW, kind
        assert not RegimeMechanismSwitch.objects.exclude(
            kind=MechanismKind.REGIME_GATE.value
        ).exists()

    def test_the_gate_flip_is_what_the_halt_side_reads(self):
        # 端到端：命令写下的那一行正是 `halt.switch_open` 读的那一行。
        driver = HaltTrigger.DEACTIVATION
        assert halt.switch_open(driver) is False
        _run(_call("/regime gate on"))
        assert halt.switch_open(driver) is True
        # 保命档那一层**没有开关**（`HALT_TRIGGER_SWITCH[BLANKET] is None`），所以两个方向
        # 都与它无关——「关掉就安全了」在它身上是一句错话。
        assert halt.switch_open(HaltTrigger.BLANKET) is True
        _run(_call("/regime gate off"))
        assert halt.switch_open(driver) is False

    def test_the_reconcile_warning_is_said_when_the_table_was_not_reconciled(self):
        # 「档位翻了，但这一轮算不出该停哪些」：本轮连对账都不发起，活行一条都没被解除，
        # 而屏幕上的「已打开」读起来像「表已经对干净了」。这句话与 CLI 入口共用同一处
        # （`gate_switch.reconcile_warning`），两个入口说的是同一件事。
        live = _live_gate_declaration()
        result = _run(_call("/regime gate on"))
        assert result.success, result.error
        assert RECONCILE_WARNING_HEAD in result.data
        assert "1 条活声明原样留着" in result.data

        # **「本来就是这一档」那条路也要说。** 这时屏幕上唯一的好消息是「仍然对了一次账」，
        # 而这一轮恰恰可能什么都没对上；不说出来，读的人会把「本来就是执行态」当成
        # 「表已经干净了」。CLI 入口两个分支都打这句，聊天这边也得对齐。
        result = _run(_call("/regime gate on"))
        assert result.success, result.error
        assert "本来就是执行态" in result.data
        assert RECONCILE_WARNING_HEAD in result.data

        # 开关不碰声明表：这两个动作既没解除它、也没删掉它（解除是 `gate_run` 的事，
        # 而这一轮它什么都没做）。
        live.refresh_from_db()
        assert live.closed_at is None
        assert HaltDeclaration.objects.filter(pk=live.pk).exists()
