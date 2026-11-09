"""测试环境不得依赖外部进程（Redis）——否则是**跨文件的顺序依赖**。

## 这份测试钉的缺陷

`core.settings.test` 原先继承基类的 `CACHES`（`redis://localhost:6379/1`）。单测不保证
本机有 Redis，而 DRF 的 `AnonRateThrottle` 把计数写进这个缓存。于是任何在
`test_logging.py` 之前**发出过匿名请求**的用例都在共享桶里留下计数，之后那些靠
`patch.object(api_settings, "DEFAULT_THROTTLE_CLASSES", [])` 绕开限流的 API 用例
一律拿到 `ConnectionRefusedError`。

表现是最难查的那一种：**单跑绿、混跑红，而且红的文件与被改的代码毫无关系**。
实测触发者不止一处——`apps/authentication/tests/test_refresh_throttle.py`（既有文件）
同样会把它带红；`test_permissions.py` 那 5 条也在同一根因下红。

## 为什么钉「默认缓存是 LocMem」而不是钉某个用例

修法是让**默认**缓存进程内化，而不是逐个文件去绕。所以这里断言的是配置本身：
只要有人把 `test.py` 的 `CACHES` 改回 Redis（或删掉那段覆盖），这条立刻红——
比等某个倒霉文件混跑失败要早得多。
"""

from __future__ import annotations

from django.conf import settings
from django.core.cache import cache
from rest_framework.throttling import SimpleRateThrottle


def test_default_cache_is_not_redis():
    """默认缓存必须是进程内的：单测不保证本机有 Redis。"""
    backend = settings.CACHES["default"]["BACKEND"]
    assert "redis" not in backend.lower(), (
        f"测试环境的默认缓存又指向 Redis 了（{backend}）。这会让「在别的文件里发过"
        "匿名请求」变成「本文件里限流用例全红」——单跑绿、混跑红，且红的地方看起来"
        "与被改的代码无关。"
    )
    assert "locmem" in backend.lower(), backend


def test_cache_roundtrip_does_not_need_a_server():
    """真的写读一次：只要这一句不抛 `ConnectionError`，就说明没有外部依赖。"""
    cache.set("settings-isolation-probe", 1, 5)
    assert cache.get("settings-isolation-probe") == 1
    cache.delete("settings-isolation-probe")


def test_throttle_bucket_semantics_still_work():
    """缓存进程内化**不等于**把限流关掉：桶仍然存在、计数仍然累加。

    这条防的是「修隔离时顺手把限流整个删掉」——`test_refresh_throttle.py` 那两条
    「打满 20 发再断言 429」正是靠这个语义成立的。
    """

    class _TwoPerMinute(SimpleRateThrottle):
        scope = "isolated-probe"
        rate = "2/min"

        def get_cache_key(self, request, view):
            return "isolation-bucket-probe"

    class _Req:
        META = {"REMOTE_ADDR": "10.0.0.1"}

    cache.delete("isolation-bucket-probe")
    throttle = _TwoPerMinute()
    assert throttle.allow_request(_Req(), None) is True
    assert throttle.allow_request(_Req(), None) is True
    # 第三发超限：桶里确实记了数，说明缓存是活的、只是不依赖外部进程
    assert throttle.allow_request(_Req(), None) is False
    cache.delete("isolation-bucket-probe")
