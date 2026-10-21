"""/start and /help answer the help text; /dub without a link answers a hint."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from notes.domain.urls import extract_urls

from .. import notes_ui

router = Router(name="start")

HELP_TEXT = (
    "🤖 <b>Maxi bot</b>\n\n"
    "Send me a document (<code>.pdf</code>, <code>.docx</code>, <code>.md</code>, "
    "<code>.txt</code>, <code>.html</code>, up to 20 MB) <b>or paste a link</b> (e.g. a blog "
    "article — I extract the readable text) and pick an agent:\n"
    "• 📄 <b>Document Translator</b> — chapter-by-chapter translation to Markdown\n"
    "• 🔊 <b>Document → Audio</b> — text-to-speech, optionally translated first\n"
    "• 🎬 <b>YouTube → dubbed audio</b> — send <code>/dub</code> and a YouTube link for a voice-over "
    "in your target language (a bare video link is saved to notes)\n\n"
    "Commands:\n"
    "/settings — languages, models, voice\n"
    "/status — current and queued jobs\n"
    "/cancel — cancel your queued jobs\n"
    "/help — this message\n\n"
    "One job runs at a time; extra jobs wait in a queue."
) + notes_ui.HELP


DUB_HINT = "🎬 Send <code>/dub</code> and a YouTube link: <code>/dub https://youtu.be/…</code>"


@router.message(Command("start", "help"))
async def help_handler(message: Message) -> None:
    await message.answer(HELP_TEXT)


async def _without_link(message: Message) -> bool:
    """`/dub <link>` is a link card (routing.route); only a bare `/dub` lands here."""
    return not extract_urls(message.text or "").urls


@router.message(Command("dub"), _without_link)
async def dub_hint_handler(message: Message) -> None:
    await message.answer(DUB_HINT)
