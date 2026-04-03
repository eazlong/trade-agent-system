from .base import BaseTool, ToolRegistry, ToolResult
from .web_search import WebSearchTool
from .web_fetch import WebFetchTool

# Register default tools
ToolRegistry.register(WebSearchTool())
ToolRegistry.register(WebFetchTool())

__all__ = ['BaseTool', 'ToolRegistry', 'ToolResult', 'WebSearchTool', 'WebFetchTool']
