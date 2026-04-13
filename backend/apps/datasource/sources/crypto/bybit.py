"""
Bybit 数据源

支持：
- WebSocket 实时数据（K线、成交、深度）
- REST API 历史数据
- 现货和合约市场
"""

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Dict, List, Optional, Any, Callable
import aiohttp
import ccxt.async_support as ccxt

from apps.datasource.base import (
    BaseDataSource,
    DataType,
    KlineInterval,
    MarketType,
    ConnectionStatus,
)
from apps.datasource.registry import DataSourceRegistry
from apps.datasource.store import get_data_store
from apps.datasource.monitor import get_quality_monitor

logger = logging.getLogger(__name__)


@DataSourceRegistry.register("bybit")
class BybitDataSource(BaseDataSource):
    """
    Bybit 数据源

    WebSocket 端点:
    - 现货: wss://stream.bybit.com/v5/public/spot
    - 合约: wss://stream.bybit.com/v5/public/linear

    REST API 端点:
    - https://api.bybit.com
    """

    name = "bybit"
    source_type = "crypto"

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
        KlineInterval.M1,
        KlineInterval.M3,
        KlineInterval.M5,
        KlineInterval.M15,
        KlineInterval.M30,
        KlineInterval.H1,
        KlineInterval.H4,
        KlineInterval.D1,
        KlineInterval.W1,
    ]

    ws_spot_endpoint = "wss://stream.bybit.com/v5/public/spot"
    ws_futures_endpoint = "wss://stream.bybit.com/v5/public/linear"
    rest_endpoint = "https://api.bybit.com"

    def __init__(self):
        """初始化"""
        super().__init__()

        self._ws_spot: Optional[aiohttp.ClientWebSocketResponse] = None
        self._ws_futures: Optional[aiohttp.ClientWebSocketResponse] = None
        self._http_client: Optional[aiohttp.ClientSession] = None
        self._ccxt: Optional[ccxt.bybit] = None
        self._ws_tasks: List[asyncio.Task] = []

        self._store = get_data_store()
        self._monitor = get_quality_monitor()

    async def connect_websocket(self) -> bool:
        """建立 WebSocket 连接（仅连接已配置的市场类型）"""
        try:
            self._ws_status = ConnectionStatus.CONNECTING
            active_types = self._get_active_market_types()
            logger.info(
                "[Bybit] connecting WebSocket, markets=%s",
                [mt.value for mt in active_types],
            )

            if self._http_client is None:
                self._http_client = aiohttp.ClientSession()

            # 仅在配置了现货时连接现货 WebSocket
            if MarketType.SPOT in active_types:
                self._ws_spot = await self._http_client.ws_connect(
                    self.ws_spot_endpoint, heartbeat=20, receive_timeout=30
                )

            # 仅在配置了合约时连接合约 WebSocket
            if MarketType.FUTURES in active_types:
                self._ws_futures = await self._http_client.ws_connect(
                    self.ws_futures_endpoint, heartbeat=20, receive_timeout=30
                )

            # 只为活跃连接启动消息处理任务
            self._ws_tasks = []
            if MarketType.SPOT in active_types:
                self._ws_tasks.append(asyncio.create_task(self._ws_spot_receiver()))
            if MarketType.FUTURES in active_types:
                self._ws_tasks.append(asyncio.create_task(self._ws_futures_receiver()))

            self._ws_status = ConnectionStatus.CONNECTED
            self._connected_at = datetime.now()

            await self._resubscribe_all()

            status = self.get_status()
            logger.info(
                "[Bybit] WebSocket connected: status=%s, subs=%d",
                status["status"],
                status["subscriptions"],
            )
            return True

        except Exception as e:
            self._ws_status = ConnectionStatus.ERROR
            logger.error("[Bybit] WebSocket connection error: %s", e)
            return False

    async def disconnect_websocket(self) -> bool:
        """断开 WebSocket 连接"""
        try:
            logger.info(
                "[Bybit] disconnecting WebSocket, current_subs=%d",
                len(self._subscriptions),
            )

            for task in self._ws_tasks:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            self._ws_tasks = []

            if self._ws_spot and not self._ws_spot.closed:
                await self._ws_spot.close()
            self._ws_spot = None

            if self._ws_futures and not self._ws_futures.closed:
                await self._ws_futures.close()
            self._ws_futures = None

            self._ws_status = ConnectionStatus.DISCONNECTED
            logger.info("[Bybit] WebSocket disconnected")
            return True

        except Exception as e:
            logger.error("[Bybit] WebSocket disconnect error: %s", e)
            return False

    async def _ws_spot_receiver(self) -> None:
        """现货 WebSocket 消息接收"""
        while self._ws_spot and not self._ws_spot.closed:
            try:
                msg = await self._ws_spot.receive()
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    await self._handle_websocket_message(data, MarketType.SPOT)
                elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                    break

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Bybit spot WebSocket receive error: {e}")
                await asyncio.sleep(1)

    async def _ws_futures_receiver(self) -> None:
        """合约 WebSocket 消息接收"""
        while self._ws_futures and not self._ws_futures.closed:
            try:
                msg = await self._ws_futures.receive()
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    await self._handle_websocket_message(data, MarketType.FUTURES)
                elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                    break

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Bybit futures WebSocket receive error: {e}")
                await asyncio.sleep(1)

    async def _handle_websocket_message(
        self, msg: Dict, market_type: MarketType
    ) -> None:
        """处理 WebSocket 消息"""
        receive_time = datetime.now()
        self._last_data_time = time.time()

        topic = msg.get("topic", "")
        data_list = msg.get("data", [])

        if not data_list:
            return

        for raw_data in data_list:
            if "kline" in topic:
                kline = self.normalize_kline({"topic": topic, "data": raw_data})
                kline["market_type"] = market_type.value
                symbol = kline["symbol"]
                self._store.store("kline", symbol, kline, self.name)
                self._monitor.record_data(
                    self.name,
                    symbol,
                    "kline",
                    kline.get("timestamp", receive_time),
                    receive_time,
                )
                self._trigger_callbacks(DataType.KLINE, kline)

            elif "publicTrade" in topic:
                trade = self.normalize_trade({"topic": topic, "data": raw_data})
                trade["market_type"] = market_type.value
                symbol = trade["symbol"]
                self._store.store("trade", symbol, trade, self.name)
                self._monitor.record_data(
                    self.name,
                    symbol,
                    "trade",
                    trade.get("timestamp", receive_time),
                    receive_time,
                )
                self._trigger_callbacks(DataType.TRADE, trade)

            elif "tickers" in topic:
                ticker = self.normalize_ticker({"topic": topic, "data": raw_data})
                ticker["market_type"] = market_type.value
                symbol = ticker["symbol"]
                self._store.store("ticker", symbol, ticker, self.name)
                self._monitor.record_data(
                    self.name,
                    symbol,
                    "ticker",
                    ticker.get("timestamp", receive_time),
                    receive_time,
                )
                self._trigger_callbacks(DataType.TICKER, ticker)

    async def _resubscribe_all(self) -> None:
        """重新发送所有订阅"""
        for sub_info in self._subscriptions.values():
            await self._send_subscribe(sub_info)

    async def _send_subscribe(self, sub_info: Dict) -> None:
        """发送订阅请求"""
        symbol = sub_info["symbol"].replace("/", "")
        data_type = sub_info["data_type"]
        interval = sub_info.get("interval")
        market_type = sub_info.get("market_type", MarketType.SPOT)

        ws = self._ws_spot if market_type == MarketType.SPOT else self._ws_futures

        if ws is None or ws.closed:
            return

        topic = self._build_topic(symbol, data_type, interval)

        msg = {"op": "subscribe", "args": [topic]}

        await ws.send_str(json.dumps(msg))

    def _build_topic(
        self, symbol: str, data_type: DataType, interval: Optional[KlineInterval] = None
    ) -> str:
        """构建订阅主题"""
        if data_type == DataType.KLINE:
            interval_str = interval.value if interval else "1"
            return f"kline.{interval_str}.{symbol}"
        elif data_type == DataType.TRADE:
            return f"publicTrade.{symbol}"
        elif data_type == DataType.TICKER:
            return f"tickers.{symbol}"
        elif data_type == DataType.DEPTH:
            return f"orderbook.50.{symbol}"
        return ""

    async def fetch_klines(
        self,
        symbol: str,
        interval: KlineInterval,
        market_type: MarketType = MarketType.SPOT,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 1000,
    ) -> List[Dict]:
        """获取历史 K 线数据"""
        try:
            category = "spot" if market_type == MarketType.SPOT else "linear"
            symbol_fmt = symbol.replace("/", "")

            interval_map = {
                "1m": 1,
                "3m": 3,
                "5m": 5,
                "15m": 15,
                "30m": 30,
                "1h": 60,
                "4h": 240,
                "1d": "D",
                "1w": "W",
            }
            interval_val = interval_map.get(interval.value, 1)

            params = {
                "category": category,
                "symbol": symbol_fmt,
                "interval": interval_val,
                "limit": min(limit, 1000),
            }

            if start_time:
                params["start"] = int(start_time.timestamp() * 1000)
            if end_time:
                params["end"] = int(end_time.timestamp() * 1000)

            url = f"{self.rest_endpoint}/v5/market/kline"

            if self._http_client is None:
                self._http_client = aiohttp.ClientSession()

            async with self._http_client.get(url, params=params) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    data = result.get("result", {}).get("list", [])
                    klines = [
                        self.normalize_kline_rest(k, symbol, interval) for k in data
                    ]
                    self._store.store_batch("kline", symbol, klines, self.name)
                    return klines
                else:
                    raise Exception(f"API error: {resp.status}")

        except Exception as e:
            print(f"Bybit fetch_klines error: {e}")
            return []

    async def fetch_trades(
        self,
        symbol: str,
        market_type: MarketType = MarketType.SPOT,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 1000,
    ) -> List[Dict]:
        """获取历史成交数据"""
        try:
            category = "spot" if market_type == MarketType.SPOT else "linear"
            symbol_fmt = symbol.replace("/", "")

            url = f"{self.rest_endpoint}/v5/market/recent-trade"
            params = {
                "category": category,
                "symbol": symbol_fmt,
                "limit": min(limit, 1000),
            }

            if self._http_client is None:
                self._http_client = aiohttp.ClientSession()

            async with self._http_client.get(url, params=params) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    data = result.get("result", {}).get("list", [])
                    trades = [self.normalize_trade_rest(t, symbol) for t in data]
                    self._store.store_batch("trade", symbol, trades, self.name)
                    return trades
                else:
                    raise Exception(f"API error: {resp.status}")

        except Exception as e:
            print(f"Bybit fetch_trades error: {e}")
            return []

    async def fetch_ticker(
        self, symbol: str, market_type: MarketType = MarketType.SPOT
    ) -> Dict:
        """获取实时行情快照"""
        try:
            category = "spot" if market_type == MarketType.SPOT else "linear"
            symbol_fmt = symbol.replace("/", "")

            url = f"{self.rest_endpoint}/v5/market/tickers"
            params = {
                "category": category,
                "symbol": symbol_fmt,
            }

            if self._http_client is None:
                self._http_client = aiohttp.ClientSession()

            async with self._http_client.get(url, params=params) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    data = result.get("result", {}).get("list", [])
                    if data:
                        ticker = self.normalize_ticker_rest(data[0], symbol)
                        self._store.store("ticker", symbol, ticker, self.name)
                        return ticker
                return {}

        except Exception as e:
            print(f"Bybit fetch_ticker error: {e}")
            return {}

    async def subscribe(
        self,
        symbol: str,
        data_type: DataType,
        interval: Optional[KlineInterval] = None,
        market_type: MarketType = MarketType.SPOT,
        callback: Optional[Callable] = None,
    ) -> bool:
        """订阅数据"""
        if callback:
            self.register_callback(data_type, callback)

        sub_key = f"{symbol}:{data_type.value}:{interval.value if interval else 'none'}:{market_type.value}"
        self._subscriptions[sub_key] = {
            "symbol": symbol,
            "data_type": data_type,
            "interval": interval,
            "market_type": market_type,
            "callback": callback,
            "active": True,
        }

        if self._ws_status == ConnectionStatus.CONNECTED:
            await self._send_subscribe(self._subscriptions[sub_key])

        return True

    async def unsubscribe(
        self,
        symbol: str,
        data_type: DataType,
        interval: Optional[KlineInterval] = None,
        market_type: MarketType = MarketType.SPOT,
    ) -> bool:
        """取消订阅"""
        sub_key = f"{symbol}:{data_type.value}:{interval.value if interval else 'none'}:{market_type.value}"

        if sub_key not in self._subscriptions:
            return False

        self._subscriptions[sub_key]["active"] = False

        if self._ws_status == ConnectionStatus.CONNECTED:
            ws = self._ws_spot if market_type == MarketType.SPOT else self._ws_futures
            if ws and not ws.closed:
                symbol_fmt = symbol.replace("/", "")
                topic = self._build_topic(symbol_fmt, data_type, interval)

                msg = {"op": "unsubscribe", "args": [topic]}
                await ws.send_str(json.dumps(msg))

        del self._subscriptions[sub_key]
        return True

    def normalize_kline(self, raw_data: Any) -> Dict:
        """标准化 WebSocket K 线数据"""
        topic = raw_data.get("topic", "")
        data = raw_data.get("data", {})

        # 从 topic 解析 symbol 和 interval
        parts = topic.split(".")
        interval_str = parts[1] if len(parts) > 1 else "1"
        symbol = parts[2] if len(parts) > 2 else ""

        ts = int(data.get("start", 0))

        return {
            "symbol": symbol,
            "interval": f"{interval_str}m" if interval_str.isdigit() else interval_str,
            "open_time": datetime.fromtimestamp(ts),
            "close_time": datetime.fromtimestamp(ts + 60000),
            "open": float(data.get("open", 0)),
            "high": float(data.get("high", 0)),
            "low": float(data.get("low", 0)),
            "close": float(data.get("close", 0)),
            "volume": float(data.get("volume", 0)),
            "turnover": float(data.get("turnover", 0)),
            "trades": 0,
            "source": self.name,
            "timestamp": datetime.fromtimestamp(ts),
        }

    def normalize_kline_rest(
        self, raw_data: Any, symbol: str, interval: KlineInterval
    ) -> Dict:
        """标准化 REST API K 线数据"""
        # Bybit 格式: [startTime, openPrice, highPrice, lowPrice, closePrice, volume, turnover]
        ts = int(raw_data[0])

        return {
            "symbol": symbol,
            "interval": interval.value,
            "open_time": datetime.fromtimestamp(ts / 1000),
            "close_time": datetime.fromtimestamp((ts + 60000) / 1000),
            "open": float(raw_data[1]),
            "high": float(raw_data[2]),
            "low": float(raw_data[3]),
            "close": float(raw_data[4]),
            "volume": float(raw_data[5]),
            "turnover": float(raw_data[6]),
            "trades": 0,
            "source": self.name,
            "timestamp": datetime.fromtimestamp(ts / 1000),
        }

    def normalize_trade(self, raw_data: Any) -> Dict:
        """标准化 WebSocket 成交数据"""
        ts = int(raw_data.get("time", 0))

        return {
            "symbol": raw_data.get("s", ""),
            "trade_id": str(raw_data.get("i", "")),
            "price": float(raw_data.get("p", 0)),
            "quantity": float(raw_data.get("v", 0)),
            "side": raw_data.get("S", "").lower(),
            "timestamp": datetime.fromtimestamp(ts / 1000),
            "source": self.name,
        }

    def normalize_trade_rest(self, raw_data: Any, symbol: str) -> Dict:
        """标准化 REST API 成交数据"""
        ts = int(raw_data.get("time", 0))

        return {
            "symbol": symbol,
            "trade_id": str(raw_data.get("execId", "")),
            "price": float(raw_data.get("price", 0)),
            "quantity": float(raw_data.get("size", 0)),
            "side": raw_data.get("side", "").lower(),
            "timestamp": datetime.fromtimestamp(ts / 1000),
            "source": self.name,
        }

    def normalize_ticker(self, raw_data: Any) -> Dict:
        """标准化 WebSocket 行情数据"""
        return {
            "symbol": raw_data.get("symbol", ""),
            "last_price": float(raw_data.get("lastPrice", 0)),
            "bid_price": float(raw_data.get("bid1Price", 0)),
            "bid_quantity": float(raw_data.get("bid1Size", 0)),
            "ask_price": float(raw_data.get("ask1Price", 0)),
            "ask_quantity": float(raw_data.get("ask1Size", 0)),
            "high_24h": float(raw_data.get("highPrice24h", 0)),
            "low_24h": float(raw_data.get("lowPrice24h", 0)),
            "volume_24h": float(raw_data.get("volume24h", 0)),
            "turnover_24h": float(raw_data.get("turnover24h", 0)),
            "change_24h": float(raw_data.get("price24hPcnt", 0)),
            "change_pct_24h": float(raw_data.get("price24hPcnt", 0)) * 100,
            "timestamp": datetime.now(),
            "source": self.name,
        }

    def normalize_ticker_rest(self, raw_data: Any, symbol: str) -> Dict:
        """标准化 REST API 行情数据"""
        return {
            "symbol": symbol,
            "last_price": float(raw_data.get("lastPrice", 0)),
            "bid_price": float(raw_data.get("bid1Price", 0)),
            "bid_quantity": float(raw_data.get("bid1Size", 0)),
            "ask_price": float(raw_data.get("ask1Price", 0)),
            "ask_quantity": float(raw_data.get("ask1Size", 0)),
            "high_24h": float(raw_data.get("highPrice24h", 0)),
            "low_24h": float(raw_data.get("lowPrice24h", 0)),
            "volume_24h": float(raw_data.get("volume24h", 0)),
            "turnover_24h": float(raw_data.get("turnover24h", 0)),
            "change_24h": float(raw_data.get("price24hPcnt", 0)),
            "change_pct_24h": float(raw_data.get("price24hPcnt", 0)) * 100,
            "timestamp": datetime.now(),
            "source": self.name,
        }

    def cleanup(self) -> None:
        """清理资源"""
        super().cleanup()

        if self._ccxt:
            asyncio.run_coroutine_threadsafe(
                self._ccxt.close(), asyncio.get_event_loop()
            )
            self._ccxt = None

        if self._http_client:
            asyncio.run_coroutine_threadsafe(
                self._http_client.close(), asyncio.get_event_loop()
            )
            self._http_client = None
