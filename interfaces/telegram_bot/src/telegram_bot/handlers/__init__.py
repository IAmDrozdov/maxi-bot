"""Router order matters for callbacks (documents claims MenuCB(page='job')) and for `/settings https://x`, `/help https://x`."""

from __future__ import annotations

from aiogram import Dispatcher

from .. import access
from . import documents, notes, settings_menu, start, status


def setup_routers(dp: Dispatcher) -> None:
    # Defense in depth: the middleware already drops everyone else, and every router
    # repeats the check, so a regression there cannot carry a stranger into a handler.
    for router in (
        start.router,
        notes.router,
        documents.router,
        settings_menu.router,
        status.router,
    ):
        router.message.filter(access.allowed_user_filter)
        router.callback_query.filter(access.allowed_user_filter)
        dp.include_router(router)
