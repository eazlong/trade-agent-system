"""
Binance Futures 交易所适配器。

使用原生 httpx 调用 Binance USDM Futures API (fapi.binance.com)。
签名算法: HMAC SHA256。
"""

from __future__ import annotations

import hashlib
import hmac
import time
from decimal import Decimal
from typing import Optional
from urllib.parse import urlencode

import httpx
from django.conf import settings

from .base import BaseExchangeAdapter, OrderRequest, OrderResponse, Position

# Futures API base URL
_BASE_URL = 'https://fapi.binance.com'


class BinanceAdapter(BaseExchangeAdapter):
    """
    Binance USD-M Futures 适配器。

    API 文档: https://developers.binance.com/docs/derivatives/usds-margined-futures
    """

    BASE_URL = _BASE_URL
    TIMEOUT = 10.0

    def __init__(self, api_key: str, secret: str, testnet: bool = False):
        super().__init__(api_key, secret)
        if testnet:
            self.BASE_URL = 'https://testnet.binancefuture.com'
        self._client: Optional[httpx.AsyncClient] = None

    async def connect(self) -> None:
        headers = {'X-MBX-APIKEY': self._api_key}
        proxy = getattr(settings, 'WEB_PROXY', '') or None
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers=headers,
            timeout=self.TIMEOUT,
            proxy=proxy if proxy else None,
        )

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    def _sign(self, params: dict) -> dict:
        """HMAC SHA256 签名"""
        params = dict(params)
        params['timestamp'] = int(time.time() * 1000)
        params['recvWindow'] = 5000
        query = urlencode(sorted(params.items()))
        signature = hmac.new(
            self._secret.encode(),
            query.encode(),
            hashlib.sha256,
        ).hexdigest()
        params['signature'] = signature
        return params

    def _ensure_connected(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError('BinanceAdapter not connected. Call connect() first.')
        return self._client

    async def place_order(self, request: OrderRequest) -> OrderResponse:
        client = self._ensure_connected()

        params = {
            'symbol': request.symbol.upper(),
            'side': request.side.upper(),
            'type': request.order_type.upper(),
            'quantity': str(request.quantity),
        }
        if request.price is not None:
            params['price'] = str(request.price)
            params['timeInForce'] = 'GTC'
        if request.client_order_id:
            params['newClientOrderId'] = request.client_order_id
        if request.stop_loss:
            params['stopPrice'] = str(request.stop_loss)
            params['workType'] = 'STOP'

        params = self._sign(params)
        resp = await client.post('/fapi/v1/order', data=params)
        resp.raise_for_status()
        data = resp.json()

        return OrderResponse(
            exchange_order_id=str(data['orderId']),
            status=data['status'],
            filled_qty=Decimal(data.get('executedQty', '0')),
            avg_price=Decimal(data['avgPrice']) if data.get('avgPrice') else None,
            fee=None,
            raw=data,
        )

    async def cancel_order(self, exchange_order_id: str, symbol: str) -> bool:
        client = self._ensure_connected()

        params = self._sign({
            'symbol': symbol.upper(),
            'orderId': exchange_order_id,
        })
        resp = await client.delete('/fapi/v1/order', data=params)
        return resp.status_code == 200

    async def get_positions(self) -> list[Position]:
        client = self._ensure_connected()

        params = self._sign({})
        resp = await client.get('/fapi/v2/positionRisk', params=params)
        resp.raise_for_status()

        positions = []
        for p in resp.json():
            if float(p['positionAmt']) == 0:
                continue
            positions.append(Position(
                symbol=p['symbol'],
                side='long' if float(p['positionAmt']) > 0 else 'short',
                quantity=abs(Decimal(p['positionAmt'])),
                entry_price=Decimal(p['entryPrice']),
                unrealized_pnl=Decimal(p['unRealizedProfit']),
                leverage=int(p['leverage']),
            ))
        return positions

    async def get_balance(self) -> dict[str, Decimal]:
        client = self._ensure_connected()

        params = self._sign({'accountType': 'UMFUTURE'})
        resp = await client.get('/fapi/v2/balance', params=params)
        resp.raise_for_status()

        return {
            b['asset']: Decimal(b['balance'])
            for b in resp.json()
        }
