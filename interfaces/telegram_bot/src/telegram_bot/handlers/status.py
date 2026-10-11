"""/status and /cancel."""

from __future__ import annotations

import html

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from ..registry import label_for
from ..worker import Job, JobQueue

router = Router(name="status")


def _describe(job: Job) -> str:
    return f"{label_for(job.workflow_id)} — <b>{html.escape(job.title)}</b>"


@router.message(Command("status"))
async def status_handler(message: Message, queue: JobQueue) -> None:
    lines: list[str] = []
    if queue.current is not None:
        lines.append(f"▶️ Running: {_describe(queue.current)}")
    for position, job in enumerate(queue.snapshot(), start=1):
        lines.append(f"{position}. {_describe(job)}")
    if not lines:
        await message.answer("Nothing running and the queue is empty.")
        return
    await message.answer("\n".join(lines))


@router.message(Command("cancel"))
async def cancel_handler(message: Message, queue: JobQueue) -> None:
    user = message.from_user
    if user is None:
        return
    count = queue.cancel_for_user(user.id)
    if count:
        await message.answer(f"✖️ Cancelled {count} queued job(s).")
    elif queue.current is not None and queue.current.user_id == user.id:
        await message.answer(
            "The running job cannot be interrupted; only queued jobs can be cancelled."
        )
    else:
        await message.answer("You have no queued jobs.")
