"""The Estimate card: per-chat state, intake and rendering (docs/runtime.md "The card lifecycle")."""

from __future__ import annotations

import asyncio
import contextlib
import html
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any, Protocol

from aiogram import BaseMiddleware, Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message, TelegramObject
from pydantic import BaseModel

from notes.domain.urls import extract_urls
from notes.enrich.providers import is_youtube
from shared.config import settings
from shared.job import DocumentSource, Estimate, LinkSource, Preview, Source
from shared.obs import get_logger

from . import db, eta, user_config
from .catalog import SETTING_LABELS, option_label
from .keyboards import ActionOption, action_menu, draft_menu, offer_menu, save_only_menu
from .registry import BY_ID, RegistryEntry, entries_for
from .scrape import ScrapedArticle, ScrapeError, filename_for, scrape_url

log = get_logger(__name__)

# Intake (parsing a document, scraping a link, probing yt-dlp) is CPU/network work that
# used to share the asyncio default executor with every DB lookup and the worker itself
# — five slow previews could starve everything else (see the security review, M2). It
# now runs on its own small pool, one job in flight per user, with a hard timeout.
_INTAKE_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="intake")
_INTAKE_TIMEOUT_S = 120

# One pending item per chat; a new one replaces the previous. Entries hold the
# whole file in RAM, so an abandoned card cannot be allowed to live forever.
PENDING_TTL_S = 30 * 60
PENDING_MAX_BYTES = 60 * 1024 * 1024
SAVABLES_MAX = 50

# The only agent a voice offer prices: the text is spoken as it is, never translated (ADR-024).
VOICE_WORKFLOW = "pdf_tts"


class Intake(Protocol):
    """What a card fetches from outside: real in __main__ (LiveIntake), a stub in the §3 harness."""

    def scrape(
        self, url: str
    ) -> ScrapedArticle: ...  # blocking; ScrapeError carries a user-facing message

    def preview(
        self, entry: RegistryEntry, config: BaseModel, source: Source
    ) -> Preview: ...  # blocking, free


class LiveIntake:
    def scrape(self, url: str) -> ScrapedArticle:
        return scrape_url(url)

    def preview(self, entry: RegistryEntry, config: BaseModel, source: Source) -> Preview:
        return entry.workflow.preview(settings, config, source)


@dataclass
class Pending:
    source: Source
    message_id: int | None = None
    previews: dict[str, Preview] = field(default_factory=dict)
    estimates: dict[str, Estimate] = field(default_factory=dict)
    created_at: float = field(default_factory=time.monotonic)
    # A savable card also offers 💾 (ADR-015); what it saves lives in the book's savables.
    savable: bool = False
    # A voice offer under a long text (ADR-024): only VOICE_WORKFLOW, and "tts.translate" is off.
    voice_offer: bool = False
    # The Owner's message a voice offer replies to; any later message or card makes it stale.
    origin_id: int = 0

    @property
    def size_bytes(self) -> int:
        return len(self.source.file_bytes) if isinstance(self.source, DocumentSource) else 0

    @property
    def title(self) -> str:
        if isinstance(self.source, DocumentSource):
            return self.source.filename
        for preview in self.previews.values():
            if preview.title:
                return preview.title
        return self.source.url


@dataclass
class RunAttempt:
    """The card's claim on the chat's pending job for one Run tap."""

    pending: Pending | None  # None: this card does not own the chat's job ("expired")
    accepted: bool = False
    is_job: bool = False  # pending is None because an earlier Run of this card already queued it

    def accept(self) -> None:
        """The job was enqueued: the card is a job now."""
        self.accepted = True


def config_for(item: Pending, entry: RegistryEntry, stored: dict[str, str]) -> BaseModel:
    """The entry's config from the Owner's settings; a voice offer is spoken untranslated."""
    if item.voice_offer:
        stored = {**stored, "tts.translate": "off"}
    return entry.build_config(stored)


def _post_filename(text: str) -> str:
    """A readable `.txt` name from a post's first words (the job's title)."""
    stem = re.sub(r"\W+", "-", " ".join(text.split()[:6])).strip("-")[:40].strip("-")
    return f"{stem or 'post'}.txt"


