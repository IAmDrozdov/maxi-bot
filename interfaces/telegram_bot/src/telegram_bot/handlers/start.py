"""/start and /help: both answer the help text."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from .. import notes_ui

router = Router(name="start")

HELP_TEXT = (
    "🤖 <b>Maxi bot</b>\n\n"
    "Send me a document (<code>.pdf</code>, <code>.docx</code>, <code>.md</code>, "
    "<code>.txt</code>, up to 20 MB) <b>or paste a link</b> (e.g. a blog "
    "article — I extract the readable text) and pick an agent:\n"
    "• 📄 <b>Document Translator</b> — chapter-by-chapter translation to Markdown\n"
    "• 🔊 <b>Document → Audio</b> — text-to-speech, optionally translated first\n"
    "• 🎬 <b>YouTube → dubbed audio</b> — paste a YouTube link, get a voice-over in your target language\n\n"
    "Commands:\n"
    "/settings — languages, models, voice\n"
    "/status — current and queued jobs\n"
    "/cancel — cancel your queued jobs\n"
    "/help — this message\n\n"
    "One job runs at a time; extra jobs wait in a queue."
) + notes_ui.HELP


@router.message(Command("start", "help"))
async def help_handler(message: Message) -> None:
    await message.answer(HELP_TEXT)
