"""
Binance Futures 交易所适配器。

使用原生 httpx 调用 Binance USDM Futures API (fapi.binance.com)。
签名算法: HMAC SHA256。
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import time
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Optional
from urllib.parse import urlencode

import httpx
from anyio import BrokenResourceError, ClosedResourceError
from django.conf import settings

from .base import (
    BaseExchangeAdapter,
    OrderNotFoundError,
    OrderRequest,
    OrderResponse,
    OrderFill,
    Position,
)

logger = logging.getLogger(__name__)

# Futures API base URL
_BASE_URL = "https://fapi.binance.com"

# 传输层异常分类（httpx）
# 只有"请求肯定没发出去"的错误，对下单才是可安全重发的；其余（读超时/协议错误）
# 可能已被交易所受理，重发即为重复下单 → 只能按 clientOrderId 对账。
_PRE_SEND_ERRORS: tuple = (
    httpx.ConnectError,  # TCP 连接建立失败
    httpx.ConnectTimeout,  # 连接超时（尚未发出）
    httpx.PoolTimeout,  # 未从连接池拿到连接
)
# anyio 资源类错误：连接被关闭/损坏。**不是** httpx.TransportError，日志里同样只有空消息。
# 语义与传输层错误同族：请求可能没出去，也可能出去后响应通道被关掉（含糊，不可盲目重发）。
_CONNECTION_LOST_ERRORS: tuple = (ClosedResourceError, BrokenResourceError)
# 统一出口 _request 需要捕获的全部"连接层"错误
_EGRESS_ERRORS: tuple = (httpx.TransportError, *_CONNECTION_LOST_ERRORS)

_AMBIGUOUS_ORDER_ERRORS: tuple = (
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.WriteError,
    httpx.ReadError,
    httpx.RemoteProtocolError,
    *_CONNECTION_LOST_ERRORS,  # 可能已被受理：必须按 clientOrderId 对账，不能直接判失败
)
# 交易所报"单号重复"= 前一次请求其实已被受理（幂等命中）
_DUPLICATE_ORDER_MARKERS = ("-4116", "Duplicate order sent")


class BinanceAdapter(BaseExchangeAdapter):
    """
    Binance USD-M Futures 适配器。

    API 文档: https://developers.binance.com/docs/derivatives/usds-margined-futures
    """

    BASE_URL = _BASE_URL
    TIMEOUT = 10.0
    # 旧客户端延迟关闭的宽限期：重建当刻可能正有并发请求在用旧的（见 _close_client_later）
    CLIENT_CLOSE_GRACE_SECONDS = 15.0
    DEFAULT_LEVERAGE = 10  # 默认杠杆倍数

    def __init__(self, api_key: str, secret: str, testnet: bool = False):
        super().__init__(api_key, secret)
        if testnet:
            self.BASE_URL = "https://demo-fapi.binance.com"
        self._client: Optional[httpx.AsyncClient] = None
        # 记录创建客户端的 event loop：跨 loop 使用会抛
        # "Event is bound to a different event loop"（启动窗口实测），需要重建。
        self._client_loop: Optional[asyncio.AbstractEventLoop] = None
        # 延迟关闭任务强引用（asyncio 只弱引用 task，不持有会被 GC 掉）
        self._closing_tasks: set = set()
        self._time_offset: int = 0  # ms: local_time = server_time + offset
        self._leverage_set: set[str] = set()  # 已设置杠杆的交易对
        self._symbol_rules: Optional[dict[str, dict]] = None  # exchangeInfo 精度规则缓存

    async def connect(self) -> None:
        await self._create_client()

    async def _create_client(self) -> None:
        """创建 HTTP 客户端并同步时钟（重建路径复用；不负责处置旧客户端）。"""
        headers = {"X-MBX-APIKEY": self._api_key}
        proxy = getattr(settings, "WEB_PROXY", "") or None
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers=headers,
            timeout=self.TIMEOUT,
            proxy=proxy if proxy else None,
        )
        self._client_loop = self._running_loop()
        # Sync clock with Binance server to avoid timestamp drift errors
        await self._sync_time()

    async def _sync_time(self) -> None:
        """Fetch server time and compute local clock offset.

        Stores offset so that _sign() can produce server-aligned timestamps.
        Falls back gracefully on failure (offset remains 0).
        """
        try:
            # Use a temporary sync client since self._client may not be fully
            # initialized for base_url-relative paths yet.
            resp = await self._client.get("/fapi/v1/time")
            if resp.status_code == 200:
                server_ts = resp.json().get("serverTime", 0)
                local_ts = int(time.time() * 1000)
                self._time_offset = local_ts - server_ts
                drift_s = abs(self._time_offset) / 1000
                if drift_s > 3:
                    logger.warning(
                        f"Binance clock drift: {drift_s:.1f}s "
                        f"(offset={self._time_offset}ms), will auto-correct"
                    )
                else:
                    logger.info(f"Binance clock synced (drift={drift_s:.2f}s)")
            else:
                logger.warning(
                    f"Binance time sync failed: HTTP {resp.status_code}"
                )
        except Exception as e:
            logger.warning(f"Binance time sync failed: {e}")

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        self._client_loop = None

    def _sign(self, params: dict) -> str:
        """HMAC SHA256 签名，返回完整查询字符串。

        返回已签名的 query string，可直接拼接在 URL 后。
        避免 httpx 重排 params 导致签名失效。
        使用 _time_offset 校正本地时钟偏差，确保 timestamp 落在 recvWindow 内。
        """
        params = dict(params)
        params["timestamp"] = int(time.time() * 1000) - self._time_offset
        params["recvWindow"] = 5000
        query = urlencode(sorted(params.items()))
        signature = hmac.new(
            self._secret.encode(),
            query.encode(),
            hashlib.sha256,
        ).hexdigest()
        return f"{query}&signature={signature}"

    def _ensure_connected(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("BinanceAdapter not connected. Call connect() first.")
        return self._client

    @staticmethod
    def _running_loop() -> Optional[asyncio.AbstractEventLoop]:
        """当前 running loop（无则 None）。"""
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            return None

    async def _ensure_client_for_current_loop(self) -> None:
        """客户端绑定在别的 event loop 上时重建它。

        适配器可能在启动阶段的某个 loop 里 connect()，之后被 runner/consumer 任务在
        另一个 loop 中调用 → httpx/anyio 的 loop-bound 原语抛
        `RuntimeError: <asyncio.locks.Event ...> is bound to a different event loop`
        （2026-09-21 启动窗口实测：持仓同步/下单/RiskGuard 拉持仓全中招，靠 _reconnect
        自愈，约 20-40s 才恢复）。这里提前按 loop 重建，消除该窗口。
        仅当客户端由本适配器创建（记录了创建 loop）且与当前 loop 不同才重建；
        测试注入的替身（_client_loop is None）不碰。
        """
        loop = self._running_loop()
        if loop is None or self._client is None or self._client_loop is None:
            return
        if self._client_loop is loop:
            return
        logger.warning(
            "[Binance] HTTP client bound to another event loop; "
            "rebuilding for the current loop"
        )
        # 先建新客户端并换引用，旧的**延迟**关闭：重建当刻可能正有并发请求在用旧客户端，
        # 内联 aclose() 会让它们报 ClosedResourceError（2026-09-21 线上实测：SOL 同步被拦一轮）。
        old = self._client
        await self._create_client()
        task = asyncio.create_task(self._close_client_later(old))
        self._closing_tasks.add(task)
        task.add_done_callback(self._closing_tasks.discard)

    async def _close_client_later(self, client) -> None:
        """延迟关闭旧客户端：并发中的在途请求可能还在用它。"""
        await asyncio.sleep(self.CLIENT_CLOSE_GRACE_SECONDS)
        try:
            await client.aclose()
        except Exception as e:  # noqa: BLE001 - 关闭失败不影响新客户端
            logger.debug(f"deferred aclose failed: {type(e).__name__}: {e!r}")

    async def _reconnect(self) -> None:
        """丢弃并重建 HTTP 客户端（并重新同步时钟）。

        长活客户端在代理/网络抖动后可能永久卡死（2026-09-20 事故：每个请求都 httpx 超时、
        连续约 10 小时 100% 失败，只能靠重启进程恢复）。重建是唯一的自愈手段。
        """
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception as e:  # noqa: BLE001 - 关闭失败不应阻断重建
                logger.debug(f"Failed to close binance client before reconnect: {e!r}")
        self._client = None
        await self.connect()

    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        *,
        signed: bool = True,
        retry_transport: bool = True,
        **kwargs,
    ) -> httpx.Response:
        """所有请求的统一出口：传输层自愈 + 时间戳失效自愈。

        1. httpx.TransportError（连接/读写/池超时等）→ 重建客户端后重试一次。
           注意：httpx 这些异常的 message 为空，日志必须带类型名，否则只剩空消息。
           `retry_transport=False`（下单用）：只有"发送前"错误才重发，
           含糊错误（读超时/协议错误）直接抛给调用方去对账——避免重复下单。
        2. 响应为 -1021（timestamp 超出 recvWindow）→ 重新同步时钟后重试一次
           （重试会重新签名，拿到的是校正后的 timestamp。被拒=未受理，对下单也安全）。
        有界重试：每种情况最多一次，仍失败则抛出，由调用方处理。
        """

        async def attempt() -> httpx.Response:
            await self._ensure_client_for_current_loop()
            client = self._ensure_connected()
            target = f"{path}?{self._sign(params or {})}" if signed else path
            # 用 getattr 分发到 get/post/delete（而非 client.request）：与既有调用风格、
            # 既有测试的 mock 断言保持一致，自愈改造不动其他任何地方。
            send = getattr(client, method.lower())
            return await send(target, **kwargs)

        try:
            resp = await attempt()
        except _EGRESS_ERRORS as e:
            if not retry_transport and not isinstance(e, _PRE_SEND_ERRORS):
                # 请求可能已被交易所受理（响应丢失）→ 重发会造成重复下单，交给调用方对账
                logger.warning(
                    f"Binance transport error on {method} {path} "
                    f"({type(e).__name__}: {e!r}); not resending "
                    "(request may have been accepted)"
                )
                raise
            logger.warning(
                f"Binance transport error on {method} {path} "
                f"({type(e).__name__}: {e!r}); reconnecting and retrying once"
            )
            await self._reconnect()
            resp = await attempt()

        if resp.status_code == 400 and "-1021" in (resp.text or ""):
            logger.warning(
                f"Binance -1021 on {method} {path} (timestamp outside recvWindow); "
                "re-syncing clock and retrying once"
            )
            await self._sync_time()
            resp = await attempt()

        return resp

    async def find_order_by_client_id(
        self, client_order_id: str, symbol: str
    ) -> Optional[dict]:
        """按 clientOrderId 查交易所侧真实订单（"请求发出但响应丢失"时的对账手段）。

        返回订单原始 dict；订单确实不存在（-2013）返回 None；
        查询本身失败也返回 None（调用方据此保守处理：不宣称成功）。
        """
        try:
            resp = await self._request(
                "GET",
                "/fapi/v1/order",
                {
                    "symbol": symbol.upper().replace("/", ""),
                    "origClientOrderId": client_order_id,
                },
            )
        except httpx.TransportError as e:
            logger.warning(
                f"Binance order lookup by clientOrderId failed "
                f"({type(e).__name__}: {e!r}); 无法确认订单状态"
            )
            return None

        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 400:
            try:
                data = resp.json()
            except Exception:  # noqa: BLE001
                data = {}
            if data.get("code") == -2013:  # Order does not exist
                return None
        logger.warning(
            f"Binance order lookup by clientOrderId unexpected: "
            f"{resp.status_code} - {(resp.text or '')[:200]}"
        )
        return None

    def _order_response_from_raw(self, data: dict) -> OrderResponse:
        """交易所订单 dict → OrderResponse（下单与对账共用，保持与原来一致的**原始**
        交易所状态；本地状态由 executor._map_exchange_status 统一映射）。"""
        return OrderResponse(
            exchange_order_id=str(data["orderId"]),
            status=data.get("status", ""),
            filled_qty=Decimal(data.get("executedQty", "0")),
            avg_price=Decimal(data["avgPrice"]) if data.get("avgPrice") else None,
            fee=None,
            raw=data,
        )

    async def _reconcile_order(
        self, request: OrderRequest, error: Optional[BaseException]
    ) -> Optional[OrderResponse]:
        """下单结果不明时按 clientOrderId 向交易所对账。

        查到 → 采用交易所真实状态（说明请求已被受理，避免"假失败"）；
        查不到/无幂等键/查询失败 → 返回 None（调用方按原错误处理，绝不臆造成功）。
        """
        if not request.client_order_id:
            logger.warning(
                "Binance 下单结果不明但没有 clientOrderId，无法对账（订单状态未知）"
                f"：{type(error).__name__ if error else 'duplicate-reject'}"
            )
            return None

        raw = await self.find_order_by_client_id(request.client_order_id, request.symbol)
        if raw is None:
            logger.warning(
                f"Binance 对账未命中 clientOrderId={request.client_order_id}"
                "（请求未被受理）"
            )
            return None

        logger.warning(
            f"Binance 对账命中 clientOrderId={request.client_order_id} → 采用交易所真实状态 "
            f"status={raw.get('status')} executedQty={raw.get('executedQty')} "
            f"avgPrice={raw.get('avgPrice')}"
        )
        return self._order_response_from_raw(raw)

    async def _ensure_leverage(self, symbol: str) -> None:
        """下单前确保已设置杠杆。每个交易对只需设置一次。"""
        if symbol in self._leverage_set:
            return
        resp = await self._request(
            "POST",
            "/fapi/v1/leverage",
            {"symbol": symbol, "leverage": self.DEFAULT_LEVERAGE},
        )
        if resp.status_code == 200:
            self._leverage_set.add(symbol)
            logger.info(f"Leverage set for {symbol}: {self.DEFAULT_LEVERAGE}x")
        else:
            logger.warning(f"Failed to set leverage for {symbol}: {resp.status_code} - {resp.text}")

    async def _load_symbol_rules(self) -> None:
        """拉取并缓存 exchangeInfo 精度规则（stepSize / tickSize / minQty）。"""
        try:
            resp = await self._request("GET", "/fapi/v1/exchangeInfo", signed=False)
            if resp.status_code != 200:
                logger.error(
                    f"exchangeInfo fetch failed: {resp.status_code} - {resp.text[:200]}"
                )
                raise RuntimeError(
                    f"exchangeInfo fetch failed: {resp.status_code}"
                )
            data = resp.json()
            rules: dict[str, dict] = {}
            for s in data.get("symbols", []):
                symbol = s.get("symbol", "")
                step_size = "1"
                tick_size = "0.01"
                min_qty = "0"
                for f in s.get("filters", []):
                    ftype = f.get("filterType")
                    if ftype == "LOT_SIZE":
                        step_size = f.get("stepSize", "1")
                        min_qty = f.get("minQty", "0")
                    elif ftype == "PRICE_FILTER":
                        tick_size = f.get("tickSize", "0.01")
                rules[symbol] = {
                    "stepSize": Decimal(step_size),
                    "tickSize": Decimal(tick_size),
                    "minQty": Decimal(min_qty),
                }
            self._symbol_rules = rules
            logger.info(f"exchangeInfo cached for {len(rules)} symbols")
        except Exception as e:
            # 不可静默吞掉：规则缺失会让数量以未归一化形态直发交易所（-1111）。
            logger.error(
                f"Failed to load exchangeInfo precision rules "
                f"({type(e).__name__}: {e!r})"
            )
            raise

    def _normalize_quantity(self, symbol: str, quantity: Decimal) -> Decimal:
        """按 LOT_SIZE stepSize 向下取整数量，避免 -1111 精度错误。"""
        if self._symbol_rules and symbol in self._symbol_rules:
            step = self._symbol_rules[symbol]["stepSize"]
            if step and step > 0:
                q = (quantity / step).to_integral_value(rounding=ROUND_DOWN) * step
                # 归一化指数位数，避免 0.01000000 之类超出精度的表示
                q = q.quantize(step)
                return q
        return quantity

    def _validate_quantity(self, symbol: str, quantity: Decimal) -> None:
        """校验归一化后的数量不为 0 且不低于 minQty，提前暴露下单参数错误。"""
        if quantity <= 0:
            raise ValueError(
                f"quantity {quantity} for {symbol} rounds to zero "
                f"below stepSize (use a larger order)"
            )
        if self._symbol_rules and symbol in self._symbol_rules:
            min_qty = self._symbol_rules[symbol]["minQty"]
            if min_qty and min_qty > 0 and quantity < min_qty:
                raise ValueError(
                    f"quantity {quantity} for {symbol} below minQty {min_qty}"
                )

    def _normalize_price(self, symbol: str, price: Decimal) -> Decimal:
        """按 PRICE_FILTER tickSize 归一化价格。"""
        if self._symbol_rules and symbol in self._symbol_rules:
            tick = self._symbol_rules[symbol]["tickSize"]
            if tick and tick > 0:
                return price.quantize(tick, rounding=ROUND_HALF_UP)
        return price

    async def place_order(self, request: OrderRequest) -> OrderResponse:
        symbol = request.symbol.upper().replace("/", "")

        # 首次下单前加载交易所精度规则（懒加载 + 缓存）
        if self._symbol_rules is None:
            await self._load_symbol_rules()

        # 精度规则不可用 → 无法保证数量/价格合法：拒绝下单。
        # 2026-09-21 实测：加载失败被静默吞掉 → _normalize_quantity 原样返回 →
        # 5332.37953400 直发 DOGEUSDT（stepSize=1）→ 交易所 -1111。
        if not self._symbol_rules or symbol not in self._symbol_rules:
            raise RuntimeError(
                f"exchangeInfo 精度规则不可用（symbol={symbol}）"
                "：拒绝下单以避免 -1111 精度错误"
            )

        # 精度归一化：数量按 stepSize 向下取整，价格按 tickSize 对齐
        quantity = self._normalize_quantity(symbol, request.quantity)
        price = self._normalize_price(symbol, request.price) if request.price is not None else None
        self._validate_quantity(symbol, quantity)

        # 下单前设置杠杆（Futures 必须）
        await self._ensure_leverage(symbol)

        params = {
            "symbol": symbol,
            "side": request.side.upper(),
            "type": request.order_type.upper(),
            "quantity": format(quantity, "f"),
        }
        if price is not None:
            params["price"] = format(price, "f")
            params["timeInForce"] = "GTC"
        if request.client_order_id:
            params["newClientOrderId"] = request.client_order_id
        if request.stop_loss:
            params["stopPrice"] = str(request.stop_loss)
            params["workType"] = "STOP"

        try:
            resp = await self._request(
                "POST", "/fapi/v1/order", params, retry_transport=False
            )
        except _AMBIGUOUS_ORDER_ERRORS as e:
            # 请求可能已被受理但响应丢失：绝不重发，先按 clientOrderId 对账
            reconciled = await self._reconcile_order(request, e)
            if reconciled is not None:
                return reconciled
            raise

        if resp.status_code >= 400:
            error_detail = resp.text or ""
            if any(marker in error_detail for marker in _DUPLICATE_ORDER_MARKERS):
                # 单号重复 = 前一次其实已受理（幂等命中），取交易所真实状态而非当作失败
                reconciled = await self._reconcile_order(request, None)
                if reconciled is not None:
                    return reconciled
            logger.error(f"Binance API error: {resp.status_code} - {error_detail}")
            # 抛出包含 Binance 错误详情的异常
            raise RuntimeError(f"Binance API {resp.status_code}: {error_detail}")

        return self._order_response_from_raw(resp.json())

    # Binance 订单状态 → 本地 Order.status 映射
    _STATUS_MAP = {
        "NEW": "submitted",
        "PARTIALLY_FILLED": "partial",
        "FILLED": "filled",
        "CANCELED": "cancelled",
        "EXPIRED": "cancelled",
        "EXPIRED_IN_MATCH": "cancelled",
        "REJECTED": "failed",
    }

    async def cancel_order(self, exchange_order_id: str, symbol: str) -> bool:
        resp = await self._request(
            "DELETE",
            "/fapi/v1/order",
            {"symbol": symbol.upper(), "orderId": exchange_order_id},
        )
        return resp.status_code == 200

    async def fetch_order(
        self, exchange_order_id: str, symbol: str
    ) -> OrderFill:
        """查询订单当前状态与成交情况（/fapi/v1/order）。"""
        resp = await self._request(
            "GET",
            "/fapi/v1/order",
            {
                "symbol": symbol.upper().replace("/", ""),
                "orderId": str(exchange_order_id),
            },
        )

        if resp.status_code == 400:
            data = resp.json()
            if data.get("code") == -2013:  # Order does not exist
                raise OrderNotFoundError(
                    f"Order {exchange_order_id} does not exist on exchange"
                )
            raise RuntimeError(
                f"Binance API {resp.status_code}: {resp.text[:300]}"
            )
        resp.raise_for_status()

        data = resp.json()
        status = self._STATUS_MAP.get(data.get("status"), "submitted")
        filled_qty = Decimal(data.get("executedQty", "0"))
        avg_price = (
            Decimal(data["avgPrice"]) if data.get("avgPrice") else None
        )
        if status == "submitted" and filled_qty > 0:
            status = "partial"

        return OrderFill(
            status=status,
            filled_quantity=filled_qty,
            avg_fill_price=avg_price,
            error_message=None,
        )

    async def get_positions(self) -> list[Position]:
        resp = await self._request("GET", "/fapi/v2/positionRisk")
        resp.raise_for_status()

        positions = []
        for p in resp.json():
            if float(p["positionAmt"]) == 0:
                continue
            positions.append(
                Position(
                    symbol=p["symbol"],
                    side="long" if float(p["positionAmt"]) > 0 else "short",
                    quantity=abs(Decimal(p["positionAmt"])),
                    entry_price=Decimal(p["entryPrice"]),
                    unrealized_pnl=Decimal(p["unRealizedProfit"]),
                    leverage=int(p["leverage"]),
                    mark_price=(
                        Decimal(p["markPrice"]) if p.get("markPrice") else None
                    ),
                )
            )
        return positions

    async def get_balance(self) -> dict[str, Decimal]:
        """获取账户余额（USDS-M Futures）。

        /fapi/v2/balance 返回格式: [{asset, balance, walletBalance, ...}]
        Demo 环境使用 v2 端点。
        """
        resp = await self._request("GET", "/fapi/v2/balance")

        if resp.status_code != 200:
            logger.error(f"Binance balance API error: {resp.status_code} - {resp.text}")
            resp.raise_for_status()

        data = resp.json()

        # v2 返回数组格式: [{asset, balance, walletBalance, ...}]
        if isinstance(data, list):
            result = {}
            for b in data:
                asset = b.get("asset", "USDT")
                balance_val = b.get("balance") or b.get("walletBalance") or "0"
                result[asset] = Decimal(balance_val)
            return result

        # 兼容对象格式
        balance_info = data.get("balance", {})
        return {"USDT": Decimal(balance_info.get("walletBalance", "0"))}
