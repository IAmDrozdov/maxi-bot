"""Entrypoint for the Telegram bot (long polling)."""

from __future__ import annotations

import asyncio
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BotCommand, MenuButtonDefault, MenuButtonWebApp, WebAppInfo

from shared.config import settings
from shared.obs import get_logger

from . import db
from .access import AccessMiddleware
from .cards import CardBook, DropOfferMiddleware, LiveIntake
from .diary_runtime import build_runtime as build_diary_runtime
from .handlers import setup_routers
from .notes_runtime import build_runtime as build_notes_runtime
from .tracing import init_tracing
from .worker import JobQueue, worker_loop

log = get_logger(__name__)

COMMANDS = [
    BotCommand(command="help", description="What this bot does"),
    BotCommand(command="settings", description="Languages, models, voice"),
    BotCommand(command="status", description="Current and queued jobs"),
    BotCommand(command="cancel", description="Cancel your queued jobs"),
]

MENU_BUTTON_TEXT = "📒"  # Telegram adds its own app icon; a word would squeeze the message field


async def set_owner_menu_button(bot: Bot, owner: int) -> None:
    """Point the Owner's menu button at the Mini App, or back to the default (ADR-016)."""
    url = settings.bot_miniapp_url.strip()
    button = (
        MenuButtonWebApp(text=MENU_BUTTON_TEXT, web_app=WebAppInfo(url=url))
        if url
        else MenuButtonDefault()
    )
    try:
        await bot.set_chat_menu_button(chat_id=owner, menu_button=button)
    except TelegramAPIError as exc:  # e.g. the Owner never opened the chat with the bot
        log.warning("could not set the Owner's menu button: %s", exc)


async def _run() -> None:
    token = settings.telegram_bot_token
    if token is None:
        log.error("TELEGRAM_BOT_TOKEN is not set — refusing to start")
        sys.exit(1)
    owner = settings.owner_telegram_id
    if owner is None:
        log.error("OWNER_TELEGRAM_ID is not set — refusing to start")
        sys.exit(1)

    db.init_db()
    if stale := db.reconcile_running_jobs():
        log.info("marked %d job(s) left running by a previous process as interrupted", stale)

    bot = Bot(token=token.get_secret_value(), default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    queue = JobQueue()
    dp["queue"] = queue
    dp["cards"] = CardBook(intake=LiveIntake())
    # If notes cannot start, the bot runs without it and 💾 says so (ADR-015).
    try:
        notes = build_notes_runtime()
    except Exception:
        log.exception("notes disabled: could not start the notes store")
        notes = None
    dp["notes"] = notes
    # The Nudge needs the Zone that notes keeps; without notes, or without the diary store, it stays off.
    diary = None
    if notes is not None:
        try:
            diary = build_diary_runtime(notes.db, owner)
        except Exception:
            log.exception("diary nudge disabled: could not open the diary store")
    else:
        log.warning("diary nudge disabled: notes did not start, so the Owner's Zone is unknown")
    dp.update.outer_middleware(AccessMiddleware())
    dp.message.outer_middleware(DropOfferMiddleware())  # after access: strangers never reach it
    setup_routers(dp)

    # Held so the loop task is not garbage collected mid-flight.
    background: set[asyncio.Task[None]] = set()

    async def on_startup() -> None:
        me = await bot.me()
        log.info("polling as @%s (id=%s)", me.username, me.id)
        await bot.set_my_commands(COMMANDS)
        await set_owner_menu_button(bot, owner)
        task = asyncio.create_task(worker_loop(bot, queue))
        background.add(task)
        task.add_done_callback(background.discard)
        if notes is not None:
            notes.start_sweeper(bot)
            notes.start_show_loop(bot)
            notes.start_reminder_loop(bot)
            notes.start_thumb_backfill()
        if diary is not None:
            nudge = asyncio.create_task(diary.nudge_loop(bot))
            background.add(nudge)
            nudge.add_done_callback(background.discard)

    dp.startup.register(on_startup)
    await dp.start_polling(bot)


def main() -> None:
    init_tracing(settings=settings)
    asyncio.run(_run())


if __name__ == "__main__":
    main()
