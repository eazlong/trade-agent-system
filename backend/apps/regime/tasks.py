"""切片任务（第①段单元 6ii）+ 日报投递与投递看门狗（第①段单元 8iv）
+ 停止声明窗口同步（第②段单元 ②c）+ 减仓投递（第②段单元 ②d）
+ 窗口通知与声明写入失败告警（第②段单元 ②e）+ 行情阶段 gate 同步（第③段单元 ③b）。

## 为什么是独立任务

见 `slicing.py` 的模块 docstring：网格搜索带 7200 秒硬超时，切片不能挂在那个循环里。

## 幂等与收敛

任务收的是**一批 result_id**，不是「一个 id + 一个剩余计数」：

- 批本身就是那个计数（`if not ids: return`），少一个可以走偏的状态量；
- 一次网格搜索的所有组合一次性投一批，只起一个任务，不必为 200 个结果起 200 个任务；
- 切片幂等，所以「重投整批」是最省事的重试策略——不必区分「哪几条没成」，
  也不会因为「第一次死在半路」而漏掉尾部。

`only_stale=True` 是默认：它同时兼任「不要重复劳动」和「重复投递不会互相打架」。
「已经有一份不陈旧的切片」就跳过，判据是 `slicing.is_stale` 的三条比对。

## 失败可见性

任务自己**不写告警**（CONTEXT.md：新任务不自己发告警）。失败往上抛：Celery 会重试
（3 次、间隔递增），重试仍失败则任务标 FAILURE。批内单条失败也走同一条路——整批重投，
代价是几轮白干，换来的是「不必为坏数据单开一条错误分支」。

## 日报投递与投递看门狗为什么也在这里、也各自是一条任务

它们是**两条互相独立的路径**（CONTEXT.md:175）：投递任务负责「把日报送到」，
看门狗负责「送到没有」。合成一条就等于让看门狗去报自己的失败——而它恰好是失败的那一
个时，它不会响。**投递任务失败不触发告警**（同上那条纪律：任务自己的故障往上抛，
交给 beat 的任务健康检查）；看门狗要说的不是「我失败了」，而是「今天这份日报没到你
手上」——那是一件关于世界的事实，且它是唯一说得出这句话的东西。

两者都不挂在 `snapshot_daily_equity` 那条心跳上：那条心跳的顺序与职责被
`test_timing.py` 逐段钉着（「回退方式 = 删掉一个调用」），而投递与看门狗本来就是独立
路径，独立成任务才谈得上「一条坏了另一条还在」。

## 窗口同步任务为什么既没有重试、也不发告警

`sync_halt_windows` 是**对账**，不是投递：它每次整表重算事件表与判定表上的事实，写
`HaltDeclaration` 的那一轮与上一轮之间没有「做过 / 没做过」的区别（见 `halt_sync.py`）。
所以它不需要 `max_retries`——**下一轮 300 秒的对账就是重试**，再排三次退避重试只是把
同一件事重做几遍（CONTEXT.md:181 按「读安全 / 写危险」区分新任务，这一条属于「写危险但
可全量重来」）。异常往上抛，失败可见性走已有的两条路：beat 的任务健康检查（跑了没）与
日报第④段机制健康（结论新不新）。

## 行情阶段 gate 为什么是**另一条**对账任务（第③段单元 ③b）

`sync_gate` 与 `sync_halt_windows` 写同一张 `HaltDeclaration`，但**档不同**：那条管事件
熔断与保命档，这条管策略停用档（`HaltTrigger.DEACTIVATION`）。分开的理由是「哪一档没对
上」这件事不能被另一档的成功盖住——两条任务各自的返回值才是人读的那份「这一档刚刚做了
什么」。同一套「对账不重试」（下一轮就是重试）与「失败往上抛」的纪律照旧；唯一多出来的
是**写失败时发告警**：声明表说不出自己在拦什么，与 `sync_halt_windows` 里那条是同一件事
（同一张表、同一个失败面），所以**受众、流程与那一个出口（`alerts.notify_user`）都共用
——只有正文分岔**（第③段 Q1）：事件熔断那一档说「不可人工豁免」，这一档的豁免**人可以
给**（`manage_deactivation_exemptions`）。两条任务各认领自己那一档
（`alert_declaration_write_failure` / `alert_gate_write_failure`），而不是让一条替另一条
发话：照抄那一段会让读的人去找一条不存在的出路，而这一档真正能救的那条路（给一次在期
豁免）只有这一段的正文说得出来。

**它在 Shadow 期也照跑**（`gate_run.sync` 自己认档位）：档位关着时它不拦人，但「阶段换
了、这批策略不再该停」这个事实仍要落进表里；而阶段说不清的那些轮次里活行一条都不动（连
对账都不发起），补上的动作是**把同一次开关动作再敲一遍**——那是人在 `gate_switch` 那边
的事，本任务只负责让每一轮都有一次对账。

## 窗口同步任务为什么兼着减仓投递（第②段单元 ②d）

同一轮里，`halt_sync.sync` 之后紧接着 `reduce_run.dispatch`。三件理由，一条代价：

- **撤单先于减仓、同一一次性任务内串行、不并发**（CONTEXT.md:122）。撤单与减仓都在
  `dispatch` 内部串行；而「窗口开了」这个事实的写入方是 `sync`。拆成两条任务的话，减仓
  那条要么自己再算一遍「窗口开没开」（第二份判定，与声明行分歧的表现是「写着熔断中、
  减仓一动不动」），要么依赖另一条任务先跑完——而 beat 不保证两条任务之间的先后。
- **同一个 `at`**。「窗口此刻开着」（`halt_at <= at < resume_at`）与业务日（「本日第 N 次
  熔断」）必须出自同一个时刻，否则跨零点的那一轮会出现「按昨天的业务日减、按今天算第几次」。
  两次 `timezone.now()` 之间的偏移很小，但留着它没有换来任何东西。
- **「每轮都投」等价于「投一次」**。CONTEXT.md:116 说减仓由一次性任务投递，而这里的
  「一次性」由 `regime_reduce_records` 的幂等键保证（`claim_record` 的 `get_or_create`），
  不靠「这条任务只跑一次」——后者在 300 秒一轮的调度里根本表达不出来。

**代价（留给 ②e / ③ 再看）**：有减仓的那一轮不再是 300 秒的事（市价单、分片、子单重试、
多账户），而 `halt_sync` 里那条「两段窗口之间的小空隙不超过一轮任务间隔」的界是按 300 秒
写的。跑得比调度间隔长时下一轮会与它重叠：声明对账幂等、减仓有幂等记录，所以重叠不会减
两次，但「一轮」在两个模块里从此不是同一个长度。

## 三条即时消息为什么都挂在这一条任务上（第②段单元 ②e）

声明写入失败、窗口开启、窗口结束——三条都是「给一个具体的人的即时消息」（CONTEXT.md:66
的「告警」定义），而 ②e 之前，一个「表里没有窗口」的系统看起来与「现在没有事件」一模
一样。它们全在这一条任务上，因为**它是声明表唯一的写入方**：只有它知道这一轮刚刚把哪
些行改成了什么样子，也只有它能在写失败时立刻说话。收件人口径、渲染与那唯一一个出口
（`alerts.notify_user`）都在 `apps/regime/halt_notify.py`；本模块只管**时机**：

- **窗口通知在 `halt_sync.sync()` 之后、`reduce_run.dispatch()` 之前**（②e Q5）。顺序的
  理由与 ②d 同源——「窗口此刻开着」这个事实的写入方是 `sync`，通知与减仓都读它；而通知
  排在减仓之前是因为它们**互不相干**：`dispatch` 里那道闸门（②d Q8）问的是「这一轮要不要
  真对市场动手」，而「窗口开了」这件事永远要说，Shadow 期尤其要说（②e Q6：Shadow 期的
  消息首行写明「只记录、不真拦」，否则用户会以为下单已经被挡住）。
- **声明写入失败走 catch → 发 → 再抛**（②e Q7），与 `reduce_run._alert`（发了就继续）
  不同：失败的是机制本身，对账没完成就该让任务标 FAILURE——下一轮 300 秒的对账就是重试。
  告警发不出去也只记日志（②e Q8：不递归告警），但**绝不吞掉原异常**——吞掉会让真实故障
  从 beat 的任务健康检查里消失。
- **通知失败不改变任务成败**：任务跑成功了、只是有人的消息没送到，那是 `window_notify`
  里的计数（`failed` / `no_recipients`），随返回值进任务健康检查。窗口通知本身**没有**
  重试逻辑——「送达了才记账」加上 300 秒一轮的调度就是重试（`halt_notify` 的 docstring）。
"""

