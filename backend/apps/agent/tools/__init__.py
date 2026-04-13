from __future__ import annotations

import importlib
import logging
from pathlib import Path

from .base import BaseTool, ToolRegistry, ToolResult

logger = logging.getLogger(__name__)

# 自动发现 tools 目录下所有继承 BaseTool 的类并注册
_tools_dir = Path(__file__).parent
for _f in _tools_dir.glob("*.py"):
    if _f.name.startswith(("_", "test")) or _f.name in ("base.py", "__init__.py"):
        continue
    module_name = _f.stem
    try:
        mod = importlib.import_module(f"apps.agent.tools.{module_name}")
        for _attr in dir(mod):
            cls = getattr(mod, _attr)
            if (
                isinstance(cls, type)
                and issubclass(cls, BaseTool)
                and cls is not BaseTool
            ):
                ToolRegistry.register(cls())
                logger.debug(f"[ToolRegistry] auto-registered: {cls.__name__}")
    except Exception as e:
        logger.warning(
            f"[ToolRegistry] failed to auto-register from {module_name}: {e}"
        )

__all__ = [
    "BaseTool",
    "ToolRegistry",
    "ToolResult",
]
