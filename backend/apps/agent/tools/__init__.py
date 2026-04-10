from .base import BaseTool, ToolRegistry, ToolResult
from .web_search import WebSearchTool
from .web_fetch import WebFetchTool
from .market_data import FetchOHLCVTool, CalculateIndicatorsTool

# Register default tools
ToolRegistry.register(WebSearchTool())
ToolRegistry.register(WebFetchTool())
ToolRegistry.register(FetchOHLCVTool())
ToolRegistry.register(CalculateIndicatorsTool())

__all__ = [
    'BaseTool',
    'ToolRegistry',
    'ToolResult',
    'WebSearchTool',
    'WebFetchTool',
    'FetchOHLCVTool',
    'CalculateIndicatorsTool',
]
