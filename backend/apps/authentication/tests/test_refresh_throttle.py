"""令牌刷新端点的**限流隔离**（txy 生产实测，2026-11-05 16:41–16:55 CST）。

## 这一份钉的是什么缺陷

那一段时间后端日志里 `/api/...` 每分钟 50–128 条 `401 Unauthorized`，
`Unauthorized: /api/trading/klines/` 也在其中；用户看到的是「K 线一直加载不出来」。
访问令牌过期时，交易页一次挂载并发发出的十几个请求**各发一发刷新**，而
`/api/auth/token/refresh/` 是 `AllowAny` —— 落在 `AnonRateThrottle` 的 `anon` 桶
（20/min），**与未登录访客共用同一个配额**。

这有两个后果，都是静默的：

1. **续会话被无关流量挤掉**：同一 IP 上只要有人在翻公开接口，用户就续不上会话。
2. **自己打自己**：一次挂载十几个并发刷新就能把桶打穿（前端侧已用单飞压到 1 发，
   但后端不该依赖前端不犯错）。

所以这份测试钉两条：

- 刷新**不占** `anon` 桶：先把匿名桶打满，刷新仍然成功。反向用例——把
  `@throttle_classes([RefreshRateThrottle])` 摘掉，它就红（退回共用 `anon` 桶）。
- 刷新**仍有**上限：超限返回 429，而不是无限放行。这一条防的是「修隔离时顺手
  把限流整个删掉」——那会让这个接受任意字符串输入的端点变成免费的 CPU 打点。

## 缓存后端

限流计数落在 Django cache 上。生产与 `core.settings.test` 的 `CACHES["default"]` 都
指向 Redis，而单测不保证本机有 Redis。所以这两条用例用 `settings` fixture 把缓存
**换成 LocMem**。注意顺序：换缓存必须发生在任何 `cache.clear()` **之前**，否则
`cache.clear()` 自己就会去连 Redis（`clear()` 会实例化缓存后端）。这就是为什么这里
用 `settings` fixture 而不是 `@override_settings` —— 后者的生效时机在 fixture 之后。
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client
from rest_framework_simplejwt.tokens import RefreshToken

LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "refresh-throttle-tests",
    }
}


@pytest.fixture
def throttle_cache(settings):
    """把缓存换成 LocMem 并清空 —— **先换再清**（`clear()` 会去连后端）。"""
    settings.CACHES = LOCMEM_CACHE
    cache.clear()
    yield cache
    cache.clear()


@pytest.fixture
def refresh_token(db) -> str:
    user = get_user_model().objects.create_user(
        email="kline-throttle@test.local",
        password="pw-for-throttle-test",
        username="kline_throttle_user",
    )
    return str(RefreshToken.for_user(user))


def _refresh(client: Client, token: str):
    return client.post(
        "/api/auth/token/refresh/",
        data={"refresh": token},
        content_type="application/json",
    )


def _register_probe(client: Client):
    """发一发非法注册：只要过限流计数就行，业务结果无关。"""
    return client.post(
        "/api/auth/register/",
        data={"email": "not-an-email"},
        content_type="application/json",
    )


def test_refresh_still_works_after_anon_bucket_is_exhausted(
    throttle_cache, refresh_token
):
    """匿名桶打满后，刷新仍必须成功 —— 它用的是自己的配额。"""
    client = Client()

    # 打满 `anon`（20/min）。`/api/auth/register/` 是 `AllowAny` 且没有独立 scope，
    # 所以它老实占 `anon` 桶，正好充当「同一 IP 上的未登录流量」。
    for _ in range(20):
        _register_probe(client)

    # **前置断言**：确认匿名桶真的满了。否则「刷新还能成功」可能只是因为桶没满，
    # 那样这条用例在共用 `anon` 桶的实现下也会绿——正是要防的假绿。
    assert _register_probe(client).status_code == 429, (
        "匿名桶没被打满，这条用例失去意义 —— 检查 `anon` 速率或打满的循环次数"
    )

    resp = _refresh(client, refresh_token)
    assert resp.status_code == 200, (
        f"刷新被匿名桶拖累（{resp.status_code}）—— token_refresh 必须挂 "
        "RefreshRateThrottle 的独立 auth-refresh 配额，不能落进 anon 桶"
    )
    assert "access" in resp.json()


def test_refresh_endpoint_still_rate_limited(throttle_cache, refresh_token):
    """独立配额依然存在上限：超限回 429，不是无限放行。"""
    client = Client()
    # auth-refresh = 60/min。打 61 发，最后一发必须是 429。
    statuses = [_refresh(client, refresh_token).status_code for _ in range(61)]
    assert statuses[0] == 200, "第一发刷新就该成功"
    assert statuses[-1] == 429, (
        "刷新端点没有任何上限 —— 它接受任意字符串输入，没有速率上限就是免费的 CPU 打点"
    )
