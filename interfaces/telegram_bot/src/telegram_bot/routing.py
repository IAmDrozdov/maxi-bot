"""What an incoming message becomes: the one decision behind docs/runtime.md "Who gets what"."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

from aiogram.types import Message

from notes.domain.urls import extract_urls
from notes.enrich.providers import is_social_media, is_youtube

from .catalog import ALLOWED_EXTENSIONS

Media = Literal["none", "document", "photo_or_video", "voice", "other"]
Kind = Literal[
    "document_card",  # an agent document (.pdf .docx .md .markdown .txt): card, never 💾
    "link_card",  # a link to price: card (a single website link: with 💾)
    "capture_text",  # text with no URL, several, or one Instagram/YouTube/TikTok URL
    "capture_file",  # a photo, video or non-agent document
    "capture_voice",  # a voice or round video
    "ignore",  # everything else: a command, an audio, a sticker…
]


@dataclass(frozen=True)
class Incoming:
    """One message as routing reads it. Built once by `incoming_of`; plain values, no aiogram."""

    text: str  # text, or the caption of a named media kind, stripped ("" when neither)
    linked: tuple[str, ...] = ()  # URLs behind text (text_link entities), text or caption
    media: Media = "none"  # "other": audio, sticker, contact… (never routed)
    filename: str | None = None  # a document's file_name (None → "document")


@dataclass(frozen=True)
class Path:
    kind: Kind
    url: str | None = None  # link_card only: the link the card prices
    savable: bool = False  # link_card only: the card offers 💾 (ADR-015 §4, notes ADR-0009)


IGNORE = Path("ignore")


def is_agent_document(filename: str) -> bool:
    return PurePosixPath(filename.lower()).suffix in ALLOWED_EXTENSIONS


def _is_direct_host(url: str) -> bool:
    """Instagram, YouTube and TikTok links are saved as they are: there is no article to scrape."""
    return is_youtube(url) or is_social_media(url)


def incoming_of(message: Message) -> Incoming:
    """The only function here that touches aiogram; captions are read for named media kinds only."""
    media: Media
    if message.text is not None:
        media = "none"
    elif message.voice is not None or message.video_note is not None:
        media = "voice"
    elif message.photo or message.video:
        media = "photo_or_video"
    elif message.document is not None:
        media = "document"
    else:
        media = "other"
    if media == "none":
        text, entities = message.text or "", message.entities or []
    elif media == "other":
        text, entities = "", []
    else:
        text, entities = message.caption or "", message.caption_entities or []
    return Incoming(
        text=text.strip(),
        linked=tuple(e.url for e in entities if e.type == "text_link" and e.url),
        media=media,
        filename=message.document.file_name if message.document is not None else None,
    )


def route(m: Incoming) -> Path:
    """Total and pure: every Incoming gets exactly one Path."""
    if m.media == "other":
        return IGNORE
    if m.media == "voice":
        return Path("capture_voice")
    if m.media == "photo_or_video":
        return Path("capture_file")
    if m.media == "document":
        if is_agent_document(m.filename or "document"):
            return Path("document_card")  # never 💾
        return Path("capture_file")
    if m.text and not m.text.startswith("/"):
        urls = extract_urls(m.text, linked=m.linked).urls
        if len(urls) == 1 and not _is_direct_host(urls[0]):
            return Path("link_card", url=urls[0], savable=True)
        return Path("capture_text")
    urls = extract_urls(m.text).urls  # a command's link: no 💾, and its hidden links do not count
    return Path("link_card", url=urls[0]) if urls else IGNORE


def takes(*kinds: Kind) -> Callable[[Message], Awaitable[dict[str, Path] | bool]]:
    """An aiogram filter: on a match it hands the handler `path: Path`."""
    wanted = frozenset(kinds)

    async def accept(message: Message) -> dict[str, Path] | bool:
        path = route(incoming_of(message))
        return {"path": path} if path.kind in wanted else False

    return accept
