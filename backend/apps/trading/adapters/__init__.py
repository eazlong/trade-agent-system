from .base import (
    BaseExchangeAdapter,
    OrderLookupUnavailableError,
    OrderLookupUnsupportedError,
    OrderNotFoundError,
    OrderPlacementUnknown,
    OrderRequest,
    OrderResponse,
    OrderFill,
    Position,
)
from .binance import BinanceAdapter

__all__ = [
    "BaseExchangeAdapter",
    "OrderRequest",
    "OrderResponse",
    "OrderFill",
    "OrderNotFoundError",
    "OrderLookupUnavailableError",
    "OrderLookupUnsupportedError",
    "OrderPlacementUnknown",
    "Position",
    "BinanceAdapter",
]

# Exchange name → Adapter class mapping
ADAPTER_MAP = {
    "binance": BinanceAdapter,
}