async def delete_message(bot: Bot, chat_id: int, message_id: int) -> None:
    """Delete a bot message; one already gone or too old to delete is only logged."""
    try:
        await bot.delete_message(chat_id, message_id)
    except TelegramAPIError as exc:  # already gone, or older than Telegram lets a bot delete
        log.warning("could not delete message %s: %s", message_id, exc)


def fmt_cost(value: float) -> str:
    return f"${value:.4f}"


def _fmt_duration(seconds: float) -> str:
    return f"{seconds:.0f}s" if seconds < 60 else f"{int(seconds // 60)} min"


def _settings_hint(entry: RegistryEntry, effective: dict[str, str]) -> str:
    keys = entry.hint_keys(effective)
    return ", ".join(f"{SETTING_LABELS[key]}={option_label(effective[key])}" for key in keys)


def _card_text(
    item: Pending,
    effective: dict[str, str],
    options: list[ActionOption],
    errors: dict[str, str],
) -> str:
    if isinstance(item.source, DocumentSource):
        head = f"📎 <b>{html.escape(item.title)}</b> ({item.size_bytes / 1024:.0f} KB)"
    else:
        head = f"🎬 <b>{html.escape(item.title)}</b>"
    lines = [head]

    shown = next((item.previews[o.workflow_id] for o in options), None)
    if shown is not None:
        bits = [f"Characters: {shown.char_count}", f"Chapters: {shown.chapter_count}"]
        if shown.duration_s:
            bits.insert(0, f"Duration: {_fmt_duration(shown.duration_s)}")
        lines.append(" · ".join(bits))
        if shown.note:
            lines.append(f"Transcript: {html.escape(shown.note)}")
    lines.append("")

    for opt in options:
        entry = BY_ID[opt.workflow_id]
        lines.append(f"<b>{html.escape(entry.label)}</b>")
        lines.append(f"• {_settings_hint(entry, effective)}")
    for workflow_id, err in errors.items():
        lines.append(f"⚠️ {html.escape(BY_ID[workflow_id].label)} unavailable: {html.escape(err)}")
    return "\n".join(lines)


def _action_options(
    item: Pending, stored: dict[str, str]
) -> tuple[list[ActionOption], dict[str, str]]:
    options: list[ActionOption] = []
    errors: dict[str, str] = {}
    for workflow_id, estimate in item.estimates.items():
        entry = BY_ID[workflow_id]
        if estimate.error:
            errors[workflow_id] = estimate.error
            continue
        config = config_for(item, entry, stored)
        preview = item.previews[workflow_id]
        seconds = eta.predict_seconds(workflow_id, config, preview.char_count)
        approx = "≈ " if estimate.approximate else ""
        options.append(
            ActionOption(
                workflow_id=workflow_id,
                label=entry.short_label,
                cost_label=f"{approx}{fmt_cost(estimate.cost.total_usd)}",
                eta_label=eta.format_eta(seconds),
            )
        )
    return options, errors