from __future__ import annotations

import logging

from celery_app import app

logger = logging.getLogger(__name__)

#: 重试次数。CONTEXT.md 给判定任务定的是「有限重试、间隔递增」，切片同款。
#: （名字里的 `SLICE` 是历史：这两个常量现在是本模块所有任务共用的重试参数。）
MAX_SLICE_RETRIES = 3

#: 首次重试的等待秒数；`retry_backoff=True` 会在此基础上翻倍。
RETRY_BACKOFF_SECONDS = 60


def _as_id_list(result_ids) -> list[str]:
    """把「一个 id / 一串 id / 一个 queryset」统一成一串非空字符串。"""
    if not result_ids:
        return []
    if isinstance(result_ids, (str, bytes)):
        candidates = [result_ids]
    else:
        candidates = list(result_ids)
    return [str(rid) for rid in candidates if rid]


def dispatch_slice(result_ids) -> bool:
    """投递一批切片。**绝不抛异常。**

    调用点是两个回测任务的收尾。一次成功的回测不该因为一个附加产物投不出去而被标成
    失败——回测结果已经落库了，把它标成 failed 会让用户以为白跑了。投递失败的代价
    （这次没切）由「`metrics` 里没有 `regime_slice` 键」和全量重算入口两条路兜底。
    """
    ids = _as_id_list(result_ids)
    if not ids:
        return False
    try:
        compute_regime_slice_task.delay(ids)
        return True
    except Exception:
        logger.error(
            "[regime] 切片任务投递失败（回测本身已成功）result_ids=%s", ids, exc_info=True
        )
        return False


