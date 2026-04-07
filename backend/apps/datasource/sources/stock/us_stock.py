"""
美股数据源

支持：
- REST API 历史数据（通过 Yahoo Finance / Alpha Vantage）
- WebSocket 实时数据（需要付费服务）

注意：此模块提供框架实现，实际接入需要配置数据服务商 API。
"""
import asyncio
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Callable
import aiohttp

from apps.datasource.base import (
    BaseDataSource, DataType, KlineInterval, MarketType,
    ConnectionStatus
)
from apps.datasource.registry import DataSourceRegistry
from apps.datasource.store import get_data_store
from apps.datasource.monitor import get_quality_monitor


@DataSourceRegistry.register('us_stock')
class USStockDataSource(BaseDataSource):
    """
    美股数据源

    数据来源：
    - Yahoo Finance（免费，有限制）
    - Alpha Vantage（需要 API Key）

    注意：
    - 免费数据源通常有 API 调用限制
    - 实时 WebSocket 数据需要付费服务
    """

    name = 'us_stock'
    source_type = 'stock'

    supported_data_types = [
        DataType.KLINE,
        DataType.TICKER,
    ]

    supported_market_types = [
        MarketType.SPOT,  # 股票只有现货
    ]

    supported_intervals = [
        KlineInterval.M1, KlineInterval.M5, KlineInterval.M15,
        KlineInterval.M30, KlineInterval.H1, KlineInterval.D1,
        KlineInterval.W1, KlineInterval.M1_MONTH
    ]

    rest_endpoint = 'https://query1.finance.yahoo.com'

    def __init__(self):
        """初始化"""
        super().__init__()

        self._http_client: Optional[aiohttp.ClientSession] = None
        self._store = get_data_store()
        self._monitor = get_quality_monitor()

    async def connect_websocket(self) -> bool:
        """
        建立 WebSocket 连接

        注意：Yahoo Finance 不提供免费 WebSocket
        需要对接其他实时数据服务（如 IEX Cloud、Polygon.io）
        """
        # 目前不支持 WebSocket，返回 False
        print("USStock: WebSocket not supported for free tier")
        return False

    async def disconnect_websocket(self) -> bool:
        """断开 WebSocket 连接"""
        return True

    async def _handle_websocket_message(self, message: Any) -> None:
        """处理 WebSocket 消息"""
        pass

    async def fetch_klines(
        self,
        symbol: str,
        interval: KlineInterval,
        market_type: MarketType = MarketType.SPOT,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 1000
    ) -> List[Dict]:
        """
        获取历史 K 线数据

        使用 Yahoo Finance API
        """
        try:
            if self._http_client is None:
                self._http_client = aiohttp.ClientSession()

            # 构建参数
            period1 = int(start_time.timestamp()) if start_time else int((datetime.now() - timedelta(days=30)).timestamp())
            period2 = int(end_time.timestamp()) if end_time else int(datetime.now().timestamp())

            # 映射周期
            interval_map = {
                '1m': '1m', '5m': '5m', '15m': '15m', '30m': '30m',
                '1h': '1h', '1d': '1d', '1w': '1wk', '1M': '1mo'
            }
            interval_str = interval_map.get(interval.value, '1d')

            url = f"{self.rest_endpoint}/v8/finance/chart/{symbol}"
            params = {
                'period1': period1,
                'period2': period2,
                'interval': interval_str,
            }

            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }

            async with self._http_client.get(url, params=params, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    result = data.get('chart', {}).get('result', [])
                    if result:
                        klines = self._parse_yahoo_klines(result[0], symbol, interval)
                        self._store.store_batch('kline', symbol, klines, self.name)
                        return klines
                else:
                    raise Exception(f"API error: {resp.status}")

        except Exception as e:
            print(f"USStock fetch_klines error: {e}")
            return []

    def _parse_yahoo_klines(self, data: Dict, symbol: str, interval: KlineInterval) -> List[Dict]:
        """解析 Yahoo Finance K线数据"""
        timestamps = data.get('timestamp', [])
        indicators = data.get('indicators', {}).get('quote', [{}])[0]

        klines = []
        for i, ts in enumerate(timestamps):
            klines.append({
                'symbol': symbol,
                'interval': interval.value,
                'open_time': datetime.fromtimestamp(ts),
                'close_time': datetime.fromtimestamp(ts + 86400),  # 假设日线
                'open': float(indicators.get('open', [0])[i]),
                'high': float(indicators.get('high', [0])[i]),
                'low': float(indicators.get('low', [0])[i]),
                'close': float(indicators.get('close', [0])[i]),
                'volume': float(indicators.get('volume', [0])[i]),
                'turnover': 0,
                'trades': 0,
                'source': self.name,
                'timestamp': datetime.fromtimestamp(ts),
            })

        return klines

    async def fetch_trades(
        self,
        symbol: str,
        market_type: MarketType = MarketType.SPOT,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 1000
    ) -> List[Dict]:
        """获取历史成交数据"""
        # Yahoo Finance 不提供逐笔成交数据
        print("USStock: Trade data not available for free tier")
        return []

    async def fetch_ticker(
        self,
        symbol: str,
        market_type: MarketType = MarketType.SPOT
    ) -> Dict:
        """获取实时行情快照"""
        try:
            if self._http_client is None:
                self._http_client = aiohttp.ClientSession()

            url = f"{self.rest_endpoint}/v6/finance/quote"
            params = {'symbols': symbol}

            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }

            async with self._http_client.get(url, params=params, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    result = data.get('quoteResponse', {}).get('result', [])
                    if result:
                        ticker = self._parse_yahoo_ticker(result[0], symbol)
                        self._store.store('ticker', symbol, ticker, self.name)
                        return ticker
                return {}

        except Exception as e:
            print(f"USStock fetch_ticker error: {e}")
            return {}

    def _parse_yahoo_ticker(self, data: Dict, symbol: str) -> Dict:
        """解析 Yahoo Finance 行情数据"""
        return {
            'symbol': symbol,
            'last_price': float(data.get('regularMarketPrice', 0)),
            'bid_price': float(data.get('bid', 0)),
            'bid_quantity': float(data.get('bidSize', 0)),
            'ask_price': float(data.get('ask', 0)),
            'ask_quantity': float(data.get('askSize', 0)),
            'high_24h': float(data.get('regularMarketDayHigh', 0)),
            'low_24h': float(data.get('regularMarketDayLow', 0)),
            'volume_24h': float(data.get('regularMarketVolume', 0)),
            'turnover_24h': 0,
            'change_24h': float(data.get('regularMarketChange', 0)),
            'change_pct_24h': float(data.get('regularMarketChangePercent', 0)),
            'timestamp': datetime.now(),
            'source': self.name,
        }

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

        print("USStock: WebSocket subscription not supported for free tier")
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
        if sub_key in self._subscriptions:
            del self._subscriptions[sub_key]
        return True

    def normalize_kline(self, raw_data: Any) -> Dict:
        """标准化 K 线数据"""
        return raw_data

    def normalize_trade(self, raw_data: Any) -> Dict:
        """标准化成交数据"""
        return raw_data

    def normalize_ticker(self, raw_data: Any) -> Dict:
        """标准化行情数据"""
        return raw_data

    def cleanup(self) -> None:
        """清理资源"""
        super().cleanup()
        if self._http_client:
            asyncio.run_coroutine_threadsafe(self._http_client.close(), asyncio.get_event_loop())
            self._http_client = None