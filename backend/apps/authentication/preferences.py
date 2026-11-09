"""用户级界面偏好的唯一出处：**顶栏行情币种**。

## 这份清单为什么必须住在后端

`User.header_symbols` 存的是「这一条登录身份希望顶栏画哪几档」。它是**用户偏好**，
不是浏览器偏好——同一个账号在两台机器上打开应该看到同一组币种，所以不能只放
localStorage 当唯一真相（localStorage 只做首屏缓存/回退）。

## 3–5 个是怎么来的

顶栏是每页常驻的横条（`frontend/tradeclaw-web/src/components/layout/Topbar.tsx`），
它与导航、通知、右侧面板切换挤在同一行 52px 里。少于 3 个，行情这一块就不成"扫一眼
看盘"而是"看一个数字"；多于 5 个，会把导航压到折行——这是**版面的硬约束**，不是审美
偏好。两条边界在前后端都各钉一遍：前端防止用户点到无效动作，后端是真正的门槛
（前端只是个提示，直接 PUT 过来照样得过）。

## 与 `DEFAULT_FEED_SYMBOLS` 的关系（容易踩的那一脚）

`GET /api/datasource/api/market/ticker/` **只从 WS 内存存储取数、不做 REST 回退**
（`apps/datasource/api/views.py::ticker`）。所以「顶栏画了什么」与「WS 订阅了什么」
必须一致，否则那一档永远是「—」而不是一个看得懂的错。

- **默认值**（`DEFAULT_HEADER_SYMBOLS`）与 `ws_runner.DEFAULT_FEED_SYMBOLS` 逐字相同
  ——进程启动时供给就按那份清单订阅，默认这四档开箱即有数。
- **用户改成别的品种**时，保存路径会调 `ws_runner.ensure_feed_symbols()` 把新加的
  品种补进订阅（幂等、尽力而为）。没这一步，用户填了 DOGE 会看到一个永久的「—」。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable, List

logger = logging.getLogger(__name__)

#: 顶栏行情档数的版面硬约束（见模块 docstring）
HEADER_SYMBOLS_MIN = 3
HEADER_SYMBOLS_MAX = 5

#: 计价货币。v1 只认 USDT：ticker 端点与 K 线弹窗都按 `BASE/USDT` 拼请求，
#: 混进 USDC/BTC 计价会得到「订阅了却永远查不到」的同一档，不如在入口就拒掉。
HEADER_SYMBOL_QUOTE = "USDT"

#: 默认四档。**必须与 `apps/datasource/ws_runner.py::DEFAULT_FEED_SYMBOLS` 相同**
#: ——供给启动时按那份清单订阅，两边一致默认档才开箱有数（有测试钉这条）。
DEFAULT_HEADER_SYMBOLS: List[str] = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"]

#: 基础币的合法字符集。交易所上市代码是 2–15 位大写字母数字（1000SHIB、BTC、
#: 1INCH 都在这个集合里）；小写先归一再判。
_BASE_RE = re.compile(r"[A-Z0-9]{2,15}")


class HeaderSymbolsError(ValueError):
    """币种清单不合法。消息是**给人看的原文**，直接进 400 响应的 `error` 字段。"""


def default_header_symbols() -> List[str]:
    """`User.header_symbols` 的 default 回调。

    用函数而不是直接给 `DEFAULT_HEADER_SYMBOLS`：JSONField 的可变默认值会被所有行
    **共享同一个 list 对象**（Django 文档明写的坑），一行 `.append` 就改到全体用户。
    """
    return list(DEFAULT_HEADER_SYMBOLS)


def normalize_header_symbol(raw: Any) -> str:
    """把用户填的一种写法归一成 `BASE/USDT`。

    接受三种写法，因为人不一定记得这条链路的格式：`btc` / `btcusdt` / `BTC/USDT`。
    归一之后**大小写与斜杠都不再由用户决定**——顶栏、ticker 请求、WS 订阅三处都读
    同一个字符串，写得五花八门就会出现「订阅了 BTCUSDT、请求的是 BTC/USDT」这种
    永远对不上的档。
    """
    if not isinstance(raw, str):
        raise HeaderSymbolsError(f"币种必须是字符串，收到 {type(raw).__name__}")
    text = raw.strip().upper().replace(" ", "")
    if not text:
        raise HeaderSymbolsError("币种不能为空")

    # 分隔符 `/` 与 `-` 一视同仁：`btc-usdt` 和 `BTC/USDT` 是同一种写法。
    base, sep, quote = text.replace("-", "/").partition("/")
    if sep:
        if "/" in quote:
            raise HeaderSymbolsError(f"币种写法看不懂：{raw!r}（示例：BTC/USDT）")
        if quote != HEADER_SYMBOL_QUOTE:
            raise HeaderSymbolsError(
                f"只支持 {HEADER_SYMBOL_QUOTE} 计价的交易对（收到 {raw!r}）"
            )
    elif text.endswith(HEADER_SYMBOL_QUOTE) and len(text) > len(HEADER_SYMBOL_QUOTE):
        base = text[: -len(HEADER_SYMBOL_QUOTE)]
    else:
        base = text

    if not _BASE_RE.fullmatch(base):
        raise HeaderSymbolsError(
            f"币种代码不合法：{raw!r}（2–15 位字母或数字，如 BTC）"
        )
    return f"{base}/{HEADER_SYMBOL_QUOTE}"


def normalize_header_symbols(values: Iterable[Any]) -> List[str]:
    """校验并归一一份完整清单。**保持用户给的顺序**（顺序就是顶栏的显示顺序）。

    三处校验各有各的失败场景，缺一条都会让页面上出现一个说不清的状态：

    1. 条数越界 → 「至少 3 个 / 最多 5 个」；
    2. 有重复 → 顶栏会画两档一模一样的价格，且用户以为自己配了两个品种；
    3. 单个写法不合法 → 归一函数自己会抛。
    """
    if not isinstance(values, (list, tuple)):
        raise HeaderSymbolsError("币种清单必须是列表")

    normalized: List[str] = []
    for item in values:
        symbol = normalize_header_symbol(item)
        if symbol in normalized:
            raise HeaderSymbolsError(f"币种重复：{symbol}")
        normalized.append(symbol)

    if len(normalized) < HEADER_SYMBOLS_MIN:
        raise HeaderSymbolsError(
            f"顶栏行情币种至少 {HEADER_SYMBOLS_MIN} 个，最多 {HEADER_SYMBOLS_MAX} 个"
            f"（当前 {len(normalized)} 个）"
        )
    if len(normalized) > HEADER_SYMBOLS_MAX:
        raise HeaderSymbolsError(
            f"顶栏行情币种至少 {HEADER_SYMBOLS_MIN} 个，最多 {HEADER_SYMBOLS_MAX} 个"
            f"（当前 {len(normalized)} 个）"
        )
    return normalized


def get_header_symbols(user: Any) -> List[str]:
    """读一条用户身份上**当前生效**的顶栏币种清单。

    落库的值可能不合法（历史数据、绕过接口直接改库、或未来收紧了规则），这时
    **退回默认四档而不是空列表**：空列表意味着「顶栏一档都不画」，用户会以为是
    功能坏了；默认值是「回到出厂状态」，看得懂、也能在配置里立刻改回来。
    """
    raw = getattr(user, "header_symbols", None)
    try:
        return normalize_header_symbols(raw)
    except HeaderSymbolsError as e:
        logger.warning(
            "[preferences] invalid header_symbols on user %s, falling back to default: %s",
            getattr(user, "id", "?"),
            e,
        )
        return default_header_symbols()


def ensure_header_symbols_subscribed(symbols: List[str]) -> List[str]:
    """把这份清单补进实时行情供给的订阅（幂等、尽力而为）。

    单独一层的原因：**保存偏好不能因为行情供给没起来而失败**。日志/DB 这台机器上
    `DATASOURCE_AUTO_WS` 可能关着、WS 可能正在重连——那些都不影响「这组币种被记下来」
    这件事。订阅补不上时顶栏会先显示「—」，下轮连接由 `_resubscribe_all` 补发，
    用户重新打开页面（GET 偏好）时还会再补一次。
    """
    if not symbols:
        return []
    try:
        from apps.datasource.ws_runner import ensure_feed_symbols

        return ensure_feed_symbols(symbols)
    except Exception as e:  # noqa: BLE001 —— 行情侧任何故障都不该冒泡成 500
        logger.warning("[preferences] ensure feed symbols failed: %s", e)
        return []