@app.task(
    bind=True,
    max_retries=MAX_SLICE_RETRIES,
    default_retry_delay=RETRY_BACKOFF_SECONDS,
    retry_backoff=True,
    acks_late=True,
)
def compute_regime_slice_task(self, result_ids, only_stale: bool = True) -> dict:
    """给一批回测结果算切片并写进 `metrics["regime_slice"]`。

    Args:
        result_ids: 一个 id 或一串 id。空/全空值时直接返回（收敛闸）。
        only_stale: 已有不陈旧切片的结果跳过。默认开。
    """
    from django.db import close_old_connections

    from apps.backtest.models import BacktestResult
    from apps.regime import slicing

    ids = _as_id_list(result_ids)
    if not ids:
        # 收敛闸：没东西可切就干净地结束，不报错——这正是「重投一批已完成的」该有的样子。
        return {"sliced": 0, "skipped": 0, "missing": [], "failed": []}

    summary: dict = {"sliced": 0, "skipped": 0, "missing": [], "failed": []}
    try:
        # 标签全批共用一份：同一个 symbol、同一套参数，算一次就够（见 slicing.compute_slice）。
        tags = slicing.load_tags()

        for result_id in ids:
            # 长循环里的连接纪律（CLAUDE.md）：批可能上百条，每条都在上一次事务边界之外。
            close_old_connections()

            result = BacktestResult.objects.filter(id=result_id).first()
            if result is None:
                # 被删掉了。不是错误，但要记下来——「投了却找不到」与「投漏了」看起来一样。
                summary["missing"].append(result_id)
                continue
            if only_stale and not slicing.is_stale(result, tags):
                summary["skipped"] += 1
                continue

            try:
                slicing.compute_slice(result, tags)
                summary["sliced"] += 1
            except Exception as exc:
                # 单条失败不中断整批：其余结果照样该被切。失败清单进 summary，
                # 批末统一决定要不要重投。
                summary["failed"].append({"result_id": result_id, "error": str(exc)})
                logger.error("[regime] 切片失败 result_id=%s", result_id, exc_info=True)

        if summary["failed"]:
            raise RuntimeError(
                f"{len(summary['failed'])}/{len(ids)} 个结果切片失败："
                f"{summary['failed'][:3]}"
            )
    except Exception as exc:
        logger.error("[regime] 切片任务失败，将重试（第 %s 次）", self.request.retries, exc_info=True)
        raise self.retry(exc=exc)

    logger.info(
        "[regime] 切片任务完成 sliced=%s skipped=%s missing=%s",
        summary["sliced"],
        summary["skipped"],
        len(summary["missing"]),
    )
    return summary


