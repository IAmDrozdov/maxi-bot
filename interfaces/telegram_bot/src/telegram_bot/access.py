"""Access control: the bot answers its admin in a private chat and nobody else (ADR-022)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Message, TelegramObject, Update, User

from shared.config import settings
from shared.obs import get_logger

log = get_logger(__name__)


def is_admin(user_id: int) -> bool:
    return user_id == settings.admin_telegram_id


async def allowed_user_filter(event: Message | CallbackQuery) -> bool:
    """Defense in depth: every router repeats the middleware's check (see `handlers/__init__.py`)."""
    user = event.from_user
    return user is not None and is_admin(user.id)


class AccessMiddleware(BaseMiddleware):
    """Outer middleware on Update: anything but the admin's private chat is dropped without a reply."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Update):
            return await handler(event, data)

        message: Message | None = event.message
        callback: CallbackQuery | None = event.callback_query
        user: User | None = None
        if message and message.from_user:
            user = message.from_user
        elif callback and callback.from_user:
            user = callback.from_user

        if user is None:
            return None  # channel posts, polls, etc. — not for this bot
        if not is_admin(user.id):
            log.debug("dropped an update from telegram user %s", user.id)
            return None

        chat = None
        if message:
            chat = message.chat
        elif callback and callback.message:
            chat = callback.message.chat
        if chat is not None and chat.type != ChatType.PRIVATE:
            return None  # in a group, other members could read a job's card and its result

        return await handler(event, data)
