"""候选推进的**单写者**闸门（Redis SETNX + TTL 兜底）。

## 为什么需要它

`orchestrator.advance()` 从 2026-10-26 起把「长跑」（样本内回测 / 样本外分折）挪到了
事务**外**——原因是引擎不能站在任何事务里（见 `orchestrator.advance` 的 docstring，
两条实证）。挪出去之后，原来那把 `select_for_update` 的候选行锁**不再覆盖整段推进**：
两个 worker 同时扫到同一条候选时，会各自跑一遍同一份回测、各自建一条占位行。

这个闸门把「一条候选同一时刻只有一次推进在飞」补回来。它**不是**正确性防线——
正确性由另外两条保证：占位行在起跑前**已提交**（引擎的另一个连接才看得见它），
以及判定与落库在同一个事务里。即使这把锁失效（Redis 不可达、TTL 到期、进程被杀），
最坏后果也只是**多跑一次回测**：两条结果都带着真实的引擎产物，`evidence.layers`
的最后一次写入胜出，不会把一条本该通过的候选判死（没有「在飞」标记这类需要人肉
清理的中间态）。

所以本模块对 Redis 故障**fail-open**：记一条 warning 后照常推进。为了一个「省算力」
的闸门把整条漏斗停在 Redis 抖动上，代价比它挡下的问题大得多。

## 闸门失效时到底会发生什么（2026-10-26 独立复核后的准确措辞）

把这条锁当成「省算力 + 常规互斥」，**不要**把它当成「行数 / 记录数不变式」的防线。
互斥丢了以后有三个残留，都是「多写」而不是「写错」：

1. **多一条回测行**：两个 worker 会各自跑一遍同一层，各留一条挂同一 `candidate_id` 的
   `BacktestResult`（后写的那条在 `evidence.layers` 里胜出，另一条成为孤儿）。
2. **判定可能引用另一次真实产物**：`orchestrator._stash_layer_record` 是共享槽，两个
   worker 各写自己的 `result_id`，最后写入者胜出——于是 A 可能拿着 B 的 `result_id` 去
   判定。输入相同、产物真实，所以**不会凭空造证据、也不会把该通过的判成不通过**；
   但「这一次结论引用的是哪一回的数」不再是确定的。
3. **多一条转移记录**：串行化之后第二个 worker 读到的 `from_status` 已是结论态，
   `_is_repeat` 判否，于是多落一行审计记录。

## 为什么不用 Postgres 咨询锁

pgbouncer 跑在 `pool_mode = transaction`（docker-compose.yml:49）：会话级咨询锁在
事务池化下会串到别的连接上，那是比没有锁更糟的东西。Redis 是本项目既有的分布式锁
载体——`apps/agent/tasks.py` 的启动恢复锁（SETNX + TTL + Lua 比对删除）是同一个形状，
这里照它写，不另造一套。
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

#: 锁的存活时间（秒）。取值要**明显长于**一次合法长跑（4 年 4h 的样本内约 10 秒，
#: 样本外分折是若干折相加，量级是分钟）——它只是「进程被杀」这类情形下的兜底，
#: 不是正常的持锁时长。
ADVANCE_LOCK_TTL = 1800

#: 键前缀。与 `apps/agent/tasks.py` 的 `recovery:` 前缀同一层命名空间，不共用键。
_LOCK_KEY_PREFIX = "strategy_factory:advance:"

#: 只删自己持有的那把锁（比对值后删除）。直接 `DEL` 会在「锁已过期、别人拿到了」
#: 时把**别人的**锁删掉——那是这把锁最典型的失效方式。
_RELEASE_LUA = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
else
    return 0
end
"""


def _client() -> Any:
    """按需建一个 Redis 客户端（会话级对象，用完即弃，与既有启动恢复锁同形）。"""
    import redis
    from django.conf import settings

    return redis.from_url(settings.REDIS_URL, decode_responses=True)


@contextmanager
def candidate_advance_lock(
    candidate_id: Any, *, ttl: int = ADVANCE_LOCK_TTL
) -> Iterator[bool]:
    """尝试独占某条候选的推进；拿到 yield True，被别人占着 yield False。

    **fail-open**：Redis 不可达时记 warning 并 yield True（照常推进，只是没有互斥）。
    理由见模块 docstring——这把锁丢了只会多跑一次回测，不会误判。
    """
    key = f"{_LOCK_KEY_PREFIX}{candidate_id}"
    token = f"{os.getpid()}:{time.time()}"

    try:
        acquired = bool(_client().set(key, token, nx=True, ex=ttl))
    except Exception:  # noqa: BLE001 - 任何 Redis 故障都不该挡住漏斗
        logger.warning(
            "[StrategyFactory] advance 锁不可用（Redis），候选 %s 本轮不加锁推进",
            candidate_id,
            exc_info=True,
        )
        yield True
        return

    if not acquired:
        logger.info(
            "[StrategyFactory] 候选 %s 已有一次推进在飞，本轮跳过", candidate_id
        )
        yield False
        return

    try:
        yield True
    finally:
        try:
            _client().eval(_RELEASE_LUA, 1, key, token)
        except Exception:  # noqa: BLE001 - 释放失败由 TTL 兜底
            logger.warning(
                "[StrategyFactory] 释放 advance 锁失败（TTL 会兜底）：候选 %s",
                candidate_id,
                exc_info=True,
            )
