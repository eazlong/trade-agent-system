from .base import BaseTool, ToolRegistry, ToolResult
from .web_search import WebSearchTool
from .web_fetch import WebFetchTool
from .file_io import ReadFileTool, WriteFileTool
from .market_data import FetchOHLCVTool, CalculateIndicatorsTool
from .system_status import GetSystemStatusTool
from .load_skill import LoadSkillTool

# Register default tools
ToolRegistry.register(WebSearchTool())
ToolRegistry.register(WebFetchTool())
ToolRegistry.register(ReadFileTool())
ToolRegistry.register(WriteFileTool())
ToolRegistry.register(LoadSkillTool())
ToolRegistry.register(FetchOHLCVTool())
ToolRegistry.register(CalculateIndicatorsTool())
ToolRegistry.register(GetSystemStatusTool())

__all__ = [
    'BaseTool',
    'ToolRegistry',
    'ToolResult',
    'ReadFileTool',
    'WriteFileTool',
    'WebSearchTool',
    'WebFetchTool',
    'FetchOHLCVTool',
    'CalculateIndicatorsTool',
    'GetSystemStatusTool'
]