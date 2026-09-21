"""
Binance Futures 交易所适配器。

使用原生 httpx 调用 Binance USDM Futures API (fapi.binance.com)。
签名算法: HMAC SHA256。
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Optional
from urllib.parse import urlencode

import httpx
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


class BinanceAdapter(BaseExchangeAdapter):
    """
    Binance USD-M Futures 适配器。

    API 文档: https://developers.binance.com/docs/derivatives/usds-margined-futures
    """

    BASE_URL = _BASE_URL
    TIMEOUT = 10.0
    DEFAULT_LEVERAGE = 10  # 默认杠杆倍数

    def __init__(self, api_key: str, secret: str, testnet: bool = False):
        super().__init__(api_key, secret)
        if testnet:
            self.BASE_URL = "https://demo-fapi.binance.com"
        self._client: Optional[httpx.AsyncClient] = None
        self._time_offset: int = 0  # ms: local_time = server_time + offset
        self._leverage_set: set[str] = set()  # 已设置杠杆的交易对
        self._symbol_rules: Optional[dict[str, dict]] = None  # exchangeInfo 精度规则缓存

    async def connect(self) -> None:
        headers = {"X-MBX-APIKEY": self._api_key}
        proxy = getattr(settings, "WEB_PROXY", "") or None
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers=headers,
            timeout=self.TIMEOUT,
            proxy=proxy if proxy else None,
        )
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
        **kwargs,
    ) -> httpx.Response:
        """所有请求的统一出口：传输层自愈 + 时间戳失效自愈。

        1. httpx.TransportError（连接/读写/池超时等）→ 重建客户端后重试一次。
           注意：httpx 这些异常的 message 为空，日志必须带类型名，否则只剩空消息。
        2. 响应为 -1021（timestamp 超出 recvWindow）→ 重新同步时钟后重试一次
           （重试会重新签名，拿到的是校正后的 timestamp）。
        有界重试：每种情况最多一次，仍失败则抛出，由调用方处理。
        """

        async def attempt() -> httpx.Response:
            client = self._ensure_connected()
            target = f"{path}?{self._sign(params or {})}" if signed else path
            # 用 getattr 分发到 get/post/delete（而非 client.request）：与既有调用风格、
            # 既有测试的 mock 断言保持一致，自愈改造不动其他任何地方。
            send = getattr(client, method.lower())
            return await send(target, **kwargs)

        try:
            resp = await attempt()
        except httpx.TransportError as e:
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
                logger.warning(
                    f"exchangeInfo fetch failed: {resp.status_code} - {resp.text[:200]}"
                )
                return
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
            logger.warning(f"Failed to load exchangeInfo precision rules: {e}")

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

        resp = await self._request("POST", "/fapi/v1/order", params)

        if resp.status_code >= 400:
            error_detail = resp.text
            logger.error(f"Binance API error: {resp.status_code} - {error_detail}")
            # 抛出包含 Binance 错误详情的异常
            raise RuntimeError(f"Binance API {resp.status_code}: {error_detail}")

        data = resp.json()

        return OrderResponse(
            exchange_order_id=str(data["orderId"]),
            status=data["status"],
            filled_qty=Decimal(data.get("executedQty", "0")),
            avg_price=Decimal(data["avgPrice"]) if data.get("avgPrice") else None,
            fee=None,
            raw=data,
        )

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
