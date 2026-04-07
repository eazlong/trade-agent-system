"""
OKX 数据源

支持：
- WebSocket 实时数据（K线、成交、深度）
- REST API 历史数据
- 现货和合约市场
"""
import asyncio
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Callable
import aiohttp
import ccxt.async_support as ccxt

from apps.datasource.base import (
    BaseDataSource, DataType, KlineInterval, MarketType,
    ConnectionStatus
)
from apps.datasource.registry import DataSourceRegistry
from apps.datasource.store import get_data_store
from apps.datasource.monitor import get_quality_monitor


@DataSourceRegistry.register('okx')
class OKXDataSource(BaseDataSource):
    """
    OKX 数据源

    WebSocket 端点:
    - 公共: wss://ws.okx.com:8443/ws/v5/public
    - 私有: wss://ws.okx.com:8443/ws/v5/private

    REST API 端点:
    - https://www.okx.com
    """

    name = 'okx'
    source_type = 'crypto'

    supported_data_types = [
        DataType.KLINE,
        DataType.TICKER,
        DataType.TRADE,
        DataType.DEPTH,
    ]

    supported_market_types = [
        MarketType.SPOT,
        MarketType.FUTURES,
    ]

    supported_intervals = [
        KlineInterval.M1, KlineInterval.M3, KlineInterval.M5,
        KlineInterval.M15, KlineInterval.M30, KlineInterval.H1,
        KlineInterval.H4, KlineInterval.D1, KlineInterval.W1,
    ]

    ws_endpoint = 'wss://ws.okx.com:8443/ws/v5/public'
    rest_endpoint = 'https://www.okx.com'

    def __init__(self):
        """初始化"""
        super().__init__()

        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._http_client: Optional[aiohttp.ClientSession] = None
        self._ccxt: Optional[ccxt.okx] = None
        self._ws_task: Optional[asyncio.Task] = None

        self._store = get_data_store()
        self._monitor = get_quality_monitor()

    async def connect_websocket(self) -> bool:
        """建立 WebSocket 连接"""
        try:
            self._ws_status = ConnectionStatus.CONNECTING

            if self._http_client is None:
                self._http_client = aiohttp.ClientSession()

            self._ws = await self._http_client.ws_connect(
                self.ws_endpoint,
                heartbeat=25,
                receive_timeout=30
            )

            self._ws_task = asyncio.create_task(self._ws_receiver())

            self._ws_status = ConnectionStatus.CONNECTED
            self._connected_at = datetime.now()

            await self._resubscribe_all()

            return True

        except Exception as e:
            self._ws_status = ConnectionStatus.ERROR
            print(f"OKX WebSocket connection error: {e}")
            return False

    async def disconnect_websocket(self) -> bool:
        """断开 WebSocket 连接"""
        try:
            if self._ws_task:
                self._ws_task.cancel()
                try:
                    await self._ws_task
                except asyncio.CancelledError:
                    pass

            if self._ws and not self._ws.closed:
                await self._ws.close()
            self._ws = None

            self._ws_status = ConnectionStatus.DISCONNECTED
            return True

        except Exception as e:
            print(f"OKX WebSocket disconnect error: {e}")
            return False

    async def _ws_receiver(self) -> None:
        """WebSocket 消息接收"""
        while self._ws and not self._ws.closed:
            try:
                msg = await self._ws.receive()
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    await self._handle_websocket_message(data)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    print(f"OKX WebSocket error: {self._ws.exception()}")
                    break
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    break

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"OKX WebSocket receive error: {e}")
                await asyncio.sleep(1)

    async def _handle_websocket_message(self, msg: Dict) -> None:
        """处理 WebSocket 消息"""
        receive_time = datetime.now()
        self._last_data_time = time.time()

        # OKX 消息格式: {"arg": {...}, "data": [...]}
        arg = msg.get('arg', {})
        channel = arg.get('channel', '')
        inst_id = arg.get('instId', '')

        data_list = msg.get('data', [])
        if not data_list:
            return

        for raw_data in data_list:
            if 'candle' in channel or 'candle1m' in channel:
                # K线数据
                kline = self.normalize_kline({'arg': arg, 'data': raw_data})
                self._store.store('kline', inst_id, kline, self.name)
                self._monitor.record_data(self.name, inst_id, 'kline', kline.get('timestamp', receive_time), receive_time)
                self._trigger_callbacks(DataType.KLINE, kline)

            elif 'trades' in channel:
                # 成交数据
                trade = self.normalize_trade({'arg': arg, 'data': raw_data})
                self._store.store('trade', inst_id, trade, self.name)
                self._monitor.record_data(self.name, inst_id, 'trade', trade.get('timestamp', receive_time), receive_time)
                self._trigger_callbacks(DataType.TRADE, trade)

            elif 'tickers' in channel:
                # 行情数据
                ticker = self.normalize_ticker({'arg': arg, 'data': raw_data})
                self._store.store('ticker', inst_id, ticker, self.name)
                self._monitor.record_data(self.name, inst_id, 'ticker', ticker.get('timestamp', receive_time), receive_time)
                self._trigger_callbacks(DataType.TICKER, ticker)

    async def _resubscribe_all(self) -> None:
        """重新发送所有订阅"""
        for sub_info in self._subscriptions.values():
            await self._send_subscribe(sub_info)

    async def _send_subscribe(self, sub_info: Dict) -> None:
        """发送订阅请求"""
        if self._ws is None or self._ws.closed:
            return

        symbol = sub_info['symbol'].replace('/', '-')
        data_type = sub_info['data_type']
        interval = sub_info.get('interval')
        market_type = sub_info.get('market_type', MarketType.SPOT)

        # 构建订阅参数
        channel = self._build_channel(data_type, interval)
        inst_type = 'SPOT' if market_type == MarketType.SPOT else 'SWAP'

        msg = {
            "op": "subscribe",
            "args": [{
                "channel": channel,
                "instId": symbol,
                "instType": inst_type
            }]
        }

        await self._ws.send_str(json.dumps(msg))

    def _build_channel(self, data_type: DataType, interval: Optional[KlineInterval] = None) -> str:
        """构建订阅频道"""
        if data_type == DataType.KLINE:
            interval_str = interval.value if interval else '1m'
            return f"candle{interval_str}"
        elif data_type == DataType.TRADE:
            return "trades"
        elif data_type == DataType.TICKER:
            return "tickers"
        elif data_type == DataType.DEPTH:
            return "books5"
        return ""

    async def fetch_klines(
        self,
        symbol: str,
        interval: KlineInterval,
        market_type: MarketType = MarketType.SPOT,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 1000
    ) -> List[Dict]:
        """获取历史 K 线数据"""
        try:
            if self._ccxt is None:
                self._ccxt = ccxt.okx({'enableRateLimit': True})

            inst_id = symbol.replace('/', '-')
            bar = interval.value

            params = {
                'instId': inst_id,
                'bar': bar,
                'limit': min(limit, 300),
            }

            if start_time:
                params['before'] = int(start_time.timestamp() * 1000)
            if end_time:
                params['after'] = int(end_time.timestamp() * 1000)

            url = f"{self.rest_endpoint}/api/v5/market/candles"

            if self._http_client is None:
                self._http_client = aiohttp.ClientSession()

            async with self._http_client.get(url, params=params) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    data = result.get('data', [])
                    klines = [self.normalize_kline_rest(k, symbol, interval) for k in data]
                    self._store.store_batch('kline', symbol, klines, self.name)
                    return klines
                else:
                    raise Exception(f"API error: {resp.status}")

        except Exception as e:
            print(f"OKX fetch_klines error: {e}")
            return []

    async def fetch_trades(
        self,
        symbol: str,
        market_type: MarketType = MarketType.SPOT,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 1000
    ) -> List[Dict]:
        """获取历史成交数据"""
        try:
            inst_id = symbol.replace('/', '-')
            url = f"{self.rest_endpoint}/api/v5/market/trades"
            params = {'instId': inst_id, 'limit': min(limit, 100)}

            if self._http_client is None:
                self._http_client = aiohttp.ClientSession()

            async with self._http_client.get(url, params=params) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    data = result.get('data', [])
                    trades = [self.normalize_trade_rest(t, symbol) for t in data]
                    self._store.store_batch('trade', symbol, trades, self.name)
                    return trades
                else:
                    raise Exception(f"API error: {resp.status}")

        except Exception as e:
            print(f"OKX fetch_trades error: {e}")
            return []

    async def fetch_ticker(
        self,
        symbol: str,
        market_type: MarketType = MarketType.SPOT
    ) -> Dict:
        """获取实时行情快照"""
        try:
            inst_id = symbol.replace('/', '-')
            url = f"{self.rest_endpoint}/api/v5/market/ticker"
            params = {'instId': inst_id}

            if self._http_client is None:
                self._http_client = aiohttp.ClientSession()

            async with self._http_client.get(url, params=params) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    data = result.get('data', [])
                    if data:
                        ticker = self.normalize_ticker_rest(data[0], symbol)
                        self._store.store('ticker', symbol, ticker, self.name)
                        return ticker
                return {}

        except Exception as e:
            print(f"OKX fetch_ticker error: {e}")
            return {}

    async def subscribe(
        self,
        symbol: str,
        data_type: DataType,
        interval: Optional[KlineInterval] = None,
        market_type: MarketType = MarketType.SPOT,
        callback: Optional[Callable] = None
    ) -> bool:
        """订阅数据"""
        if callback:
            self.register_callback(data_type, callback)

        sub_key = f"{symbol}:{data_type.value}:{interval.value if interval else 'none'}:{market_type.value}"
        self._subscriptions[sub_key] = {
            'symbol': symbol,
            'data_type': data_type,
            'interval': interval,
            'market_type': market_type,
            'callback': callback,
            'active': True,
        }

        if self._ws_status == ConnectionStatus.CONNECTED:
            await self._send_subscribe(self._subscriptions[sub_key])

        return True

    async def unsubscribe(
        self,
        symbol: str,
        data_type: DataType,
        interval: Optional[KlineInterval] = None,
        market_type: MarketType = MarketType.SPOT
    ) -> bool:
        """取消订阅"""
        sub_key = f"{symbol}:{data_type.value}:{interval.value if interval else 'none'}:{market_type.value}"

        if sub_key not in self._subscriptions:
            return False

        self._subscriptions[sub_key]['active'] = False

        if self._ws_status == ConnectionStatus.CONNECTED and self._ws and not self._ws.closed:
            symbol_fmt = symbol.replace('/', '-')
            channel = self._build_channel(data_type, interval)
            inst_type = 'SPOT' if market_type == MarketType.SPOT else 'SWAP'

            msg = {
                "op": "unsubscribe",
                "args": [{
                    "channel": channel,
                    "instId": symbol_fmt,
                    "instType": inst_type
                }]
            }
            await self._ws.send_str(json.dumps(msg))

        del self._subscriptions[sub_key]
        return True

    def normalize_kline(self, raw_data: Any) -> Dict:
        """标准化 WebSocket K 线数据"""
        arg = raw_data.get('arg', {})
        data = raw_data.get('data', {})

        ts = int(data.get('ts', 0))

        return {
            'symbol': arg.get('instId', '').replace('-', '/'),
            'interval': arg.get('channel', '').replace('candle', ''),
            'open_time': datetime.fromtimestamp(ts / 1000),
            'close_time': datetime.fromtimestamp((ts + 60000) / 1000),
            'open': float(data.get('o', 0)),
            'high': float(data.get('h', 0)),
            'low': float(data.get('l', 0)),
            'close': float(data.get('c', 0)),
            'volume': float(data.get('vol', 0)),
            'turnover': float(data.get('volCcy', 0)),
            'trades': 0,
            'source': self.name,
            'timestamp': datetime.fromtimestamp(ts / 1000),
        }

    def normalize_kline_rest(self, raw_data: Any, symbol: str, interval: KlineInterval) -> Dict:
        """标准化 REST API K 线数据"""
        # OKX 格式: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
        ts = int(raw_data[0])

        return {
            'symbol': symbol,
            'interval': interval.value,
            'open_time': datetime.fromtimestamp(ts / 1000),
            'close_time': datetime.fromtimestamp((ts + 60000) / 1000),
            'open': float(raw_data[1]),
            'high': float(raw_data[2]),
            'low': float(raw_data[3]),
            'close': float(raw_data[4]),
            'volume': float(raw_data[5]),
            'turnover': float(raw_data[6]),
            'trades': 0,
            'source': self.name,
            'timestamp': datetime.fromtimestamp(ts / 1000),
        }

    def normalize_trade(self, raw_data: Any) -> Dict:
        """标准化 WebSocket 成交数据"""
        data = raw_data.get('data', {})
        ts = int(data.get('ts', 0))

        return {
            'symbol': raw_data.get('arg', {}).get('instId', '').replace('-', '/'),
            'trade_id': str(data.get('tradeId', '')),
            'price': float(data.get('px', 0)),
            'quantity': float(data.get('sz', 0)),
            'side': data.get('side', '').lower(),
            'timestamp': datetime.fromtimestamp(ts / 1000),
            'source': self.name,
        }

    def normalize_trade_rest(self, raw_data: Any, symbol: str) -> Dict:
        """标准化 REST API 成交数据"""
        ts = int(raw_data.get('ts', 0))

        return {
            'symbol': symbol,
            'trade_id': str(raw_data.get('tradeId', '')),
            'price': float(raw_data.get('px', 0)),
            'quantity': float(raw_data.get('sz', 0)),
            'side': raw_data.get('side', '').lower(),
            'timestamp': datetime.fromtimestamp(ts / 1000),
            'source': self.name,
        }

    def normalize_ticker(self, raw_data: Any) -> Dict:
        """标准化 WebSocket 行情数据"""
        arg = raw_data.get('arg', {})
        data = raw_data.get('data', {})
        ts = int(data.get('ts', 0))

        return {
            'symbol': arg.get('instId', '').replace('-', '/'),
            'last_price': float(data.get('last', 0)),
            'bid_price': float(data.get('bidPx', 0)),
            'bid_quantity': float(data.get('bidSz', 0)),
            'ask_price': float(data.get('askPx', 0)),
            'ask_quantity': float(data.get('askSz', 0)),
            'high_24h': float(data.get('high24h', 0)),
            'low_24h': float(data.get('low24h', 0)),
            'volume_24h': float(data.get('vol24h', 0)),
            'turnover_24h': float(data.get('volCcy24h', 0)),
            'change_24h': 0,
            'change_pct_24h': float(data.get('change24h', 0)),
            'timestamp': datetime.fromtimestamp(ts / 1000),
            'source': self.name,
        }

    def normalize_ticker_rest(self, raw_data: Any, symbol: str) -> Dict:
        """标准化 REST API 行情数据"""
        ts = int(raw_data.get('ts', 0))

        return {
            'symbol': symbol,
            'last_price': float(raw_data.get('last', 0)),
            'bid_price': float(raw_data.get('bidPx', 0)),
            'bid_quantity': float(raw_data.get('bidSz', 0)),
            'ask_price': float(raw_data.get('askPx', 0)),
            'ask_quantity': float(raw_data.get('askSz', 0)),
            'high_24h': float(raw_data.get('high24h', 0)),
            'low_24h': float(raw_data.get('low24h', 0)),
            'volume_24h': float(raw_data.get('vol24h', 0)),
            'turnover_24h': float(raw_data.get('volCcy24h', 0)),
            'change_24h': 0,
            'change_pct_24h': float(raw_data.get('changeUtc24h', 0)),
            'timestamp': datetime.fromtimestamp(ts / 1000),
            'source': self.name,
        }

    def cleanup(self) -> None:
        """清理资源"""
        super().cleanup()

        if self._ccxt:
            asyncio.run_coroutine_threadsafe(self._ccxt.close(), asyncio.get_event_loop())
            self._ccxt = None

        if self._http_client:
            asyncio.run_coroutine_threadsafe(self._http_client.close(), asyncio.get_event_loop())
            self._http_client = None