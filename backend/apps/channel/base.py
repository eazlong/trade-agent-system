from __future__ import annotations
import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class BaseChannel(ABC):
    """用户交互入口抽象接口。Phase1: Telegram。Phase2: Web。"""

    name: str = 'base'

    @abstractmethod
    async def send_message(self, text: str) -> None:
        """向用户发送文本消息"""

    @abstractmethod
    async def send_photo(self, photo_bytes: bytes, caption: str = '') -> None:
        """向用户发送图片（K线图、回测结果等）"""

    @abstractmethod
    async def start(self) -> None:
        """启动Channel监听"""

    @abstractmethod
    async def stop(self) -> None:
        """停止Channel监听"""
