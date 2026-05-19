from .base import BaseExchangeAdapter, OrderRequest, OrderResponse, Position
from .binance import BinanceAdapter

__all__ = [
    "BaseExchangeAdapter",
    "OrderRequest",
    "OrderResponse",
    "Position",
    "BinanceAdapter",
]

# Exchange name → Adapter class mapping
ADAPTER_MAP = {
    "binance": BinanceAdapter,
}