@app.task(
    bind=True,
    max_retries=MAX_SLICE_RETRIES,
    default_retry_delay=RETRY_BACKOFF_SECONDS,
    retry_backoff=True,
    acks_late=True,
)
def deliver_report(self, run_day=None) -> dict:
    """把当天那份日报投给每个 `is_active` 用户（第①段单元 8iv）。

    Args:
        run_day: 运行日（`date` 或 ISO 字符串）。beat 不传——**按业务时区的自然日算**，
            口径在 `report._as_run_day` 一处。留这个入参是为了能手动补投某一天。

    幂等：已成功送达的人不再重投（`report.deliver_daily_report`）。所以这个任务天然可以
    被重复投递、被 beat 每 5 分钟撞一次、被重试三次——代价都只是几次没有收件人的空转。

    **不吞异常**：DB 故障往上抛，走 Celery 的有限重试与任务健康检查。这一层不写告警
    （CONTEXT.md：新任务不自己发告警）；「用户没收到日报」那句话由看门狗说。
    """
    from django.db import close_old_connections

    from apps.regime.report import deliver_daily_report

    try:
        summary = deliver_daily_report(run_day)
        logger.info("[regime] 日报投递任务 %s", summary)
        return summary
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[regime] 日报投递失败，将重试（第 %s 次）", self.request.retries, exc_info=True
        )
        raise self.retry(exc=exc)
    finally:
        # 反复调度的任务必须自己收掉 DB 连接，否则连接会攒在 worker 上
        close_old_connections()


@app.task(
    bind=True,
    max_retries=MAX_SLICE_RETRIES,
    default_retry_delay=RETRY_BACKOFF_SECONDS,
    retry_backoff=True,
    acks_late=True,
)
def check_report_delivery(self, run_day=None) -> dict:
    """投递看门狗：当天日报到点还没投出去就升级告警（第①段单元 8iv）。

    它**只读**：不写 `DailyReport` 的投递明细（那是投递任务的账），也不改任何状态。
    唯一的副作用是往外发一条消息，且同一运行日只成功发一次
    （`report._alerted_on`）。

    一天绝大多数轮次走到的是「未到截止时刻」或「已投递」，都是干净的空转。

    **不吞异常**：这里读不到库就没法判「投出去了没有」，往上抛让 beat 的任务健康检查
    看得见——**这条路径自己哑掉，是它自己发现不了的**（CONTEXT.md:180 承认的那条缝，
    由「日报本身是否到达」在第⑤段兜底）。
    """
    from django.db import close_old_connections

    from apps.regime.report import check_report_delivery as run_check

    try:
        summary = run_check(run_day)
        if summary.get("escalated"):
            logger.warning("[regime] 投递看门狗已升级告警 %s", summary)
        return summary
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[regime] 投递看门狗失败，将重试（第 %s 次）", self.request.retries, exc_info=True
        )
        raise self.retry(exc=exc)
    finally:
        # 反复调度的任务必须自己收掉 DB 连接，否则连接会攒在 worker 上
        close_old_connections()


