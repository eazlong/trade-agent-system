"""切片任务（第①段单元 6ii）+ 日报投递与投递看门狗（第①段单元 8iv）。

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
