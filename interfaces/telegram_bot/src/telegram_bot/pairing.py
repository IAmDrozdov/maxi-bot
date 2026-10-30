"""A short text sent in the same second as a share or forward is that Capture's Annotation (ADR-025)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from shared.obs import get_logger

from . import routing

log = get_logger(__name__)

HOLD_S = 1.5  # how long a short text waits for the post it was sent with
SAME_SECOND_S = 1  # Telegram dates are whole seconds: the post carries the same one or the next
KEEP_S = 60  # a taken text stays takeable this long, for the rest of a multi-message forward


@dataclass
class _Held:
    words: str
    date: datetime
    taken: bool = False
    released: bool = False


class Pairing:
    """Per chat, the Owner's recent short texts that may still be the Annotation of the next message."""

    def __init__(self) -> None:
        self._held: dict[int, dict[int, _Held]] = {}

    def hold(self, chat_id: int, message_id: int, date: datetime, words: str) -> None:
        held = self._held.setdefault(chat_id, {})
        for mid in [m for m, h in held.items() if (date - h.date).total_seconds() > KEEP_S]:
            del held[mid]
        held[message_id] = _Held(words, date)

    def take(self, chat_id: int, message_id: int, date: datetime) -> str | None:
        """The newest earlier short text sent within a second of this message, or None."""
        held = self._held.get(chat_id, {})
        earlier = [m for m in held if m < message_id]
        if not earlier:
            return None
        text = held[max(earlier)]
        if text.released or (date - text.date).total_seconds() > SAME_SECOND_S:
            return None
        text.taken = True
        return text.words

    def release(self, chat_id: int, message_id: int) -> bool:
        """True: nothing took the text, so it is a Capture of its own and no later message takes it."""
        text = self._held.get(chat_id, {}).get(message_id)
        if text is None:
            return True
        if text.taken:
            return False
        text.released = True
        return True


class PairingMiddleware(BaseMiddleware):
    """Outer message middleware, first after access: a short text waits for the post it was sent with."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        pairing = data.get("pairing")
        if not isinstance(event, Message) or not isinstance(pairing, Pairing):
            return await handler(event, data)
        chat, mid = event.chat.id, event.message_id
        incoming = routing.incoming_of(event)
        # No await before hold or take: updates start in order, so a text is held before its post looks.
        if routing.may_annotate(incoming):
            pairing.hold(chat, mid, event.date, incoming.text)
            await asyncio.sleep(HOLD_S)
            if not pairing.release(chat, mid):
                log.info("pairing: message %s is the Annotation of the post sent with it", mid)
                return None
        elif routing.takes_annotation(incoming):
            annotation = pairing.take(chat, mid, event.date)
            if annotation is not None:
                data["annotation"] = annotation
        return await handler(event, data)