@app.task(acks_late=True)
def sync_halt_windows() -> dict:
    """把事件表与生效判定上的事实对账成 `HaltDeclaration` 行（第②段单元 ②c），
    投递减仓（第②段单元 ②d），并发那三条即时消息（第②段单元 ②e）。

    这一条是**声明表的唯一写入方**：`halt.py` 的判定函数只读状态、不读事件表
    （CONTEXT.md:134），所以「谁该拦」到「此刻在拦」的搬运全在这里。减仓接在同一轮里
    而不是另起一条任务，理由见模块 docstring 的「窗口同步任务为什么兼着减仓投递」；
    窗口通知与失败告警也在这里，理由见「三条即时消息为什么都挂在这一条任务上」。

    幂等：期望值逐字取自事实里已经存好的时刻（事件的 `halt_at`、判定的 `effective_at`），
    所以连着跑两轮，第二轮必然是整表空转。减仓那一侧另有幂等键（`regime_reduce_records`
    的 `uniq_regime_reduce_record`）；通知那一侧靠 `HaltDeclaration` 上的两个通知时刻
    「送达了才记账」。所以「跑第二轮」既不会重复写声明、也不会重复减仓，也不会重复通知
    （**没送达的会重发**，那是重试而不是重复）。beat 每 5 分钟撞一次、手工补跑任意多次，
    代价都只是几次空转。

    返回值是 `halt_sync.sync` 的摘要，外加 ``reduce_rows``（本轮写下的减仓记录条数，空闲
    轮为 0）与 ``window_notify``（窗口通知的计数：``opened`` / ``closed`` / ``failed`` /
    ``no_recipients``）。后两者进 beat 的任务健康检查——**通知发不出去不改变任务成败**
    （那是「有人没收到消息」，不是「对账没完成」）。

    没有 `max_retries`、也不吞异常——理由见模块 docstring（对账的下一次执行就是重试）。
    beat 用固定 300 秒间隔而不是 crontab：`CELERY_TIMEZONE` 是 UTC，而这条任务只关心
    「多久跑一次」，不关心「每天几点」（见 `celery_app.py` 的 `beat_schedule`）。
    """
    from django.db import close_old_connections

    from apps.regime import halt_notify, halt_sync, reduce_run

    try:
        try:
            # 成功那一轮的日志由 `halt_sync.sync` 自己记（它知道每类改动几条）。
            summary = halt_sync.sync()
        except Exception as exc:
            # ②e Q7：catch → 发 → 再抛。**不吞**：对账没完成，任务就该标 FAILURE。
            logger.error("[regime] 声明写入失败，已发告警后继续往上抛", exc_info=True)
            logger.error(
                "[regime] 声明写入失败告警结果：%s",
                halt_notify.alert_declaration_write_failure(exc),
            )
            raise
        # ②e Q5：窗口通知排在减仓**之前**，且不受 ②d 那道闸门管——「窗口开了」这件事
        # 与「这一轮要不要真动手减仓」是两个问题，前者永远要说。
        summary["window_notify"] = halt_notify.notify_pending()
        # ②d：声明行落库之后**紧接着**投递减仓。顺序不能反——`reduce_run` 认「窗口开着」
        # 靠的正是 `HaltDeclaration`，这一轮刚写的行要在同一轮里被它看见，否则窗口刚开的
        # 那一轮会整个跳过减仓。两次调用之间不重取 `now`：窗口判定与业务日出自同一时刻
        # （见模块 docstring 的「窗口同步任务为什么兼着减仓投递」）。
        #
        # 不传 `extra_open`：那是给「本函数读表之后由别的写方插进来的 `HaltDeclaration`」
        # 留的口子（`halt_sync._reconcile` 的 docstring 记着 ②d 那种场景），而 ②d 不写声明
        # 表——它的认领行是 `RegimeReduceRecord`，且它排在本行之后，所以「它写的行」这件事
        # 在时序上不存在。为了不存在的行多接线，代价是给 `_reconcile` 一条永远为空的入参。
        fired = reduce_run.dispatch()
        summary["reduce_rows"] = fired.get("rows", 0)
        return summary
    finally:
        # 反复调度的任务必须自己收掉 DB 连接，否则连接会攒在 worker 上
        close_old_connections()


@app.task(acks_late=True)
def sync_gate() -> dict:
    """按行情阶段对账「本阶段该停哪些策略」（第③段单元 ③b）。

    与 `sync_halt_windows` 同一套写法、同一张表、不同的档（见模块 docstring 的「行情
    阶段 gate 为什么是另一条对账任务」）：没有 `max_retries`（下一轮 300 秒的对账就是
    重试），异常往上抛进 beat 的任务健康检查；beat 也是固定 300 秒间隔，不用 crontab
    （`CELERY_TIMEZONE` 是 UTC，而行情阶段本身是日频判定的产物，这条任务不关心几点）。

    幂等：`gate_run.sync` 的期望值逐字来自库里存好的事实（当前阶段、当前代、声明的
    `opened_at` 是那个唯一常量），所以连着跑两轮，第二轮必然是声明表整表空转、`statuses`
    为空。返回的就是它的摘要（18 个键，键集在四条路径上一致——它进 Celery 结果与任务
    健康检查）。

    **只读的那一半也一样跑。** 档位是 Shadow、阶段还没判出来（`blocked`）时这一轮几乎
    什么都不写，但那是**结论**而不是「可以跳过」：`skipped` 那件事本身要被记下来，否则
    「机制按阶段停过谁」与「机制根本没在跑」在事后读起来一模一样。
    """
    from django.db import close_old_connections

    from apps.regime import gate_run, halt_notify

    try:
        try:
            return gate_run.sync()
        except Exception as exc:  # noqa: BLE001
            # 与 `sync_halt_windows` 同一条：写声明失败 = 机制说不出自己在拦什么。发完
            # 再抛——吞掉会让真实故障从任务健康检查里消失。
            #
            # 但正文那一档不同（第③段 Q1）：这一档的豁免是**人可以给的**
            # （`manage_deactivation_exemptions`），而事件熔断那一档不可人工豁免。照抄
            # 那一段会让读的人以为无路可走，所以走 `alert_gate_write_failure`。
            logger.error("[regime] 行情阶段 gate 对账失败，已发告警后继续往上抛", exc_info=True)
            logger.error(
                "[regime] 声明写入失败告警结果：%s",
                halt_notify.alert_gate_write_failure(exc),
            )
            raise
    finally:
        # 反复调度的任务必须自己收掉 DB 连接，否则连接会攒在 worker 上
        close_old_connections()