class CardBook:
    """Every chat's Estimate cards: which one is live, whose pending job it is, what 💾 would save."""

    def __init__(self, intake: Intake) -> None:
        self.intake = intake
        self._pending: dict[int, Pending] = {}
        # chat id → the live voice offer (ADR-024), apart from _pending/_live: a long text never
        # takes a link or document card's job or status.
        self._offers: dict[int, Pending] = {}
        # chat id → the newest message id seen (the Owner's, or a card opened from a button): older offers are stale.
        self._newest: dict[int, int] = {}
        # (chat id, card message id) of a card whose Run was accepted: it is a job, never deleted.
        self._accepted: dict[tuple[int, int], None] = {}
        # (chat id, card message id) → what 💾 saves, until saved, cancelled, run or evicted.
        self._savables: dict[tuple[int, int], object] = {}
        # chat id → the card message the in-flight intake may still edit. Cancel and 💾 close a
        # card, so one that finishes pricing afterwards does not overwrite what the user chose; a
        # newer message supersedes it (one pending job per chat).
        self._live: dict[int, int] = {}
        self._closed: dict[tuple[int, int], None] = {}
        self._intake_locks: dict[int, asyncio.Lock] = {}
        # Also closes the daily-cap race on concurrent Run taps (L1): serializes one user's
        # estimate -> spend-check -> enqueue critical section.
        self._run_locks: dict[int, asyncio.Lock] = {}

    # --- public interface -------------------------------------------------------------

    async def offer_source(self, message: Message, source: DocumentSource, user_id: int) -> None:
        """Price card for a document the Owner sent in `message`; never savable."""
        await self._begin_pending(message, source, user_id, "⏳ Estimating…")

    async def offer_link(
        self,
        message: Message,
        url: str,
        user_id: int,
        savable: object | None = None,
        *,
        from_button: bool = False,
    ) -> None:
        """Price card for a link in `message`; with a savable it also offers 💾.

        `from_button`: `message` is the bot's own (a tapped button), not the Owner's trigger.
        """
        if is_youtube(url):
            await self._begin_pending(
                message, LinkSource(url), user_id, "📡 Fetching video info…", savable, from_button
            )
            return

        offers_save = savable is not None
        status = await self._open_card(
            message, "🔗 Fetching the link…", savable, from_button=from_button
        )
        try:
            article = await self._run_intake(user_id, self.intake.scrape, url)
        except ScrapeError as exc:
            await self._fail_card(status, f"❌ {html.escape(str(exc))}", offers_save)
            return
        except TimeoutError:
            await self._fail_card(
                status, "❌ That took too long to fetch. Please try again.", offers_save
            )
            return
        except Exception:
            log.exception("link scrape failed for %s", url)
            await self._fail_card(
                status, "❌ Something went wrong fetching that link.", offers_save
            )
            return
        if not await self._still_live(status):
            return

        source = DocumentSource(article.markdown.encode("utf-8"), filename_for(article))
        item = Pending(source=source, message_id=status.message_id, savable=offers_save)
        self._remember_pending(message.chat.id, item)
        await status.edit_text("⏳ Estimating…", reply_markup=draft_menu() if offers_save else None)
        await self._render_actions(status, user_id, item)

    async def offer_voice(self, message: Message, text: str, user_id: int) -> None:
        """Reply to a saved long text with a priced voice-over and ✖️ (ADR-024); no price, no offer."""
        chat_id, origin = message.chat.id, message.message_id
        item = Pending(
            source=DocumentSource(text.encode("utf-8"), _post_filename(text)),
            voice_offer=True,
            origin_id=origin,
        )
        try:
            stored = await asyncio.to_thread(db.get_user_settings, user_id)
            await self._run_intake(user_id, self._prepare, item, stored)
            options, errors = await asyncio.to_thread(_action_options, item, stored)
        except Exception:
            log.exception("voice offer: pricing failed")
            return
        if not options:
            log.warning("voice offer: not priced: %s", errors)
            return
        if self._is_stale(chat_id, item):  # the Owner wrote on while this was priced
            return
        words = len(extract_urls(text).annotation.split())
        try:
            offer = await message.reply(
                f"🔊 Voice this post? · {words} words", reply_markup=offer_menu(options[0])
            )
        except TelegramAPIError as exc:  # e.g. the Owner deleted the text meanwhile
            log.warning("voice offer: could not send: %s", exc)
            return
        item.message_id = offer.message_id
        if self._is_stale(chat_id, item):  # ...or during the send
            if message.bot is not None:
                await delete_message(message.bot, chat_id, offer.message_id)
            return
        stale = self._offers.pop(chat_id, None)  # no await between this and the registration below
        self._remember_offer(chat_id, item)
        if stale is not None and stale.message_id is not None and message.bot is not None:
            await delete_message(message.bot, chat_id, stale.message_id)

    def saw(self, chat_id: int, message_id: int) -> None:
        """Note the newest message in the chat: a voice offer replying to an older one is stale."""
        self._newest[chat_id] = max(self._newest.get(chat_id, 0), message_id)

    def pop_offer(self, chat_id: int) -> int | None:
        """Take the chat's live voice offer (not yet a job) out of the book; its message id, or None."""
        item = self._offers.pop(chat_id, None)
        return None if item is None else item.message_id

    async def discard_offer(
        self, bot: Bot, chat_id: int, *, origin_below: int | None = None
    ) -> None:
        """Delete the chat's live voice offer, if any (with `origin_below`: only one replying to an
        older Owner message). An offer whose Run was tapped is a job and stays."""
        item = self._offers.get(chat_id)
        if item is None or (origin_below is not None and item.origin_id >= origin_below):
            return
        message_id = self.pop_offer(chat_id)
        if message_id is not None:
            await delete_message(bot, chat_id, message_id)

    async def dismiss_offer(self, bot: Bot, chat_id: int, message_id: int, user_id: int) -> None:
        """✖️ on a voice offer: delete it, unless a Run already made it a job (then it stays)."""
        async with self._lock_for(self._run_locks, user_id):
            if (
                self._pop_job(chat_id, message_id) is None
                and (chat_id, message_id) in self._accepted
            ):
                return
        await delete_message(bot, chat_id, message_id)

    def owns_job(self, chat_id: int, message_id: int) -> bool:
        """True when this card is the chat's pending job."""
        item = self._pending.get(chat_id)
        return item is not None and item.message_id == message_id

    async def reprice(self, card: Message, user_id: int) -> bool:
        """Rebuild the card's estimates with today's settings; False if it is not the pending job."""
        item = self._pending.get(card.chat.id)
        if item is None or item.message_id != card.message_id:
            return False
        # Settings may have changed: previews stay (same source), estimates are rebuilt.
        item.estimates.clear()
        await self._render_actions(card, user_id, item)
        return True

    def run_attempt(
        self, chat_id: int, message_id: int, user_id: int, bot: Bot | None = None
    ) -> AbstractAsyncContextManager[RunAttempt]:
        """Hold the user's run lock and claim the card's job; unless accepted, it is put back.

        A voice offer rejected after a newer message arrived is deleted (via `bot`), not put back.
        """
        return self._run_attempt(chat_id, message_id, user_id, bot)

    def cancel(self, chat_id: int, message_id: int) -> None:
        """The card is neither a job nor savable any more, and no late result may edit it."""
        self._pop_job(chat_id, message_id)
        self._savables.pop((chat_id, message_id), None)
        self._close(chat_id, message_id)

    def take_savable(self, chat_id: int, message_id: int) -> object | None:
        """Claim what 💾 saves; the card stops being a job."""
        savable = self._savables.pop((chat_id, message_id), None)
        if savable is None:
            return None
        self._close(chat_id, message_id)
        self._pop_job(chat_id, message_id)
        return savable

    def restore_savable(self, chat_id: int, message_id: int, savable: object) -> None:
        """Give a savable back to its card after a failed save, so 💾 can be tried again."""
        self._remember_savable(chat_id, message_id, savable)

    # --- run attempt ------------------------------------------------------------------

    @contextlib.asynccontextmanager
    async def _run_attempt(
        self, chat_id: int, message_id: int, user_id: int, bot: Bot | None
    ) -> AsyncIterator[RunAttempt]:
        async with self._lock_for(self._run_locks, user_id):
            item = self._pop_job(chat_id, message_id)
            attempt = RunAttempt(
                item, is_job=item is None and (chat_id, message_id) in self._accepted
            )
            try:
                yield attempt
            finally:
                if attempt.pending is not None:
                    if attempt.accepted:
                        self._savables.pop((chat_id, message_id), None)
                        self._accepted[(chat_id, message_id)] = None
                        while len(self._accepted) > SAVABLES_MAX:
                            del self._accepted[next(iter(self._accepted))]
                    elif not attempt.pending.voice_offer:
                        self._remember_pending(chat_id, attempt.pending)
                    elif not self._is_stale(chat_id, attempt.pending):
                        self._remember_offer(chat_id, attempt.pending)
                    elif bot is not None:  # the Owner moved on while this Run was in flight
                        await delete_message(bot, chat_id, message_id)

    # --- state ------------------------------------------------------------------------

    @staticmethod
    def _lock_for(registry: dict[int, asyncio.Lock], user_id: int) -> asyncio.Lock:
        lock = registry.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            registry[user_id] = lock
        return lock

    async def _run_intake(self, user_id: int, func, *args):
        """Run blocking intake work off the loop, one at a time per user, with a timeout."""
        async with self._lock_for(self._intake_locks, user_id):
            loop = asyncio.get_running_loop()
            return await asyncio.wait_for(
                loop.run_in_executor(_INTAKE_EXECUTOR, func, *args),
                timeout=_INTAKE_TIMEOUT_S,
            )

    def _evict_pending(self, now: float | None = None) -> None:
        """Drop expired cards, then oldest-first until the cache fits in its budget."""
        now = time.monotonic() if now is None else now
        slots = (self._pending, self._offers)
        for slot in slots:
            for chat_id, item in list(slot.items()):
                if now - item.created_at > PENDING_TTL_S:
                    del slot[chat_id]
        total = sum(item.size_bytes for slot in slots for item in slot.values())
        if total <= PENDING_MAX_BYTES:
            return
        oldest = sorted(
            ((slot, chat_id, item) for slot in slots for chat_id, item in slot.items()),
            key=lambda entry: entry[2].created_at,
        )
        for slot, chat_id, item in oldest:
            if total <= PENDING_MAX_BYTES:
                break
            total -= item.size_bytes
            del slot[chat_id]

    def _remember_pending(self, chat_id: int, item: Pending) -> None:
        # Touch on every (re)insert, including the put-back after a rejected run, so the
        # entry a user is actively working with is never the one evicted for age.
        item.created_at = time.monotonic()
        self._pending[chat_id] = item
        self._evict_pending()

    def _remember_offer(self, chat_id: int, item: Pending) -> None:
        item.created_at = time.monotonic()
        self._offers[chat_id] = item
        self._evict_pending()

    def _is_stale(self, chat_id: int, item: Pending) -> bool:
        """True when a newer message (or a card opened from a button) than the offer's origin has appeared."""
        return self._newest.get(chat_id, 0) > item.origin_id

    def _remember_savable(self, chat_id: int, message_id: int, savable: object) -> None:
        self._savables[(chat_id, message_id)] = savable
        while len(self._savables) > SAVABLES_MAX:
            del self._savables[next(iter(self._savables))]

    def _pop_job(self, chat_id: int, message_id: int) -> Pending | None:
        """The chat's pending job (or voice offer), only if it belongs to this card."""
        for slot in (self._pending, self._offers):
            item = slot.get(chat_id)
            if item is not None and item.message_id == message_id:
                del slot[chat_id]
                return item
        return None

    def _is_live(self, message: Message) -> bool:
        return self._live.get(message.chat.id) == message.message_id

    def _close(self, chat_id: int, message_id: int) -> None:
        if self._live.get(chat_id) == message_id:
            del self._live[chat_id]
        self._closed[(chat_id, message_id)] = None
        while len(self._closed) > SAVABLES_MAX:
            del self._closed[next(iter(self._closed))]

    async def _still_live(self, status: Message) -> bool:
        """False when the card was closed or superseded; a superseded card says so."""
        if self._is_live(status):
            return True
        key = (status.chat.id, status.message_id)
        if key not in self._closed:
            await status.edit_text(
                "⏭ Replaced by your newer message.",
                reply_markup=save_only_menu() if key in self._savables else None,
            )
        return False

    # --- intake and rendering ---------------------------------------------------------

    async def _open_card(
        self,
        message: Message,
        status_text: str,
        savable: object | None,
        *,
        from_button: bool = False,
    ) -> Message:
        """The immediate answer: a status line (with 💾 / Cancel when it is savable) that becomes the card.

        Staleness keys on the triggering Owner message, never on the status id (ADR-024 §5): a card
        for an older message leaves a newer text's offer alone. A button's card drops the live offer.
        """
        status = await message.answer(
            status_text, reply_markup=draft_menu() if savable is not None else None
        )
        chat_id = message.chat.id
        self._live[chat_id] = status.message_id
        if savable is not None:
            self._remember_savable(chat_id, status.message_id, savable)
        if from_button:
            self.saw(chat_id, status.message_id)
        else:
            self.saw(chat_id, message.message_id)
        if message.bot is not None:  # a new card replaces an offer for an older message
            await self.discard_offer(
                message.bot, chat_id, origin_below=None if from_button else message.message_id
            )
        return status

    async def _fail_card(self, status: Message, text: str, offers_save: bool) -> None:
        if not await self._still_live(status):
            return
        # A failed card is no job: the chat's earlier pending card, if any, is live again.
        item = self._pending.get(status.chat.id)
        if (
            item is not None
            and item.message_id is not None
            and item.message_id != status.message_id
        ):
            self._live[status.chat.id] = item.message_id
        else:
            del self._live[status.chat.id]
        await status.edit_text(text, reply_markup=save_only_menu() if offers_save else None)

    def _prepare(self, item: Pending, stored: dict[str, str]) -> None:
        """Blocking: preview and price the source for every workflow that accepts it.

        Reuses an existing preview when one is already cached (e.g. "back to job" after a
        settings change) instead of re-fetching over the network on every tap — see the
        security review, L6. Only the estimate, which is settings-dependent, is redone.
        """
        entries = [BY_ID[VOICE_WORKFLOW]] if item.voice_offer else entries_for(item.source.kind)
        for entry in entries:
            config = config_for(item, entry, stored)
            preview = item.previews.get(entry.id)
            if preview is None:
                preview = self.intake.preview(entry, config, item.source)
                item.previews[entry.id] = preview
            if preview.error:
                item.estimates[entry.id] = Estimate.failed(preview.error)
            else:
                item.estimates[entry.id] = entry.workflow.estimate(settings, config, preview)

    async def _render_actions(self, message: Message, user_id: int, item: Pending) -> None:
        stored = await asyncio.to_thread(db.get_user_settings, user_id)
        if not item.estimates:
            try:
                await self._run_intake(user_id, self._prepare, item, stored)
            except TimeoutError:
                if self._pending.get(message.chat.id) is item:
                    del self._pending[message.chat.id]
                await self._fail_card(
                    message, "❌ That took too long to prepare. Please try again.", item.savable
                )
                return
        if not await self._still_live(message):
            return
        effective = user_config.effective_settings(stored)
        # _action_options reads the jobs table through eta.predict_seconds — off the loop.
        options, errors = await asyncio.to_thread(_action_options, item, stored)
        if not options:
            if self._pending.get(message.chat.id) is item:
                del self._pending[message.chat.id]
            detail = "; ".join(f"{a}: {e}" for a, e in errors.items()) or "unknown error"
            await self._fail_card(
                message, f"❌ Cannot process this: {html.escape(detail)}", item.savable
            )
            return
        if not await self._still_live(message):
            return
        await message.edit_text(
            _card_text(item, effective, options, errors),
            reply_markup=action_menu(options, savable=item.savable),
        )

    async def _begin_pending(
        self,
        message: Message,
        source: Source,
        user_id: int,
        status_text: str,
        savable: object | None = None,
        from_button: bool = False,
    ) -> None:
        status = await self._open_card(message, status_text, savable, from_button=from_button)
        item = Pending(source=source, message_id=status.message_id, savable=savable is not None)
        self._remember_pending(message.chat.id, item)
        await self._render_actions(status, user_id, item)


class DropOfferMiddleware(BaseMiddleware):
    """Outer message middleware: the Owner's next message of any kind deletes the live voice offer."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        cards, bot = data.get("cards"), data.get("bot")
        if isinstance(event, Message) and isinstance(cards, CardBook) and bot is not None:
            cards.saw(event.chat.id, event.message_id)  # first: a pricing offer must see it
            await cards.discard_offer(bot, event.chat.id)
        return await handler(event, data)
