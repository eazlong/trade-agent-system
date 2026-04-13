"""
A股数据源

支持：
- REST API 历史数据（通过东方财富 / 新浪财经）
- WebSocket 实时数据（需要对接服务商）

注意：此模块提供框架实现，实际接入需要配置数据服务商 API。
"""

import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Callable
import aiohttp

from apps.datasource.base import (
    BaseDataSource,
    DataType,
    KlineInterval,
    MarketType,
)
from apps.datasource.registry import DataSourceRegistry
from apps.datasource.store import get_data_store
from apps.datasource.monitor import get_quality_monitor


@DataSourceRegistry.register("cn_stock")
class CNStockDataSource(BaseDataSource):
    """
    A股数据源

    数据来源：
    - 东方财富（需要处理接口限制）
    - 新浪财经（免费，有限制）

    注意：
    - A股数据需要特殊编码（如 000001.SZ）
    - 实时数据通常需要付费服务
    """

    name = "cn_stock"
    source_type = "stock"

    supported_data_types = [
        DataType.KLINE,
        DataType.TICKER,
    ]

    supported_market_types = [
        MarketType.SPOT,
    ]

    supported_intervals = [
        KlineInterval.M1,
        KlineInterval.M5,
        KlineInterval.M15,
        KlineInterval.M30,
        KlineInterval.H1,
        KlineInterval.D1,
        KlineInterval.W1,
        KlineInterval.M1_MONTH,
    ]

    rest_endpoint = "http://push2his.eastmoney.com"

    def __init__(self):
        """初始化"""
        super().__init__()

        self._http_client: Optional[aiohttp.ClientSession] = None
        self._store = get_data_store()
        self._monitor = get_quality_monitor()

    async def connect_websocket(self) -> bool:
        """建立 WebSocket 连接"""
        print("CNStock: WebSocket not implemented")
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
        limit: int = 1000,
    ) -> List[Dict]:
        """
        获取历史 K 线数据

        使用东方财富 API
        """
        try:
            if self._http_client is None:
                self._http_client = aiohttp.ClientSession()

            # 解析股票代码和市场
            code, market = self._parse_symbol(symbol)

            # 映射周期
            period_map = {
                "1m": "1",
                "5m": "5",
                "15m": "15",
                "30m": "30",
                "60m": "60",
                "1d": "101",
                "1w": "102",
                "1M": "103",
            }
            period = period_map.get(interval.value, "101")

            params = {
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57",
                "klt": period,
                "fqt": "1",  # 前复权
                "secid": f"{market}.{code}",
                "beg": start_time.strftime("%Y%m%d") if start_time else "0",
                "end": end_time.strftime("%Y%m%d") if end_time else "20500101",
            }

            url = f"{self.rest_endpoint}/api/qt/stock/kline/get"

            async with self._http_client.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    klines_data = data.get("data", {}).get("klines", [])
                    klines = [
                        self._parse_kline(k, symbol, interval) for k in klines_data
                    ]
                    self._store.store_batch("kline", symbol, klines, self.name)
                    return klines
                else:
                    raise Exception(f"API error: {resp.status}")

        except Exception as e:
            print(f"CNStock fetch_klines error: {e}")
            return []

    def _parse_symbol(self, symbol: str) -> tuple:
        """
        解析股票代码

        格式：000001.SZ 或 600001.SH
        返回：(code, market_id)
        """
        parts = symbol.split(".")
        code = parts[0]
        market = parts[1] if len(parts) > 1 else "SZ"

        # 市场编码
        market_id = "0" if market == "SZ" else "1"  # 0=深市, 1=沪市

        return code, market_id

    def _parse_kline(
        self, kline_str: str, symbol: str, interval: KlineInterval
    ) -> Dict:
        """
        解析东方财富 K线数据

        格式：日期,开,收,高,低,成交量,成交额
        """
        parts = kline_str.split(",")

        date_str = parts[0]
        # 解析日期
        if len(date_str) == 10:  # YYYY-MM-DD
            ts = datetime.strptime(date_str, "%Y-%m-%d")
        else:  # YYYY-MM-DD HH:mm
            ts = datetime.strptime(date_str, "%Y-%m-%d %H:%M")

        return {
            "symbol": symbol,
            "interval": interval.value,
            "open_time": ts,
            "close_time": ts + timedelta(days=1),
            "open": float(parts[1]),
            "close": float(parts[2]),
            "high": float(parts[3]),
            "low": float(parts[4]),
            "volume": float(parts[5]),
            "turnover": float(parts[6]) if len(parts) > 6 else 0,
            "trades": 0,
            "source": self.name,
            "timestamp": ts,
        }

    async def fetch_trades(
        self,
        symbol: str,
        market_type: MarketType = MarketType.SPOT,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 1000,
    ) -> List[Dict]:
        """获取历史成交数据"""
        print("CNStock: Trade data not available")
        return []

    async def fetch_ticker(
        self, symbol: str, market_type: MarketType = MarketType.SPOT
    ) -> Dict:
        """获取实时行情快照"""
        try:
            if self._http_client is None:
                self._http_client = aiohttp.ClientSession()

            code, market = self._parse_symbol(symbol)

            url = f"{self.rest_endpoint}/api/qt/stock/get"
            params = {
                "secid": f"{market}.{code}",
                "fields": "f43,f44,f45,f46,f47,f48,f50,f51,f52,f58,f60,f170,f171",
            }

            async with self._http_client.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    ticker_data = data.get("data", {})
                    if ticker_data:
                        ticker = self._parse_ticker(ticker_data, symbol)
                        self._store.store("ticker", symbol, ticker, self.name)
                        return ticker
                return {}

        except Exception as e:
            print(f"CNStock fetch_ticker error: {e}")
            return {}

    def _parse_ticker(self, data: Dict, symbol: str) -> Dict:
        """解析东方财富行情数据"""
        return {
            "symbol": symbol,
            "last_price": float(data.get("f43", 0)) / 100,  # 价格需要除以100
            "bid_price": float(data.get("f44", 0)) / 100,
            "bid_quantity": float(data.get("f45", 0)),
            "ask_price": float(data.get("f46", 0)) / 100,
            "ask_quantity": float(data.get("f47", 0)),
            "high_24h": float(data.get("f44", 0)) / 100,
            "low_24h": float(data.get("f45", 0)) / 100,
            "volume_24h": float(data.get("f47", 0)),
            "turnover_24h": 0,
            "change_24h": float(data.get("f170", 0)) / 100,
            "change_pct_24h": float(data.get("f170", 0)) / 100 * 100,
            "timestamp": datetime.now(),
            "source": self.name,
        }

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
            asyncio.run_coroutine_threadsafe(
                self._http_client.close(), asyncio.get_event_loop()
            )
            self._http_client = None
