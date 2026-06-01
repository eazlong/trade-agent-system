"""Channel resolution utility — determines which channel (Telegram/Feishu) is active for a user."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ChannelTarget:
    """Resolved channel info for sending a notification to a user."""
    channel_type: str  # "telegram" | "lark"
    chat_id: str  # Telegram chat_id or Feishu chat_id
    user_open_id: str = ""  # Feishu open_id (empty for Telegram)


def get_user_active_channel(user_id: str) -> Optional[ChannelTarget]:
    """Determine the active notification channel for a user.

    Priority: Feishu > Telegram (if user has both, prefer Feishu as it's
    the more recently configured channel).

    Args:
        user_id: Telegram user ID (numeric string) or Feishu open_id.

    Returns:
        ChannelTarget if a channel is found, None if user has no channel configured.
    """
    if not user_id or user_id == "unknown":
        return None

    from apps.authentication.models import User

    # Try to find user by telegram_id first
    user = User.objects.filter(telegram_id=user_id).first()
    if not user:
        user = User.objects.filter(feishu_open_id=user_id).first()
    if not user:
        logger.debug("[ChannelResolver] user %s not found in DB", user_id)
        return None

    # Prefer Feishu if configured (more recently active)
    if user.feishu_open_id:
        return ChannelTarget(
            channel_type="lark",
            chat_id="",  # Resolved at send time from LarkChannel instance
            user_open_id=user.feishu_open_id,
        )

    if user.telegram_chat_id:
        return ChannelTarget(
            channel_type="telegram",
            chat_id=str(user.telegram_chat_id),
        )

    logger.debug(
        "[ChannelResolver] user %s has no active channel", user_id
    )
    return None


def resolve_user_channel_for_task(task_data: dict) -> Optional[ChannelTarget]:
    """Resolve channel from a task's Redis data or user_id.

    The task tracker stores `channel` and `user_id` in the Redis hash.
    This function combines those with DB lookup to find the right target.

    Args:
        task_data: Dict from TaskTracker Redis hash (contains user_id, channel).

    Returns:
        ChannelTarget or None.
    """
    user_id = task_data.get("user_id", "")
    stored_channel = task_data.get("channel", "telegram")

    if not user_id or user_id == "unknown":
        return None

    # If the task was explicitly tagged with lark, honor it
    if stored_channel == "lark":
        return ChannelTarget(
            channel_type="lark",
            chat_id="",
            user_open_id=user_id,
        )

    if stored_channel == "telegram":
        target = get_user_active_channel(user_id)
        if target and target.channel_type == "telegram":
            return target

    # DB-based resolution (covers both channels)
    return get_user_active_channel(user_id)
