"""Market data tools — fetch OHLCV data and calculate technical indicators."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

import pandas as pd

from .base import BaseTool, ToolResult

logger = logging.getLogger(__name__)

# 临时文件目录
_TMP_DIR = Path.home() / '.tradelogx' / 'tmp'


class FetchOHLCVTool(BaseTool):
    """
    Fetch OHLCV (Open/High/Low/Close/Volume) data from exchange.
    Results are written to a temp file for CalculateIndicatorsTool to read.
    """

    name = 'fetch_ohlcv'
    description = (
        '从交易所获取K线数据（开盘价、最高价、最低价、收盘价、成交量）。'
        '数据会写入临时文件，返回文件路径供 calculate_indicators 工具读取。'
        '支持日线、4小时、1小时等多种时间周期。'
    )

    @property
    def parameters_schema(self) -> dict:
        return {
            'type': 'object',
            'properties': {
                'symbol': {
                    'type': 'string',
                    'description': '交易品种，如 BTC/USDT、ETH/USDT',
                },
                'timeframe': {
                    'type': 'string',
                    'description': '时间周期，如 1d（日线）、4h（4小时）、1h（1小时）',
                    'enum': ['1d', '4h', '1h', '30m', '15m', '5m', '1m'],
                },
                'limit': {
                    'type': 'integer',
                    'description': '获取的K线数量，默认200，最大500',
                    'minimum': 10,
                    'maximum': 500,
                    'default': 200,
                },
                'exchange': {
                    'type': 'string',
                    'description': '交易所名称，如 binance、okx、bybit',
                    'default': 'binance',
                },
            },
            'required': ['symbol', 'timeframe'],
        }

    async def execute(
        self,
        symbol: str = '',
        timeframe: str = '1d',
        limit: int = 200,
        exchange: str = 'binance',
        **kwargs,
    ) -> ToolResult:
        if not symbol:
            return ToolResult(success=False, error='symbol 参数缺失')

        # Normalize symbol
        symbol_normalized = symbol.replace('-', '/').replace('_', '/')
        if '/' not in symbol_normalized:
            symbol_normalized = f'{symbol_normalized}/USDT'

        try:
            import ccxt.async_support as ccxt
            from django.conf import settings

            exchange_class = getattr(ccxt, exchange.lower(), None)
            if exchange_class is None:
                return ToolResult(
                    success=False,
                    error=f'不支持的交易所: {exchange}',
                )

            # Proxy config
            options = {'enableRateLimit': True}
            proxy = getattr(settings, 'WEB_PROXY', '') or None
            if proxy:
                options['aiohttp_proxy'] = proxy

            ex = exchange_class(options)
            logger.debug('Fetching OHLCV for %s from %s', symbol_normalized, exchange)

            try:
                ohlcv = await ex.fetch_ohlcv(
                    symbol_normalized, timeframe, limit=min(limit, 500)
                )
            finally:
                try:
                    await ex.close()
                except Exception:
                    pass

            if not ohlcv:
                return ToolResult(
                    success=False,
                    error=f'未获取到 {symbol_normalized} 的K线数据',
                )

            # Convert to DataFrame
            df = pd.DataFrame(
                ohlcv,
                columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'],
            )
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')

            # Write to temp file
            _TMP_DIR.mkdir(parents=True, exist_ok=True)
            task_id = uuid.uuid4().hex[:8]
            tmp_file = _TMP_DIR / f'ohlcv_{task_id}.json'
            tmp_file.write_text(df.to_json(orient='records', date_format='iso'), encoding='utf-8')

            data = {
                'symbol': symbol_normalized,
                'timeframe': timeframe,
                'exchange': exchange,
                'temp_file': str(tmp_file),
                'count': len(df),
                'latest_price': float(df['close'].iloc[-1]),
                'latest_volume': float(df['volume'].iloc[-1]),
            }
            return ToolResult(success=True, data=data)

        except Exception as e:
            logger.error('[FetchOHLCVTool] error: %s', e)
            return ToolResult(success=False, error=f'获取K线数据失败: {e}')


class CalculateIndicatorsTool(BaseTool):
    """
    Calculate technical indicators (EMA, MACD, RSI, KDJ, ATR, Bollinger Bands).
    Reads OHLCV data from the temp file created by fetch_ohlcv.
    """

    name = 'calculate_indicators'
    description = (
        '计算技术指标。从 fetch_ohlcv 生成的临时文件中读取K线数据，'
        '返回EMA、MACD、RSI、KDJ、ATR、布林带等指标。'
    )

    @property
    def parameters_schema(self) -> dict:
        return {
            'type': 'object',
            'properties': {
                'temp_file': {
                    'type': 'string',
                    'description': 'fetch_ohlcv 返回的 temp_file 路径',
                },
                'indicators': {
                    'type': 'array',
                    'description': '要计算的指标列表',
                    'items': {
                        'type': 'string',
                        'enum': ['ema', 'macd', 'rsi', 'kdj', 'atr', 'bollinger'],
                    },
                    'default': ['ema', 'macd', 'rsi', 'kdj', 'atr'],
                },
            },
            'required': ['temp_file'],
        }

    def _calc_ema(self, series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    def _calc_macd(self, series: pd.Series) -> dict:
        ema_fast = self._calc_ema(series, 12)
        ema_slow = self._calc_ema(series, 26)
        macd_line = ema_fast - ema_slow
        signal_line = self._calc_ema(macd_line, 9)
        histogram = macd_line - signal_line
        return {'macd': macd_line, 'signal': signal_line, 'histogram': histogram}

    def _calc_rsi(self, series: pd.Series, period: int = 14) -> pd.Series:
        delta = series.diff()
        gain = delta.where(delta > 0, 0.0).ewm(alpha=1 / period, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0.0)).ewm(alpha=1 / period, adjust=False).mean()
        rs = gain / loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _calc_kdj(self, high: pd.Series, low: pd.Series, close: pd.Series) -> dict:
        lowest_low = low.rolling(window=9).min()
        highest_high = high.rolling(window=9).max()
        rsv = (close - lowest_low) / (highest_high - lowest_low) * 100.0
        k_val = rsv.ewm(alpha=1 / 3, adjust=False).mean()
        d_val = k_val.ewm(alpha=1 / 3, adjust=False).mean()
        j_val = 3.0 * k_val - 2.0 * d_val
        return {'K': k_val, 'D': d_val, 'J': j_val}

    def _calc_atr(
        self, high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
    ) -> pd.Series:
        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low - close.shift()).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.ewm(span=period, adjust=False).mean()

    def _calc_bollinger(self, series: pd.Series) -> dict:
        middle = series.rolling(window=20).mean()
        std = series.rolling(window=20).std()
        return {
            'middle': middle,
            'upper': middle + 2.0 * std,
            'lower': middle - 2.0 * std,
        }

    async def execute(
        self,
        temp_file: str = '',
        indicators: list[str] = None,
        **kwargs,
    ) -> ToolResult:
        if not temp_file:
            return ToolResult(success=False, error='temp_file 参数缺失')

        indicators = indicators or ['ema', 'macd', 'rsi', 'kdj', 'atr']

        try:
            path = Path(temp_file)
            if not path.exists():
                return ToolResult(
                    success=False,
                    error=f'临时文件不存在: {temp_file}，请先调用 fetch_ohlcv',
                )

            records = json.loads(path.read_text(encoding='utf-8'))
            df = pd.DataFrame(records)

            # Parse timestamp if present
            if 'timestamp' in df.columns:
                df['timestamp'] = pd.to_datetime(df['timestamp'])

            close = df['close']
            high = df['high']
            low = df['low']

            result = {}

            if 'ema' in indicators:
                result['ema'] = {
                    'ema20': float(self._calc_ema(close, 20).iloc[-1]),
                    'ema50': float(self._calc_ema(close, 50).iloc[-1]),
                    'ema200': float(self._calc_ema(close, 200).iloc[-1]),
                }

            if 'macd' in indicators:
                macd = self._calc_macd(close)
                result['macd'] = {
                    'macd': float(macd['macd'].iloc[-1]),
                    'signal': float(macd['signal'].iloc[-1]),
                    'histogram': float(macd['histogram'].iloc[-1]),
                }

            if 'rsi' in indicators:
                result['rsi'] = float(self._calc_rsi(close).iloc[-1])

            if 'kdj' in indicators:
                kdj = self._calc_kdj(high, low, close)
                result['kdj'] = {
                    'K': float(kdj['K'].iloc[-1]),
                    'D': float(kdj['D'].iloc[-1]),
                    'J': float(kdj['J'].iloc[-1]),
                }

            if 'atr' in indicators:
                result['atr'] = float(self._calc_atr(high, low, close).iloc[-1])

            if 'bollinger' in indicators:
                bb = self._calc_bollinger(close)
                result['bollinger'] = {
                    'middle': float(bb['middle'].iloc[-1]),
                    'upper': float(bb['upper'].iloc[-1]),
                    'lower': float(bb['lower'].iloc[-1]),
                }

            result['latest'] = {
                'open': float(df['open'].iloc[-1]),
                'high': float(df['high'].iloc[-1]),
                'low': float(df['low'].iloc[-1]),
                'close': float(df['close'].iloc[-1]),
                'volume': float(df['volume'].iloc[-1]),
            }

            # Cleanup temp file
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

            return ToolResult(success=True, data=result)

        except Exception as e:
            logger.error('[CalculateIndicatorsTool] error: %s', e)
            return ToolResult(success=False, error=f'计算指标失败: {e}')
