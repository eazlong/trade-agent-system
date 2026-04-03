from .base import BaseExchangeAdapter, OrderRequest, OrderResponse, Position
from .binance import BinanceAdapter
from .okx import OKXAdapter

__all__ = [
    'BaseExchangeAdapter',
    'OrderRequest',
    'OrderResponse',
    'Position',
    'BinanceAdapter',
    'OKXAdapter',
]

# Exchange name → Adapter class mapping
ADAPTER_MAP = {
    'binance': BinanceAdapter,
    'okx': OKXAdapter,
}
