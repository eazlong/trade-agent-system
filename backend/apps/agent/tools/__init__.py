from .base import BaseTool, ToolRegistry, ToolResult
from .web_search import WebSearchTool
from .web_fetch import WebFetchTool
from .system_status import GetSystemStatusTool

# Register default tools
ToolRegistry.register(WebSearchTool())
ToolRegistry.register(WebFetchTool())
ToolRegistry.register(GetSystemStatusTool())

__all__ = ['BaseTool', 'ToolRegistry', 'ToolResult', 'WebSearchTool', 'WebFetchTool', 'GetSystemStatusTool']
