"""The diary runtime in the bot: the Nudge pass over diary.sqlite3 (ADR-020, amended 2026-10-11)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from diary import domain
from diary.db import Database as DiaryDatabase
from notes.db import Database as NotesDatabase
from notes.domain import clock
from shared.config import settings
from shared.obs import get_logger

from . import diary_ui
from .notes_ui import RETRYABLE

log = get_logger(__name__)

NUDGE_POLL_S = 60


@dataclass
class DiaryRuntime:
    db: DiaryDatabase
    notes_db: NotesDatabase  # the Owner's Zone lives in notes
    chat: int  # the Owner's chat

    async def nudge_loop(self, bot: Bot) -> None:
        """Nudge the Owner about an untouched Day; the database is the claim (ADR-020, amended)."""
        while True:
            try:
                await self.nudge_once(bot)
            except Exception:
                log.exception("diary: nudge pass failed; will try again")
            await asyncio.sleep(NUDGE_POLL_S)

    async def nudge_once(self, bot: Bot, now: datetime | None = None) -> bool:
        """One Nudge pass at `now`: claim today's Day if it is due and untouched, and send; True when sent."""
        zone = await asyncio.to_thread(clock.owner_zone, self.notes_db)
        local_now = (now or datetime.now(UTC)).astimezone(clock.zone(zone))
        day = domain.nudge_day(local_now)
        if day is None:
            return False
        if not await asyncio.to_thread(domain.claim_nudge, self.db, day):
            return False
        try:
            message_id = await diary_ui.send_nudge(bot, self.chat)
        except RETRYABLE as exc:
            log.warning("diary: could not send the nudge for %s, will retry: %s", day, exc)
            await asyncio.to_thread(domain.release_nudge, self.db, day)
            return False
        except TelegramAPIError as exc:  # a refusal is not retried every minute: the claim stays
            log.warning("diary: could not send the nudge for %s: %s", day, exc)
            return False
        except Exception:
            log.exception("diary: nudge for %s failed", day)
            await asyncio.to_thread(domain.release_nudge, self.db, day)
            return False
        await asyncio.to_thread(domain.keep_nudge_message, self.db, day, message_id)
        return True


def build_runtime(notes_db: NotesDatabase, chat: int) -> DiaryRuntime:
    db = DiaryDatabase(settings.diary_db_path)
    db.init()
    return DiaryRuntime(db=db, notes_db=notes_db, chat=chat)
