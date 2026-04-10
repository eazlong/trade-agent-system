"""
Binance 数据源

支持：
- WebSocket 实时数据（K线、成交、深度）
- REST API 历史数据
- 现货和合约市场
"""
import asyncio
import json
import time
import threading
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


@DataSourceRegistry.register('binance')
class BinanceDataSource(BaseDataSource):
    """
    Binance 数据源

    WebSocket 端点:
    - 现货: wss://stream.binance.com:9443/ws
    - 合约: wss://fstream.binance.com/ws

    REST API 端点:
    - 现货: https://api.binance.com
    - 合约: https://fapi.binance.com
    """

    name = 'binance'
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
        KlineInterval.M1_MONTH
    ]

    ws_spot_endpoint = 'wss://stream.binance.com:9443/ws'
    ws_futures_endpoint = 'wss://fstream.binance.com/ws'

    rest_spot_endpoint = 'https://api.binance.com'
    rest_futures_endpoint = 'https://fapi.binance.com'

    # WebSocket ping 间隔（分钟）
    WS_PING_INTERVAL = 3

    def __init__(self):
        """初始化"""
        super().__init__()

        # WebSocket 连接
        self._ws_spot: Optional[aiohttp.ClientWebSocketResponse] = None
        self._ws_futures: Optional[aiohttp.ClientWebSocketResponse] = None

        # HTTP 客户端
        self._http_client: Optional[aiohttp.ClientSession] = None

        # CCXT 客户端（备用）
        self._ccxt_spot: Optional[ccxt.binance] = None
        self._ccxt_futures: Optional[ccxt.binanceusdm] = None

        # WebSocket 消息处理任务
        self._ws_tasks: List[asyncio.Task] = []

        # Ping 任务
        self._ping_task: Optional[asyncio.Task] = None

        # 数据存储
        self._store = get_data_store()

        # 质量监控
        self._monitor = get_quality_monitor()

    # ==================== WebSocket 连接 ====================

    async def connect_websocket(self) -> bool:
        """建立 WebSocket 连接（仅连接已配置的市场类型）"""
        try:
            self._ws_status = ConnectionStatus.CONNECTING

            # 获取需要连接的市场类型
            active_types = self._get_active_market_types()

            # 创建 HTTP 客户端
            if self._http_client is None:
                self._http_client = aiohttp.ClientSession()

            # 仅在配置了现货时连接现货 WebSocket
            if MarketType.SPOT in active_types:
                if self._ws_spot is None or self._ws_spot.closed:
                    self._ws_spot = await self._http_client.ws_connect(
                        self.ws_spot_endpoint,
                        heartbeat=self.WS_PING_INTERVAL * 60,
                        receive_timeout=30
                    )

            # 仅在配置了合约时连接合约 WebSocket
            if MarketType.FUTURES in active_types:
                if self._ws_futures is None or self._ws_futures.closed:
                    self._ws_futures = await self._http_client.ws_connect(
                        self.ws_futures_endpoint,
                        heartbeat=self.WS_PING_INTERVAL * 60,
                        receive_timeout=30
                    )

            # 只为活跃连接启动消息处理任务
            self._ws_tasks = []
            if MarketType.SPOT in active_types:
                self._ws_tasks.append(asyncio.create_task(self._ws_spot_receiver()))
            if MarketType.FUTURES in active_types:
                self._ws_tasks.append(asyncio.create_task(self._ws_futures_receiver()))

            self._ws_status = ConnectionStatus.CONNECTED
            self._connected_at = datetime.now()

            # 发送活跃订阅
            await self._resubscribe_all()

            return True

        except Exception as e:
            self._ws_status = ConnectionStatus.ERROR
            print(f"Binance WebSocket connection error: {e}")
            return False

    async def disconnect_websocket(self) -> bool:
        """断开 WebSocket 连接"""
        try:
            # 取消消息处理任务
            for task in self._ws_tasks:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            self._ws_tasks = []

            # 关闭 WebSocket 连接
            if self._ws_spot and not self._ws_spot.closed:
                await self._ws_spot.close()
            self._ws_spot = None

            if self._ws_futures and not self._ws_futures.closed:
                await self._ws_futures.close()
            self._ws_futures = None

            self._ws_status = ConnectionStatus.DISCONNECTED

            return True

        except Exception as e:
            print(f"Binance WebSocket disconnect error: {e}")
            return False

    async def _ws_spot_receiver(self) -> None:
        """现货 WebSocket 消息接收"""
        while self._ws_spot and not self._ws_spot.closed:
            try:
                msg = await self._ws_spot.receive()
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._handle_websocket_message(json.loads(msg.data), MarketType.SPOT)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    print(f"Spot WebSocket error: {self._ws_spot.exception()}")
                    break
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    print("Spot WebSocket closed")
                    break

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Spot WebSocket receive error: {e}")
                await asyncio.sleep(1)

    async def _ws_futures_receiver(self) -> None:
        """合约 WebSocket 消息接收"""
        while self._ws_futures and not self._ws_futures.closed:
            try:
                msg = await self._ws_futures.receive()
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._handle_websocket_message(json.loads(msg.data), MarketType.FUTURES)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    print(f"Futures WebSocket error: {self._ws_futures.exception()}")
                    break
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    print("Futures WebSocket closed")
                    break

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Futures WebSocket receive error: {e}")
                await asyncio.sleep(1)

    async def _handle_websocket_message(self, msg: Dict, market_type: MarketType) -> None:
        """处理 WebSocket 消息"""
        # 记录接收时间
        receive_time = datetime.now()
        self._last_data_time = time.time()

        # 解析消息类型
        event_type = msg.get('e')

        if event_type == 'kline':
            # K线数据
            kline = self.normalize_kline(msg)
            kline['market_type'] = market_type.value

            # 存储
            symbol = kline['symbol']
            self._store.store('kline', symbol, kline, self.name)

            # 监控
            self._monitor.record_data(
                self.name, symbol, 'kline',
                kline.get('timestamp', receive_time), receive_time
            )

            # 触发回调
            self._trigger_callbacks(DataType.KLINE, kline)

        elif event_type == 'trade' or event_type == 'aggTrade':
            # 成交数据
            trade = self.normalize_trade(msg)
            trade['market_type'] = market_type.value

            symbol = trade['symbol']
            self._store.store('trade', symbol, trade, self.name)

            self._monitor.record_data(
                self.name, symbol, 'trade',
                trade.get('timestamp', receive_time), receive_time
            )

            self._trigger_callbacks(DataType.TRADE, trade)

        elif event_type == '24hrTicker' or event_type == '24hrMiniTicker':
            # 行情快照
            ticker = self.normalize_ticker(msg)
            ticker['market_type'] = market_type.value

            symbol = ticker['symbol']
            self._store.store('ticker', symbol, ticker, self.name)

            self._monitor.record_data(
                self.name, symbol, 'ticker',
                ticker.get('timestamp', receive_time), receive_time
            )

            self._trigger_callbacks(DataType.TICKER, ticker)

    async def _resubscribe_all(self) -> None:
        """重新发送所有订阅"""
        for sub_key, sub_info in self._subscriptions.items():
            try:
                await self._send_subscribe(sub_info)
            except Exception as e:
                print(f"Resubscribe error for {sub_key}: {e}")

    async def _send_subscribe(self, sub_info: Dict) -> None:
        """发送订阅请求"""
        symbol = sub_info['symbol']
        data_type = sub_info['data_type']
        interval = sub_info.get('interval')
        market_type = sub_info.get('market_type', MarketType.SPOT)

        # 构建 WebSocket 消息
        ws = self._ws_spot if market_type == MarketType.SPOT else self._ws_futures

        if ws is None or ws.closed:
            return

        # 构建订阅参数
        streams = self._build_stream_names(symbol, data_type, interval)

        msg = {
            "method": "SUBSCRIBE",
            "params": streams,
            "id": int(time.time() * 1000)
        }

        await ws.send_str(json.dumps(msg))

    def _build_stream_names(
        self,
        symbol: str,
        data_type: DataType,
        interval: Optional[KlineInterval] = None
    ) -> List[str]:
        """构建 WebSocket stream 名称"""
        symbol_lower = symbol.lower().replace('/', '')

        if data_type == DataType.KLINE:
            interval_str = interval.value if interval else '1m'
            return [f"{symbol_lower}@kline_{interval_str}"]

        elif data_type == DataType.TRADE:
            return [f"{symbol_lower}@aggTrade"]

        elif data_type == DataType.TICKER:
            return [f"{symbol_lower}@ticker"]

        elif data_type == DataType.DEPTH:
            return [f"{symbol_lower}@depth@100ms"]

        return []

    # ==================== REST API ====================

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
            # 初始化 CCXT 客户端
            await self._init_ccxt(market_type)

            client = self._ccxt_spot if market_type == MarketType.SPOT else self._ccxt_futures

            # 参数
            params = {
                'symbol': symbol.replace('/', ''),  # Binance 格式
                'interval': interval.value,
                'limit': min(limit, 1000),
            }

            if start_time:
                params['startTime'] = int(start_time.timestamp() * 1000)
            if end_time:
                params['endTime'] = int(end_time.timestamp() * 1000)

            # API 端点
            endpoint = '/api/v3/klines' if market_type == MarketType.SPOT else '/fapi/v1/klines'

            # 发送请求
            url = f"{self.rest_spot_endpoint}{endpoint}" if market_type == MarketType.SPOT else f"{self.rest_futures_endpoint}{endpoint}"

            if self._http_client is None:
                self._http_client = aiohttp.ClientSession()

            async with self._http_client.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    klines = [self.normalize_kline_rest(k, symbol, interval) for k in data]

                    # 批量存储
                    self._store.store_batch('kline', symbol, klines, self.name)

                    return klines
                else:
                    error_text = await resp.text()
                    raise Exception(f"API error {resp.status}: {error_text}")

        except Exception as e:
            print(f"Binance fetch_klines error: {e}")
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
            endpoint = '/api/v3/trades' if market_type == MarketType.SPOT else '/fapi/v1/trades'

            url = f"{self.rest_spot_endpoint}{endpoint}" if market_type == MarketType.SPOT else f"{self.rest_futures_endpoint}{endpoint}"

            params = {
                'symbol': symbol.replace('/', ''),
                'limit': min(limit, 1000),
            }

            if self._http_client is None:
                self._http_client = aiohttp.ClientSession()

            async with self._http_client.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    trades = [self.normalize_trade_rest(t, symbol) for t in data]

                    self._store.store_batch('trade', symbol, trades, self.name)

                    return trades
                else:
                    error_text = await resp.text()
                    raise Exception(f"API error {resp.status}: {error_text}")

        except Exception as e:
            print(f"Binance fetch_trades error: {e}")
            return []

    async def fetch_ticker(
        self,
        symbol: str,
        market_type: MarketType = MarketType.SPOT
    ) -> Dict:
        """获取实时行情快照"""
        try:
            endpoint = '/api/v3/ticker/24hr' if market_type == MarketType.SPOT else '/fapi/v1/ticker/24hr'

            url = f"{self.rest_spot_endpoint}{endpoint}" if market_type == MarketType.SPOT else f"{self.rest_futures_endpoint}{endpoint}"

            params = {'symbol': symbol.replace('/', '')}

            if self._http_client is None:
                self._http_client = aiohttp.ClientSession()

            async with self._http_client.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    ticker = self.normalize_ticker_rest(data, symbol)

                    self._store.store('ticker', symbol, ticker, self.name)

                    return ticker
                else:
                    error_text = await resp.text()
                    raise Exception(f"API error {resp.status}: {error_text}")

        except Exception as e:
            print(f"Binance fetch_ticker error: {e}")
            return {}

    async def _init_ccxt(self, market_type: MarketType) -> None:
        """初始化 CCXT 客户端"""
        if market_type == MarketType.SPOT:
            if self._ccxt_spot is None:
                self._ccxt_spot = ccxt.binance({'enableRateLimit': True})
        else:
            if self._ccxt_futures is None:
                self._ccxt_futures = ccxt.binanceusdm({'enableRateLimit': True})

    # ==================== 数据订阅 ====================

    async def subscribe(
        self,
        symbol: str,
        data_type: DataType,
        interval: Optional[KlineInterval] = None,
        market_type: MarketType = MarketType.SPOT,
        callback: Optional[Callable] = None
    ) -> bool:
        """订阅数据"""
        # 注册回调
        if callback:
            self.register_callback(data_type, callback)

        # 生成订阅键
        sub_key = f"{symbol}:{data_type.value}:{interval.value if interval else 'none'}:{market_type.value}"

        # 存储订阅信息
        self._subscriptions[sub_key] = {
            'symbol': symbol,
            'data_type': data_type,
            'interval': interval,
            'market_type': market_type,
            'callback': callback,
            'active': True,
        }

        # 如果已连接，发送订阅
        if self._ws_status == ConnectionStatus.CONNECTED:
            try:
                await self._send_subscribe(self._subscriptions[sub_key])
                return True
            except Exception as e:
                print(f"Binance subscribe error: {e}")
                return False

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

        # 标记为非活跃
        self._subscriptions[sub_key]['active'] = False

        # 如果已连接，发送取消订阅
        if self._ws_status == ConnectionStatus.CONNECTED:
            try:
                ws = self._ws_spot if market_type == MarketType.SPOT else self._ws_futures
                if ws and not ws.closed:
                    streams = self._build_stream_names(symbol, data_type, interval)

                    msg = {
                        "method": "UNSUBSCRIBE",
                        "params": streams,
                        "id": int(time.time() * 1000)
                    }

                    await ws.send_str(json.dumps(msg))

            except Exception as e:
                print(f"Binance unsubscribe error: {e}")

        # 移除订阅
        del self._subscriptions[sub_key]

        return True

    # ==================== 数据标准化 ====================

    def normalize_kline(self, raw_data: Any) -> Dict:
        """标准化 WebSocket K 线数据"""
        k = raw_data.get('k', {})

        return {
            'symbol': k.get('s'),
            'interval': k.get('i'),
            'open_time': datetime.fromtimestamp(k.get('t', 0) / 1000),
            'close_time': datetime.fromtimestamp(k.get('T', 0) / 1000),
            'open': float(k.get('o', 0)),
            'high': float(k.get('h', 0)),
            'low': float(k.get('l', 0)),
            'close': float(k.get('c', 0)),
            'volume': float(k.get('v', 0)),
            'turnover': float(k.get('q', 0)),
            'trades': int(k.get('n', 0)),
            'is_closed': k.get('x', False),
            'source': self.name,
            'timestamp': datetime.fromtimestamp(k.get('t', 0) / 1000),
        }

    def normalize_kline_rest(self, raw_data: Any, symbol: str, interval: KlineInterval) -> Dict:
        """标准化 REST API K 线数据"""
        return {
            'symbol': symbol,
            'interval': interval.value,
            'open_time': datetime.fromtimestamp(raw_data[0] / 1000),
            'close_time': datetime.fromtimestamp(raw_data[6] / 1000),
            'open': float(raw_data[1]),
            'high': float(raw_data[2]),
            'low': float(raw_data[3]),
            'close': float(raw_data[4]),
            'volume': float(raw_data[5]),
            'turnover': float(raw_data[7]),
            'trades': int(raw_data[8]),
            'source': self.name,
            'timestamp': datetime.fromtimestamp(raw_data[0] / 1000),
        }

    def normalize_trade(self, raw_data: Any) -> Dict:
        """标准化 WebSocket 成交数据"""
        return {
            'symbol': raw_data.get('s'),
            'trade_id': str(raw_data.get('a', raw_data.get('t', 0))),
            'price': float(raw_data.get('p', 0)),
            'quantity': float(raw_data.get('q', 0)),
            'side': 'buy' if raw_data.get('m', False) else 'sell',
            'timestamp': datetime.fromtimestamp(raw_data.get('T', 0) / 1000),
            'source': self.name,
        }

    def normalize_trade_rest(self, raw_data: Any, symbol: str) -> Dict:
        """标准化 REST API 成交数据"""
        return {
            'symbol': symbol,
            'trade_id': str(raw_data.get('id', 0)),
            'price': float(raw_data.get('price', 0)),
            'quantity': float(raw_data.get('qty', 0)),
            'side': 'buy' if raw_data.get('isBuyerMaker', False) else 'sell',
            'timestamp': datetime.fromtimestamp(raw_data.get('time', 0) / 1000),
            'source': self.name,
        }

    def normalize_ticker(self, raw_data: Any) -> Dict:
        """标准化 WebSocket 行情数据"""
        return {
            'symbol': raw_data.get('s'),
            'last_price': float(raw_data.get('c', 0)),
            'bid_price': float(raw_data.get('b', 0)),
            'bid_quantity': float(raw_data.get('B', 0)),
            'ask_price': float(raw_data.get('a', 0)),
            'ask_quantity': float(raw_data.get('A', 0)),
            'high_24h': float(raw_data.get('h', 0)),
            'low_24h': float(raw_data.get('l', 0)),
            'volume_24h': float(raw_data.get('v', 0)),
            'turnover_24h': float(raw_data.get('q', 0)),
            'change_24h': float(raw_data.get('p', 0)),
            'change_pct_24h': float(raw_data.get('P', 0)),
            'timestamp': datetime.now(),
            'source': self.name,
        }

    def normalize_ticker_rest(self, raw_data: Any, symbol: str) -> Dict:
        """标准化 REST API 行情数据"""
        return {
            'symbol': symbol,
            'last_price': float(raw_data.get('lastPrice', 0)),
            'bid_price': float(raw_data.get('bidPrice', 0)),
            'bid_quantity': float(raw_data.get('bidQty', 0)),
            'ask_price': float(raw_data.get('askPrice', 0)),
            'ask_quantity': float(raw_data.get('askQty', 0)),
            'high_24h': float(raw_data.get('highPrice', 0)),
            'low_24h': float(raw_data.get('lowPrice', 0)),
            'volume_24h': float(raw_data.get('volume', 0)),
            'turnover_24h': float(raw_data.get('quoteVolume', 0)),
            'change_24h': float(raw_data.get('priceChange', 0)),
            'change_pct_24h': float(raw_data.get('priceChangePercent', 0)),
            'timestamp': datetime.fromtimestamp(raw_data.get('closeTime', 0) / 1000),
            'source': self.name,
        }

    # ==================== 清理 ====================

    def cleanup(self) -> None:
        """清理资源"""
        super().cleanup()

        # 关闭 CCXT 客户端
        if self._ccxt_spot:
            asyncio.run_coroutine_threadsafe(
                self._ccxt_spot.close(),
                asyncio.get_event_loop()
            )
            self._ccxt_spot = None

        if self._ccxt_futures:
            asyncio.run_coroutine_threadsafe(
                self._ccxt_futures.close(),
                asyncio.get_event_loop()
            )
            self._ccxt_futures = None

        # 关闭 HTTP 客户端
        if self._http_client:
            asyncio.run_coroutine_threadsafe(
                self._http_client.close(),
                asyncio.get_event_loop()
            )
            self._http_client = None