"""进程内共享的同步 Redis 客户端工厂。

**为什么需要它**：仓库里原先有多处 ``redis.from_url(...)`` 各自新建客户端——有的是每次
调用都建（``apps/agent/task_tracker.py`` 的 8 处、``apps/agent/tasks.py`` 的 4 处、
``apps/signal_monitor/engine.py`` 的推送路径）。每次 ``from_url`` 都会建一个**独立的连接
池**并立刻占一条 socket，用完既不复用也不显式关闭：redis-py 5.x 的 ``ConnectionPool``
没有 ``__del__``，socket 要等 GC 才释放。``task_tracker`` 那几个读/写是在「每个任务里程碑
都走一遍」的路径上，``--concurrency=4`` 之下漏损按任务数×里程碑数累积。

按 URL（含 DB 号）缓存客户端，全进程每种 URL 只留一个池。**fork 安全**：Celery 的 prefork
子进程会继承父进程建的池，而池里的 socket 在 fork 后**父子共用同一个 fd**——两边一起读写
就是数据错乱。所以这里按 PID 判定，PID 变了就丢弃父进程的池、重新建立。

与 ``apps/memory/redis_client.py`` 的「按 PID 缓存客户端」是同一条纪律，只是那一份是
``redis.asyncio``、这一份给同步调用方用。
"""

from __future__ import annotations

import os
import threading
from typing import Any

import redis

#: url -> (pid, client)。pid 用来识别 fork 后的子进程，见模块 docstring。
_clients: dict[str, tuple[int, redis.Redis]] = {}
_lock = threading.Lock()


def get_sync_redis(url: str, *, decode_responses: bool = True) -> redis.Redis:
    """拿一个本进程共享的同步 Redis 客户端（按 url 复用连接池）。

    Args:
        url: 完整 Redis URL，**含 DB 号**（不同 DB 是不同客户端）。
        decode_responses: 是否把返回值解码成 str。注意同一 url 只能有一种取值——
            连接池是共享的，混用会让响应类型取决于谁先建池。调用方凡需 decode 就
            显式传 True（本仓库全部调用点都是 True）。
    """
    pid = os.getpid()
    cached = _clients.get(url)
    if cached is not None and cached[0] == pid:
        return cached[1]

    with _lock:
        cached = _clients.get(url)
        if cached is not None and cached[0] == os.getpid():
            return cached[1]

        old = cached[1] if cached is not None else None
        # 用**模块级** ``redis.from_url`` 而不是 ``redis.Redis.from_url``：后者是
        # classmethod，是另一个属性。既有测试（如 test_task_tracker.py）统一
        # ``patch("redis.from_url")``，走模块级入口才能被它们拦住——否则测试会
        # 绕过 mock 真去连 localhost:6379。
        client = redis.from_url(
            url,
            decode_responses=decode_responses,
            health_check_interval=30,
        )
        _clients[url] = (os.getpid(), client)

        # fork 之后：父进程那条池的 socket 与子进程共用 fd，必须让子进程丢掉
        # 自己手里那份引用（不能 close，那会连父进程的 fd 一起关掉）。
        del old

        return client


def reset_for_tests() -> None:
    """清空缓存（测试用；生产代码不该调用）。"""
    with _lock:
        _clients.clear()


def url_with_db(base_url: str, db: int) -> str:
    """把 ``base_url`` 的 DB 号换成 ``db``；已有 DB 后缀时先剥掉。

    仓库里曾有 8 处重复这段逻辑（每处都手写 ``rsplit("/", 1)``）。
    """
    base = base_url.rstrip("/")
    if base.rsplit("/", 1)[-1].isdigit():
        base = base.rsplit("/", 1)[0]
    return f"{base}/{db}"


def get_client_stats() -> dict[str, Any]:
    """排障用：本进程缓存了几个客户端、分别属于哪个 PID。"""
    pid = os.getpid()
    return {
        "pid": pid,
        "cached": len(_clients),
        "stale": sum(1 for (p, _) in _clients.values() if p != pid),
    }
