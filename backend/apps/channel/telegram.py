from __future__ import annotations
import logging
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from .base import BaseChannel, chunk_message

logger = logging.getLogger(__name__)

# Telegram API 限制 4096 字符，留余量给格式化
_TG_MAX_LENGTH = 4000


class TelegramChannel(BaseChannel):
    """Phase 1 Channel：Telegram Bot"""

    name = "telegram"

    def __init__(self, token: str, supervisor_agent):
        self._token = token
        self._supervisor = supervisor_agent
        self._app = None
        self._chat_id: int | None = None

    async def send_message(self, text: str) -> None:
        if self._chat_id and self._app:
            try:
                for chunk in chunk_message(text, _TG_MAX_LENGTH):
                    await self._app.bot.send_message(chat_id=self._chat_id, text=chunk)
            except Exception as e:
                logger.error(f"Failed to send message: {e}")

    async def send_photo(self, photo_bytes: bytes, caption: str = "") -> None:
        if self._chat_id and self._app:
            try:
                await self._app.bot.send_photo(
                    chat_id=self._chat_id, photo=photo_bytes, caption=caption
                )
            except Exception as e:
                logger.error(f"Failed to send photo: {e}")

    async def start(self) -> None:
        from django.conf import settings

        proxy = getattr(settings, "TELEGRAM_PROXY", "") or None
        builder = ApplicationBuilder().token(self._token)
        if proxy:
            builder = builder.proxy(proxy).get_updates_proxy(proxy)
        self._app = builder.build()
        self._app.add_handler(CommandHandler("start", self._cmd_start))
        self._app.add_handler(CommandHandler("status", self._cmd_status))
        self._app.add_handler(CommandHandler("stop", self._cmd_stop))
        self._app.add_handler(CommandHandler("help", self._cmd_help))
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message)
        )
        logger.info("[TelegramChannel] starting polling")
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()

    async def stop(self) -> None:
        if self._app:
            try:
                await self._app.updater.stop()
                await self._app.stop()
                await self._app.shutdown()
                logger.info("[TelegramChannel] stopped")
            except Exception as e:
                logger.error(f"[TelegramChannel] error during stop: {e}")

    # --- command handlers ---
    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self._chat_id = update.effective_chat.id
        telegram_id = str(update.effective_user.id) if update.effective_user else None
        username = update.effective_user.username if update.effective_user else None

        # 保存 telegram_id 和 telegram_chat_id 到数据库
        if telegram_id:
            self._save_telegram_user(telegram_id, self._chat_id, username)

        await update.message.reply_text(
            "TradeAgent已就绪。发送任何消息开始交互，或使用 /help 查看指令。"
        )

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        from apps.agent.frame_manager import FrameManager

        fm = FrameManager.get_instance()
        status = fm.status()
        await update.message.reply_text(f"框架状态：{status}")

    async def _cmd_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        from apps.agent.frame_manager import FrameManager

        fm = FrameManager.get_instance()
        await fm.stop_all()
        await update.message.reply_text("所有框架已停止。")

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = (
            "/start  — 初始化对话\n"
            "/status — 查看当前框架运行状态\n"
            "/stop   — 停止所有运行中的框架\n"
            "/help   — 显示本帮助\n"
            "\n直接发送消息即可与Agent交互。"
        )
        await update.message.reply_text(help_text)

    def _save_telegram_user(
        self, telegram_id: str, chat_id: int, username: str | None
    ) -> None:
        """保存或更新 Telegram 用户信息到数据库"""
        try:
            from apps.authentication.models import User

            user = User.objects.filter(telegram_id=telegram_id).first()
            if not user:
                user = User.objects.filter(username=telegram_id).first()
            if user:
                changed = False
                if user.telegram_id != telegram_id:
                    user.telegram_id = telegram_id
                    changed = True
                if user.telegram_chat_id != chat_id:
                    user.telegram_chat_id = chat_id
                    changed = True
                if changed:
                    user.save(update_fields=["telegram_id", "telegram_chat_id"])
                    logger.info(
                        "Saved telegram info for user %s: telegram_id=%s, chat_id=%s",
                        user.username,
                        telegram_id,
                        chat_id,
                    )
            else:
                logger.debug(
                    "No Django user found for telegram_id %s, chat_id still tracked in memory",
                    telegram_id,
                )
        except Exception as e:
            logger.warning("Failed to save telegram user info: %s", e)

    async def _on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self._chat_id = update.effective_chat.id
        text = update.message.text
        user_id = str(update.effective_user.id) if update.effective_user else "unknown"
        username = update.effective_user.username if update.effective_user else None

        # 保存用户的 telegram_chat_id 到数据库（用于信号通知）
        if user_id != "unknown":
            self._save_telegram_user(user_id, self._chat_id, username)

        # 检查是否是特殊命令来结束多轮对话
        if text.lower() in ["/end", "/stop", "/quit", "/exit", "结束", "停止", "退出"]:
            # 发送特殊消息来结束多轮对话
            from apps.agent.bus import (
                publish,
                build_agent_task,
                wait_reply,
                AGENT_TASKS,
            )

            msg = build_agent_task(
                user_id=user_id,
                payload={"text": text, "intent": "end_multi_turn"},
                origin="telegram",
            )
            await publish(AGENT_TASKS, msg)

            reply = await wait_reply(msg["task_id"], timeout=3600)
            if reply:
                # 如果返回的是字典格式，提取content部分
                processed_reply = self._extract_content_from_response(reply)
                # 发送可能较长的消息，分段处理
                await self._send_long_message(update, processed_reply)
            else:
                await update.message.reply_text("[超时] Agent 处理超时，请稍后重试")
            return

        from apps.agent.bus import publish, build_agent_task, wait_reply, AGENT_TASKS

        msg = build_agent_task(user_id=user_id, payload={"text": text}, origin="telegram")
        await publish(AGENT_TASKS, msg)

        reply = await wait_reply(msg["task_id"], timeout=3600)
        if reply:
            # 处理Agent返回的响应格式，只提取内容部分
            processed_reply = self._extract_content_from_response(reply)
            # 发送可能较长的消息，分段处理
            await self._send_long_message(update, processed_reply)
        else:
            await update.message.reply_text("[超时] Agent 处理超时，请稍后重试")

    def _extract_content_from_response(self, reply):
        """
        从Agent的响应中提取内容部分
        如果reply是字典格式（包含content等字段），只返回content
        如果reply是字符串，直接返回
        """
        import json

        try:
            # 尝试解析JSON字符串
            if isinstance(reply, str) and reply.strip().startswith("{"):
                parsed = json.loads(reply)
                if isinstance(parsed, dict) and "content" in parsed:
                    return parsed["content"]
                return reply
            elif isinstance(reply, dict) and "content" in reply:
                return reply["content"]
            else:
                return str(reply)
        except (json.JSONDecodeError, TypeError):
            # 如果解析失败，直接返回原始内容
            return str(reply)

    async def _send_long_message(self, update, message_text):
        """发送可能很长的消息，自动分段处理。"""
        import asyncio

        chunks = chunk_message(message_text, _TG_MAX_LENGTH)
        for chunk in chunks:
            await update.message.reply_text(chunk)
            if len(chunks) > 1:
                await asyncio.sleep(0.5)
