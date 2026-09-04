"""sync_to_async 的数据库安全封装。

在 Celery worker 中，sync_to_async 在线程池的独立线程中执行同步函数。
每个线程有自己的 Django 数据库连接（thread-local），长时间运行后连接可能
被 pgbouncer / PostgreSQL 服务端关闭。

db_async 在目标线程内部首先调用 close_old_connections() 刷新连接，
确保 ORM 操作不会因 "connection already closed" 而失败。

用法::

    from apps.core.db_utils import db_async

    result = await db_async(SomeModel.objects.create)(field1=val1, field2=val2)
    result = await db_async(lambda: SomeModel.objects.filter(...).delete())()
    items = await db_async(list)(SomeModel.objects.filter(...))
"""

from __future__ import annotations

from asgiref.sync import sync_to_async
from django.db import close_old_connections


def db_async(fn):
    """包装 sync_to_async，在目标线程内自动刷新数据库连接。

    等价于 sync_to_async(fn, thread_sensitive=False)，但执行 fn 之前会
    调用 close_old_connections() 确保目标线程的连接可用。

    固定 thread_sensitive=False：通用线程池里 DB 连接是 thread-local，
    长时间空闲后 pgbouncer / Postgres 会单方面关闭 → 必须先
    close_old_connections() 重连。同时不占用 asgiref 共享单线程，
    避免 FrameManager K 线回调里的阻塞 ccxt 网络调用占死单线程、
    导致 ASGI 处理器（WebSocket 握手等）排队挂死。
    """

    @sync_to_async(thread_sensitive=False)
    def _inner(*args, **kwargs):
        close_old_connections()
        return fn(*args, **kwargs)

    return _inner
