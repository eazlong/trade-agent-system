"""Tests for channel module."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock


class TestTelegramChannel(unittest.TestCase):
    """测试 Telegram Channel"""

    def setUp(self):
        from apps.channel.telegram import TelegramChannel

        mock_supervisor = MagicMock()
        self.channel = TelegramChannel(
            token="test_token", supervisor_agent=mock_supervisor
        )

    def test_channel_name(self):
        self.assertEqual(self.channel.name, "telegram")

    def test_send_message_without_chat_id(self):
        """没有 chat_id 时不应发送消息"""
        self.channel._chat_id = None
        self.channel._app = MagicMock()
        result = asyncio.run(self.channel.send_message("test"))
        self.assertIsNone(result)

    def test_send_message_with_chat_id(self):
        """有 chat_id 时应发送消息"""
        self.channel._chat_id = 12345
        mock_bot = AsyncMock()
        mock_bot.send_message = AsyncMock()
        mock_app = MagicMock()
        mock_app.bot = mock_bot
        self.channel._app = mock_app

        asyncio.run(self.channel.send_message("Hello"))
        mock_bot.send_message.assert_called_once_with(chat_id=12345, text="Hello")

    def test_send_message_handles_error(self):
        """发送消息失败时应静默处理"""
        self.channel._chat_id = 12345
        mock_bot = AsyncMock()
        mock_bot.send_message = AsyncMock(side_effect=Exception("Network error"))
        mock_app = MagicMock()
        mock_app.bot = mock_bot
        self.channel._app = mock_app

        # 不应抛出异常
        asyncio.run(self.channel.send_message("test"))

    def test_send_photo_without_chat_id(self):
        """没有 chat_id 时不应发送照片"""
        self.channel._chat_id = None
        result = asyncio.run(self.channel.send_photo(b"image_data", "caption"))
        self.assertIsNone(result)

    def test_cmd_start_handler(self):
        """测试 /start 命令处理"""
        mock_update = MagicMock()
        mock_update.effective_chat.id = 99999
        mock_reply = AsyncMock()
        mock_update.message.reply_text = mock_reply

        asyncio.run(self.channel._cmd_start(mock_update, MagicMock()))
        self.assertEqual(self.channel._chat_id, 99999)
        mock_reply.assert_called_once()

    def test_cmd_help_handler(self):
        """测试 /help 命令处理"""
        mock_update = MagicMock()
        mock_reply = AsyncMock()
        mock_update.message.reply_text = mock_reply

        asyncio.run(self.channel._cmd_help(mock_update, MagicMock()))
        mock_reply.assert_called_once()


class TestBaseChannel(unittest.TestCase):
    """测试基础 Channel 类"""

    def test_base_channel_is_abstract(self):
        """BaseChannel 是抽象类，不能直接实例化"""
        from apps.channel.base import BaseChannel

        # 尝试实例化抽象类应抛出 TypeError
        with self.assertRaises(TypeError):
            BaseChannel()
