"""
OKX 交易所适配器。

使用原生 httpx 调用 OKX Trade API (www.okx.com)。
签名算法: HMAC SHA256 + BASE64。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from decimal import Decimal
from typing import Optional

import httpx
from django.conf import settings

from .base import BaseExchangeAdapter, OrderRequest, OrderResponse, Position

_BASE_URL = 'https://www.okx.com'


class OKXAdapter(BaseExchangeAdapter):
    """
    OKX 适配器。

    API 文档: https://www.okx.com/docs-v5/zh/
    """

    BASE_URL = _BASE_URL
    TIMEOUT = 10.0

    def __init__(self, api_key: str, secret: str, passphrase: str, testnet: bool = False):
        super().__init__(api_key, secret)
        self._passphrase = passphrase
        if testnet:
            self.BASE_URL = 'https://www.okx.com'
            # OKX testnet uses different endpoints
        self._client: Optional[httpx.AsyncClient] = None
        self._timestamp = ''

    async def connect(self) -> None:
        proxy = getattr(settings, 'WEB_PROXY', '') or None
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            timeout=self.TIMEOUT,
            proxy=proxy if proxy else None,
        )

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    def _sign(self, timestamp: str, method: str, path: str, body: str = '') -> str:
        """HMAC SHA256 + BASE64 签名"""
        message = timestamp + method + path + body
        mac = hmac.new(
            self._secret.encode(),
            message.encode(),
            hashlib.sha256,
        )
        return base64.b64encode(mac.digest()).decode()

    def _headers(self, method: str, path: str, body: str = '') -> dict:
        timestamp = time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime())
        signature = self._sign(timestamp, method, path, body)
        return {
            'OKX-API-KEY': self._api_key,
            'OKX-SIGNATURE': signature,
            'OKX-TIMESTAMP': timestamp,
            'OKX-PASSPHRASE': self._passphrase,
            'OKX-COIN-EXCHANGE': '1',  # 永续合约
        }

    def _ensure_connected(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError('OKXAdapter not connected. Call connect() first.')
        return self._client

    async def place_order(self, request: OrderRequest) -> OrderResponse:
        client = self._ensure_connected()

        body = {
            'instId': request.symbol.upper(),
            'tdMode': 'cross',          # 全仓模式
            'side': request.side.lower(),
            'ordType': request.order_type.lower(),
            'sz': str(request.quantity),
        }
        if request.price is not None:
            body['px'] = str(request.price)
        if request.client_order_id:
            body['clOrdId'] = request.client_order_id
        if request.stop_loss:
            body['slTriggerPx'] = str(request.stop_loss)

        path = '/api/v5/trade/order'
        headers = self._headers('POST', path, str(body))
        resp = await client.post(path, json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        if data.get('code') != '0':
            raise RuntimeError(f"OKX order failed: {data.get('msg')}")

        order = data['data'][0]
        return OrderResponse(
            exchange_order_id=order['ordId'],
            status=order['state'],
            filled_qty=Decimal(order.get('filledSz', '0')),
            avg_price=Decimal(order['avgPx']) if order.get('avgPx') else None,
            fee=None,
            raw=order,
        )

    async def cancel_order(self, exchange_order_id: str, symbol: str) -> bool:
        client = self._ensure_connected()

        body = {
            'instId': symbol.upper(),
            'ordId': exchange_order_id,
        }
        path = '/api/v5/trade/cancel-order'
        headers = self._headers('POST', path, str(body))
        resp = await client.post(path, json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return data.get('code') == '0'

    async def get_positions(self) -> list[Position]:
        client = self._ensure_connected()

        path = '/api/v5/account/positions'
        headers = self._headers('GET', path)
        resp = await client.get(path, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        positions = []
        for p in data.get('data', []):
            if Decimal(p.get('pos', '0')) == 0:
                continue
            positions.append(Position(
                symbol=p['instId'],
                side='long' if Decimal(p['pos']) > 0 else 'short',
                quantity=abs(Decimal(p['pos'])),
                entry_price=Decimal(p['avgEntryPx']),
                unrealized_pnl=Decimal(p['upl']),
                leverage=int(p.get('lever', '1')),
            ))
        return positions

    async def get_balance(self) -> dict[str, Decimal]:
        client = self._ensure_connected()

        path = '/api/v5/account/balance'
        headers = self._headers('GET', path)
        resp = await client.get(path, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        balances = {}
        for detail in data.get('data', [{}])[0].get('details', []):
            for asset, balance in detail.items():
                if asset in ('totalEq', 'mmr', 'upl', 'liab', 'details'):
                    continue
                if balance and Decimal(balance) != 0:
                    balances[asset] = Decimal(balance)
        return balances
