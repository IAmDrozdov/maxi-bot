"""Diary presentation in the chat: the Nudge, in Russian (ADR-020, amended 2026-10-11)."""

from __future__ import annotations

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from shared.config import settings

NUDGE_TEXT = "📔 Сегодня в дневнике пусто"
NUDGE_BUTTON = "✍️ Записать"


def diary_link() -> str | None:
    """The Mini App opened on today's Day, or None when no Mini App URL is configured."""
    base = settings.bot_miniapp_url.strip()
    return f"{base.rstrip('/')}/?place=diary" if base else None


async def send_nudge(bot: Bot, chat: int) -> int:
    """Send the Nudge to the Owner's chat and answer its message id; Bot API errors pass through."""
    markup = None
    if link := diary_link():
        button = InlineKeyboardButton(text=NUDGE_BUTTON, web_app=WebAppInfo(url=link))
        markup = InlineKeyboardMarkup(inline_keyboard=[[button]])
    message = await bot.send_message(chat, NUDGE_TEXT, reply_markup=markup)
    return message.message_id
