"""顶栏行情币种的偏好层：**3–5 个、默认 BTC/ETH/SOL/BNB、按顺序显示**。

这份测试钉四件事，每一件坏了都是静默的：

1. **边界真的拦人**（2 个 / 6 个一律 400）。只钉「合法的能存」是没用的——一个恒
   返回 `values` 的实现照样绿，而顶栏会把导航压到折行、或者只剩一档不像行情条。
2. **归一真的发生**（`btc` / `btcusdt` 都落成 `BTC/USDT`）。这条链路上顶栏、ticker
   请求、WS 订阅三处读同一个字符串；写法不统一，就会出现「订阅了 BTCUSDT、请求的是
   BTC/USDT」那种永远对不上、且只表现为一个「—」的档。
3. **默认值只有一处**：`DEFAULT_HEADER_SYMBOLS` 与
   `apps/datasource/ws_runner.py::DEFAULT_FEED_SYMBOLS` 逐字相同。不同的话，
   「从没改过偏好的用户」看到的是四档，而进程启动只订阅了三档里的某些——那一档永远「—」。
4. **读路径补订阅**：`GET /api/auth/preferences/` 必须调 `ensure_feed_symbols`
   （幂等、尽力而为）。少这一步，用户在新进程登录后看到的是永久「—」。

失败方向的选择也有测试：库里存了非法值（历史数据 / 直接改库）时，读出来是**默认四档**
而不是空列表或 500。空列表意味着「顶栏一档都不画」，用户会以为功能坏了。
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from apps.authentication.preferences import (
    DEFAULT_HEADER_SYMBOLS,
    HEADER_SYMBOLS_MAX,
    HEADER_SYMBOLS_MIN,
    HeaderSymbolsError,
    default_header_symbols,
    get_header_symbols,
    normalize_header_symbol,
    normalize_header_symbols,
)
from apps.datasource.ws_runner import DEFAULT_FEED_SYMBOLS

User = get_user_model()

PREFS = "/api/auth/preferences/"


# ── 纯函数：归一 ──


class TestNormalizeSymbol:
    """三种人手会写的写法 → 一种系统认的写法。"""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("BTC", "BTC/USDT"),
            ("btc", "BTC/USDT"),
            ("btcusdt", "BTC/USDT"),
            ("BTCUSDT", "BTC/USDT"),
            ("BTC/USDT", "BTC/USDT"),
            (" btc/usdt ", "BTC/USDT"),
            ("btc-usdt", "BTC/USDT"),
            ("1000shib", "1000SHIB/USDT"),
            ("1inch", "1INCH/USDT"),
        ],
    )
    def test_accepts_common_spellings(self, raw, expected):
        assert normalize_header_symbol(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "   ",
            "B",  # 太短
            "THISISWAYTOOLONGNAME",  # 超过 15 位
            "BTC/USDC",  # 非 USDT 计价
            "BTC-EUR",
            "BTX/文",  # 非 ASCII 基础币
            None,
            123,
        ],
    )
    def test_rejects_garbage(self, raw):
        with pytest.raises(HeaderSymbolsError):
            normalize_header_symbol(raw)

    def test_error_message_is_human_readable(self):
        """`error` 原文会直接显示在页面上——它得说清「该怎么填」，不是抛个 ValueError。"""
        with pytest.raises(HeaderSymbolsError) as e:
            normalize_header_symbol("BTC/USDC")
        assert "USDT" in str(e.value)


# ── 纯函数：整份清单 ──


class TestNormalizeSymbolList:
    def test_keeps_user_order(self):
        """顺序就是顶栏的显示顺序 —— 不能被归一过程排序。"""
        assert normalize_header_symbols(["SOL", "BTC", "ETH"]) == [
            "SOL/USDT",
            "BTC/USDT",
            "ETH/USDT",
        ]

    def test_min_boundary_accepts_exactly_min(self):
        assert (
            len(normalize_header_symbols(["BTC", "ETH", "SOL"])) == HEADER_SYMBOLS_MIN
        )

    def test_max_boundary_accepts_exactly_max(self):
        five = ["BTC", "ETH", "SOL", "BNB", "XRP"]
        assert len(normalize_header_symbols(five)) == HEADER_SYMBOLS_MAX

    def test_too_few_rejected(self):
        with pytest.raises(HeaderSymbolsError) as e:
            normalize_header_symbols(["BTC", "ETH"])
        msg = str(e.value)
        assert str(HEADER_SYMBOLS_MIN) in msg and str(HEADER_SYMBOLS_MAX) in msg

    def test_too_many_rejected(self):
        with pytest.raises(HeaderSymbolsError) as e:
            normalize_header_symbols(["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE"])
        assert str(HEADER_SYMBOLS_MAX) in str(e.value)

    def test_empty_rejected(self):
        with pytest.raises(HeaderSymbolsError):
            normalize_header_symbols([])

    def test_duplicates_rejected_after_normalization(self):
        """`BTC` 与 `btcusdt` 是同一档 —— 归一后才判重，否则顶栏画两个一样的价格。"""
        with pytest.raises(HeaderSymbolsError) as e:
            normalize_header_symbols(["BTC", "btcusdt", "ETH"])
        assert "重复" in str(e.value)

    def test_non_list_rejected(self):
        with pytest.raises(HeaderSymbolsError):
            normalize_header_symbols("BTC/USDT")


# ── 默认值：单一来源 ──


class TestDefaults:
    def test_default_matches_feed_symbols(self):
        """出厂默认四档 == 进程启动时的订阅清单（否则没改过偏好的用户看着「—」）。"""
        assert DEFAULT_HEADER_SYMBOLS == DEFAULT_FEED_SYMBOLS

    def test_default_is_btc_eth_sol_bnb(self):
        """用户要的那四档，逐字钉住。"""
        assert DEFAULT_HEADER_SYMBOLS == [
            "BTC/USDT",
            "ETH/USDT",
            "SOL/USDT",
            "BNB/USDT",
        ]

    def test_default_returns_fresh_list(self):
        """JSONField 的可变默认值会被所有行共享 —— 一行 `.append` 会改到全体用户。"""
        a = default_header_symbols()
        a.append("XRP/USDT")
        assert default_header_symbols() == DEFAULT_HEADER_SYMBOLS


# ── 读：非法库值退回默认 ──


@pytest.mark.django_db
class TestGetHeaderSymbols:
    def _user(self, username, symbols=None):
        return User.objects.create_user(
            email=f"{username}@prefs.local",
            password="pw-prefs-test",
            username=username,
            header_symbols=symbols,
        )

    def test_null_falls_back_to_default(self):
        user = self._user("prefs-null")
        assert get_header_symbols(user) == DEFAULT_HEADER_SYMBOLS

    def test_stored_list_is_normalized_on_read(self):
        user = self._user("prefs-stored", ["btc", "eth", "sol"])
        assert get_header_symbols(user) == ["BTC/USDT", "ETH/USDT", "SOL/USDT"]

    def test_invalid_stored_list_falls_back_to_default(self):
        """库里的非法值不制造 500、也不制造「顶栏空一档」—— 退回默认四档。"""
        user = self._user("prefs-bad", ["BTC", "ETH"])  # 只有 2 个
        assert get_header_symbols(user) == DEFAULT_HEADER_SYMBOLS

    def test_not_a_list_falls_back_to_default(self):
        user = self._user("prefs-notlist", {"weird": 1})
        assert get_header_symbols(user) == DEFAULT_HEADER_SYMBOLS


# ── HTTP 面 ──


def _client(user=None):
    from rest_framework.test import APIClient

    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


@pytest.fixture
def local_cache(settings):
    """把限流计数落在 LocMem 上。

    `DEFAULT_THROTTLE_CLASSES` 走 Django cache，而 `core.settings.test` 的 `CACHES`
    指向 Redis——单测不保证本机有 Redis，不换就是一个 `ConnectionError` 冒充业务失败
    （同一个坑与 `test_refresh_throttle.py` 一致：**先换再清**，`clear()` 会去实例化
    后端）。限流本身不是这条链路要测的东西。
    """
    from django.core.cache import cache

    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "header-symbols-tests",
        }
    }
    cache.clear()
    yield cache
    cache.clear()


@pytest.fixture
def subscribed(monkeypatch):
    """记录「读路径/写路径有没有去补订阅」，并且**不碰真实网络**。

    `ensure_header_symbols_subscribed` 会走 `ws_runner.ensure_feed_symbols`，那里在
    pytest 环境下 `_feed_started` 为假、只登记清单不发帧。但这里再打一层桩把这层也
    摘掉：这条用例要测的是「有没有调用」，不是行情供给本身（那由 test_ws_runner 管）。
    """
    calls = []

    def fake_ensure(symbols):
        calls.append(list(symbols))
        return list(symbols)

    import apps.authentication.preferences as prefs

    monkeypatch.setattr(prefs, "ensure_feed_symbols", fake_ensure, raising=False)
    monkeypatch.setattr(
        "apps.authentication.views.ensure_header_symbols_subscribed",
        lambda symbols: calls.append(list(symbols)),
    )
    return calls


@pytest.mark.django_db
@pytest.mark.usefixtures("local_cache")
class TestPreferencesEndpoint:
    def _user(self, username, symbols=None):
        return User.objects.create_user(
            email=f"{username}@prefs.local",
            password="pw-prefs-test",
            username=username,
            header_symbols=symbols,
        )

    def test_requires_auth(self):
        assert _client().get(PREFS).status_code == 401
        assert (
            _client().put(PREFS, {"header_symbols": []}, format="json").status_code
            == 401
        )

    def test_get_returns_default_for_untouched_user(self, subscribed):
        user = self._user("pref-get-default")
        data = _client(user).get(PREFS).json()
        assert data["header_symbols"] == DEFAULT_HEADER_SYMBOLS
        assert data["is_default"] is True
        assert data["min"] == HEADER_SYMBOLS_MIN
        assert data["max"] == HEADER_SYMBOLS_MAX
        assert data["default"] == DEFAULT_HEADER_SYMBOLS

    def test_get_subscribes_the_list(self, subscribed):
        """读路径必须补订阅（ticker 只读 WS 存储，没订阅就是永久「—」）。"""
        user = self._user("pref-get-sub", ["BTC", "ETH", "DOGE"])
        _client(user).get(PREFS)
        assert subscribed == [["BTC/USDT", "ETH/USDT", "DOGE/USDT"]]

    def test_put_saves_and_normalizes(self, subscribed):
        user = self._user("pref-put")
        resp = _client(user).put(
            PREFS,
            {"header_symbols": ["sol", "btcusdt", "eth"]},
            format="json",
        )
        assert resp.status_code == 200
        assert resp.json()["header_symbols"] == [
            "SOL/USDT",
            "BTC/USDT",
            "ETH/USDT",
        ]
        assert resp.json()["is_default"] is False
        user.refresh_from_db()
        assert user.header_symbols == ["SOL/USDT", "BTC/USDT", "ETH/USDT"]

    def test_put_subscribes_new_symbols(self, subscribed):
        """用户刚把 DOGE 配进来，下一轮轮询就该有数，而不是等下一次重连。"""
        user = self._user("pref-put-sub")
        _client(user).put(
            PREFS,
            {"header_symbols": ["BTC", "ETH", "DOGE"]},
            format="json",
        )
        assert subscribed == [["BTC/USDT", "ETH/USDT", "DOGE/USDT"]]

    @pytest.mark.parametrize(
        "payload",
        [
            ["BTC", "ETH"],  # 太少
            ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE"],  # 太多
            ["BTC", "btcusdt", "eth"],  # 重复
            [],  # 空
            "BTC/USDT",  # 不是列表
            ["BTC/USDC", "ETH", "SOL"],  # 非 USDT 计价
        ],
    )
    def test_invalid_payload_is_400_with_human_message(self, payload, subscribed):
        user = self._user("pref-put-bad")
        resp = _client(user).put(PREFS, {"header_symbols": payload}, format="json")
        assert resp.status_code == 400
        body = resp.json()
        # `error` 是给人看的原文：前端直接显示，不自己按状态码翻译
        assert isinstance(body.get("error"), str) and body["error"]
        assert subscribed == []  # 存不下就不该动订阅

    def test_invalid_payload_does_not_touch_stored_value(self, subscribed):
        user = self._user("pref-put-bad-keep", ["BTC", "ETH", "SOL"])
        _client(user).put(PREFS, {"header_symbols": ["BTC", "ETH"]}, format="json")
        user.refresh_from_db()
        assert user.header_symbols == ["BTC", "ETH", "SOL"]

    def test_put_missing_key_is_400(self, subscribed):
        user = self._user("pref-put-missing")
        resp = _client(user).put(PREFS, {}, format="json")
        assert resp.status_code == 400
        assert resp.json()["error"]
