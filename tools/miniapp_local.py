"""Dev launcher: run the Mini App on throwaway DBs with a signed local URL (docs/verifying.md §2)."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import shutil
import signal
import socket
import sqlite3
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlencode

REPO_ROOT = Path(__file__).resolve().parent.parent
WORK = REPO_ROOT / ".local" / "miniapp-local"
URL_FILE = REPO_ROOT / ".local" / "miniapp_url.txt"
PID_FILE = WORK / "miniapp.pid"
LOG_FILE = WORK / "server.log"
NOTES_DB = WORK / "notes.sqlite3"
BOT_DB = WORK / "bot.sqlite3"
DIARY_DB = WORK / "diary.sqlite3"
PROFILE_FILE = WORK / "profile"  # written by `up --demo`; the README shoot checks it

TEST_OWNER_ID = 100  # the only id the local server admits
HOST = "127.0.0.1"
SOURCE_NOTES = "notes.sqlite3"
SOURCE_BOT = "telegram_bot.sqlite3"
SOURCE_DIARY = "diary.sqlite3"


def init_secret(token: str) -> str:
    """Hex of HMAC("WebAppData", token), the same derivation as infrastructure/deploy.sh."""
    return hmac.new(b"WebAppData", token.encode(), hashlib.sha256).hexdigest()


def sign_init_data(secret: str) -> str:
    """URL-encoded initData signed the way Telegram does; the key is the raw bytes of the hex secret."""
    fields = {
        "query_id": "AAHlocal",
        "user": json.dumps({"id": TEST_OWNER_ID, "first_name": "Local"}, separators=(",", ":")),
        "auth_date": str(int(time.time())),
        "signature": "bG9jYWwtc2lnbmF0dXJl",
    }
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    key = bytes.fromhex(secret)
    fields["hash"] = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def launch_url(port: int, secret: str) -> str:
    """The page URL carrying initData in the fragment, as telegram-web-app.js expects."""
    data = quote(sign_init_data(secret), safe="")
    return f"http://{HOST}:{port}/#tgWebAppData={data}&tgWebAppVersion=8.0&tgWebAppPlatform=web"


def seed_notes(path: Path) -> None:
    """A few 🧪 Sections and Items across statuses: Links, Notes, an overdue reminder, three BotResults."""
    from notes.db import Database
    from notes.domain import items, reminders, sections

    db = Database(str(path))
    db.init()
    lab = sections.create_section(db, name="🧪 Lab", emoji="🧪", color="#7a5cff", hint="local test")
    spare = sections.create_section(
        db, name="🧪 Spare", emoji="🧪", color="#1fa37a", hint="local test"
    )
    now = datetime.now(UTC)
    for i in range(6):
        created = now - timedelta(days=i, hours=i)
        capture = items.capture_link(
            db, f"https://example.com/local-{i}", f"🧪 link {i}", now=created
        )
        items.store_enrichment(
            db,
            capture.item.id,
            sections=[(lab.slug, spare.slug, "watch")[i % 3]],
            title=f"🧪 Link {i}",
            gist=f"🧪 Gist {i}",
            source="example.com",
            now=created,
        )
    for i in range(3):
        note = items.capture_note(db, f"🧪 note {i}", now=now - timedelta(hours=i))
        items.store_enrichment(db, note.id, sections=[lab.slug], title=f"🧪 Note {i}", now=now)
    items.edit_item(db, 1, status="done", now=now)
    overdue = items.capture_note(db, "🧪 overdue", now=now - timedelta(days=2))
    stored = items.store_enrichment(
        db, overdue.id, sections=[spare.slug], title="🧪 Overdue", now=now
    )
    reminders.fill_from_filing(
        db, stored, (now - timedelta(days=1)).replace(tzinfo=None), "UTC", now=now
    )
    results = (  # title, Workflow id (the last one is unknown to the page), file name, mime, size, duration
        ("🧪 Перевод статьи", "doc_translator", "article.ru.md", "text/markdown", 348_160, None),
        ("🧪 Аудио статьи", "pdf_tts", None, "audio/ogg", 2_400_000, 754),
        ("🧪 Новый воркфлоу", "new_flow", "notes.txt", "text/plain", 512, None),
    )
    for n, (title, workflow, name, mime, size, duration) in enumerate(results):
        items.capture_bot_result(
            db,
            title=title,
            workflow=workflow,
            file_id=f"local-result-{n}",
            file_name=name,
            file_mime=mime,
            file_size=size,
            duration_s=duration,
            chat_id=TEST_OWNER_ID,
            message_id=9000 + n,
            now=now - timedelta(minutes=n),
        )


def seed_diary(path: Path) -> None:
    """Four 🧪 Weeks up to yesterday (always across a month boundary): Marks of all three kinds,
    Entries at every Level, a gap; today is left empty so the Streak counts from yesterday."""
    from diary import domain
    from diary.db import Database

    db = Database(str(path))
    db.init()
    pool = ("🧪 тренировка", "🧪 читал", "🧪 код", "🧪 прогулка", "🧪 готовил", "🧪 английский")
    today = date.today()
    for back in range(1, 29):
        day = today - timedelta(days=back)
        if back == 9:
            domain.set_mark(db, day, "meh")  # a Mark-only Day breaks the Streak
            continue
        for n in range(1 + back % 3):
            domain.add_entry(db, day, pool[(back + n * 2) % len(pool)])
        domain.set_mark(db, day, (None, "fire", "meh", "dead")[back % 4])
    raised = {"week": (2, 5, 12), "month": (5, 12), "year": (12,)}
    for level, backs in raised.items():
        for back in backs:
            first = domain.day(db, today - timedelta(days=back)).entries[0]
            for _ in range(domain.LEVELS.index(level) - domain.LEVELS.index(first.level)):
                domain.raise_entry(db, first.id)


def seed_jobs(path: Path) -> None:
    """A few usage rows for the «Расходы» screen; the server has already created the schema."""
    rows = [
        (TEST_OWNER_ID, "owner", "pdf_tts", "🧪 a.pdf", "ok", 0.12, f"-{n} days") for n in range(4)
    ] + [(TEST_OWNER_ID, "owner", "doc_translator", "🧪 b.docx", "ok", 0.31, "-1 days")]
    with sqlite3.connect(path) as conn:
        conn.executemany(
            "INSERT INTO jobs(telegram_id, username, agent, filename, status, cost_usd, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, datetime('now', ?))",
            rows,
        )


@dataclass(frozen=True)
class DemoItem:
    kind: str  # link | note | file | voice
    title: str
    sections: tuple[str, ...]  # Section slugs
    age_h: int  # hours since it was captured
    url: str = ""
    source: str | None = None
    author: str | None = None
    gist: str | None = None
    caption: str | None = None
    own: str = ""  # the Owner's words saved with a Link or File
    cover: tuple[str, str] | None = None  # two colours of the Thumbnail gradient
    due_days: int | None = None  # a Due this many days from now (19:00); negative: already overdue
    file: tuple[str, str, int] | None = None  # name, mime, bytes
    duration_s: int | None = None
    transcript: str | None = None


# README demo (docs/verifying.md §2): invented, Russian, no 🧪. URLs carry fake ids on real hosts.
DEMO_SECTIONS = (  # name, emoji, colour, hint; added to the starters of Database.init()
    ("Учёба", "🎓", "#c2571a", "Курсы, английский, конспекты и задания"),
    ("Дом", "🏠", "#7a5cff", "Ремонт, мебель, быт и всё для квартиры"),
)
DEMO_ITEMS = (
    DemoItem(
        "link",
        "Как устроен трансформер: разбор за 20 минут",
        ("watch", "pet"),
        3,
        url="https://www.youtube.com/watch?v=Kx3mT7vQp2Y",
        source="youtube.com",
        author="Нейрокухня",
        own="Пригодится для доклада на митапе",
        gist="Пошагово: как слой внимания превращает слова в числа и почему этого хватает для перевода и чата.",
        caption="Разбор архитектуры трансформера: токены, внимание, обучение. Без математики, с примерами.",
        cover=("#1f3a8a", "#38bdf8"),
        due_days=3,
    ),
    DemoItem(
        "link",
        "Сырники без муки за 15 минут",
        ("eat", "watch"),
        22,
        url="https://www.instagram.com/reel/C9xKq2LmPzA/",
        source="instagram.com",
        caption="Творог, яйцо и ложка крахмала. Жарить по три минуты с каждой стороны.",
        cover=("#f59e0b", "#fde68a"),
    ),
    DemoItem(
        "link",
        "Три упражнения для спины за рабочим столом",
        ("watch",),
        30,
        url="https://www.tiktok.com/@spinaok/video/7312345678901234567",
        source="tiktok.com",
        cover=("#10b981", "#a7f3d0"),
    ),
    DemoItem(
        "link",
        "Как выступать, чтобы слушали: лекция",
        ("watch", "ucheba"),
        52,
        url="https://www.youtube.com/watch?v=Zr8wN5eHb1U",
        source="youtube.com",
        cover=("#7c3aed", "#f0abfc"),
    ),
    DemoItem("note", "Посмотреть «Дюну: часть вторую» в выходные", ("watch",), 76),
    DemoItem(
        "file",
        "Запись вебинара по SQLite",
        ("watch", "work"),
        100,
        file=("sqlite-webinar.mp4", "video/mp4", 18_400_000),
        cover=("#0f766e", "#5eead4"),
    ),
    DemoItem(
        "link",
        "Postgres как очередь задач: когда хватит SKIP LOCKED",
        ("work", "read"),
        120,
        url="https://habr.com/ru/articles/812345/",
        source="habr.com",
        gist="Когда очередь на Postgres проще отдельного брокера: FOR UPDATE SKIP LOCKED, повторы и мониторинг.",
    ),
    DemoItem(
        "link",
        "Наушники с шумоподавлением: сравнение трёх моделей",
        ("buy",),
        140,
        url="https://market.yandex.ru/journal/obzory/shumopodavlenie-trekh-modelej",
        source="market.yandex.ru",
        cover=("#334155", "#94a3b8"),
    ),
    DemoItem(
        "link",
        "Выставка «Космос: начало» в Политехе",
        ("go",),
        160,
        url="https://polymus.ru/ru/events/kosmos-nachalo/",
        source="polymus.ru",
    ),
    DemoItem(
        "link",
        "sqlite-vec: векторный поиск внутри SQLite",
        ("pet",),
        180,
        url="https://github.com/vecdb-labs/sqlite-vec-notes",
        source="github.com",
    ),
    DemoItem("note", "Купить подарок маме до пятницы", ("buy",), 48, due_days=-1),
    DemoItem("note", "Идея: бот, который раз в неделю присылает итоги дневника", ("pet",), 200),
    DemoItem("note", "Разобрать конспект по Present Perfect", ("ucheba",), 220),
    DemoItem(
        "file",
        "План квартиры после ремонта",
        ("dom",),
        240,
        file=("kvartira-plan.jpg", "image/jpeg", 920_000),
        cover=("#be185d", "#fbcfe8"),
    ),
    DemoItem(
        "voice",
        "Мысль про доклад на митапе",
        ("work",),
        260,
        duration_s=47,
        transcript="Хочу рассказать на митапе, как перевёл заметки на SQLite. Три слайда: зачем, "
        "как устроено и что получилось. И показать живой пример.",
    ),
    DemoItem(
        "link",
        "Как выбрать ортопедический матрас",
        ("dom",),
        280,
        url="https://vc.ru/life/987654-kak-vybrat-ortopedicheskiy-matras",
        source="vc.ru",
    ),
)
DEMO_BOT_RESULTS = (  # title, Workflow id, file name, mime, bytes, seconds, hours old
    ("Устойчивые системы: глава 5", "doc_translator", "ustoychivye-sistemy-gl5.md", "text/markdown", 41_000, None, 5),
    ("Письмо акционерам 2025", "pdf_tts", None, "audio/ogg", 8_100_000, 1284, 26),
    ("Как выступать, чтобы слушали", "yt_dub", None, "audio/ogg", 24_300_000, 3780, 54),
)  # fmt: skip
DEMO_DONE = (  # titles of the done notes, with their Section slug
    ("Оплатить интернет", "other"), ("Рецепт хумуса", "eat"), ("Записаться к стоматологу", "other"),
    ("Заменить лампочку в коридоре", "dom"), ("Дочитать главу про индексы", "read"),
    ("Отправить отчёт по спринту", "work"), ("Забронировать столик на субботу", "eat"),
    ("Обновить резюме", "work"), ("Позвонить в сервис по гарантии", "buy"),
    ("Повторить неправильные глаголы", "ucheba"), ("Сходить на каток", "go"),
    ("Разобрать антресоль", "dom"), ("Настроить резервные копии", "pet"),
    ("Купить корм коту", "buy"), ("Сдать книги в библиотеку", "read"),
    ("Записать вебинар в календарь", "work"), ("Выбрать маршрут на выходные", "go"),
    ("Поменять фильтр в кухне", "dom"), ("Дописать заметки к докладу", "work"),
    ("Заказать цветы на юбилей", "buy"),
)  # fmt: skip
DEMO_DIARY = (
    "Тренировка", "Английский 30 минут", "Прогулка 8000 шагов", "Читал книгу",
    "Готовил ужин", "Писал код для бота", "Растяжка", "Позвонил родителям",
)  # fmt: skip
# agent, file, days ago, status, cost USD, seconds, chars, input tokens, output tokens, TTS chars billed
DEMO_JOBS = (
    ("doc_translator", "resilient-systems-ch5.pdf", 0, "ok", 0.4132, 211.4, 38_900, 21_500, 19_800, None),
    ("pdf_tts", "shareholder-letter-2025.pdf", 0, "ok", 0.1827, 96.2, 12_150, None, None, 12_150),
    ("yt_dub", "Как выступать, чтобы слушали", 1, "ok", 1.8640, 1204.0, 61_200, 34_000, 31_400, 61_200),
    ("doc_translator", "api-guidelines.docx", 1, "ok", 0.1264, 58.7, 11_800, 6_900, 6_300, None),
    ("pdf_tts", "chapter-3.md", 2, "ok", 0.1123, 71.5, 9_400, None, None, 9_400),
    ("doc_translator", "sqlite-internals.pdf", 2, "ok", 0.8921, 402.9, 82_300, 45_000, 41_200, None),
    ("pdf_tts", "interview-prep.docx", 3, "ok", 0.2210, 128.0, 17_600, None, None, 17_600),
    ("doc_translator", "meeting-notes.txt", 3, "ok", 0.0214, 12.3, 2_100, 1_200, 1_100, None),
    ("yt_dub", "Как устроен трансформер: разбор за 20 минут", 4, "ok", 0.9420, 640.2, 33_500, 18_000, 16_900, 33_500),
    ("doc_translator", "release-notes-2.4.md", 5, "error", 0.0231, 41.8, 7_400, 4_100, 0, None),
    ("pdf_tts", "terms-of-service.pdf", 6, "ok", 0.3318, 190.6, 24_900, None, None, 24_900),
    ("doc_translator", "thesis-draft.pdf", 7, "ok", 1.2406, 588.1, 118_000, 64_000, 59_500, None),
    ("pdf_tts", "whitepaper.pdf", 8, "ok", 0.5604, 322.4, 40_300, None, None, 40_300),
    ("doc_translator", "contract.docx", 9, "ok", 0.1688, 77.9, 15_600, 8_700, 8_100, None),
    ("doc_translator", "essay.txt", 10, "interrupted", None, None, None, None, None, None),
    ("yt_dub", "Рецепт пасты за 10 минут", 11, "ok", 1.1302, 780.5, 40_100, 21_600, 20_300, 40_100),
    ("pdf_tts", "faq.md", 12, "ok", 0.0467, 31.2, 3_900, None, None, 3_900),
    ("doc_translator", "essay-2.txt", 13, "ok", 0.0392, 22.0, 4_300, 2_400, 2_200, None),
)  # fmt: skip
DEMO_ERROR = "[provider] OpenAI rate limit: 429 after 3 retries"


def _cover(a: str, b: str, size: int = 216) -> bytes:
    """A size x size PNG: a diagonal gradient from colour a to colour b, stdlib only."""
    ca, cb = ([int(c[i : i + 2], 16) for i in (1, 3, 5)] for c in (a, b))
    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            t = (x + y) / (2 * size - 2)
            rows += bytes(round(ca[k] + (cb[k] - ca[k]) * t) for k in range(3))

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + chunk(b"IEND", b"")
    )


def _at_19(moment: datetime, days: int) -> datetime:
    """`days` from `moment`, at 19:00 UTC."""
    return (moment + timedelta(days=days)).replace(hour=19, minute=0, second=0, microsecond=0)


def seed_demo_notes(path: Path) -> None:
    """The README's notes: 16 todo Items and 3 BotResults over 11 Sections, 40 done notes over 26 weeks."""
    from notes.db import Database
    from notes.domain import items, reminders, sections

    db = Database(str(path))
    db.init()
    for name, emoji, color, hint in DEMO_SECTIONS:
        sections.create_section(db, name=name, emoji=emoji, color=color, hint=hint)
    now = datetime.now(UTC)
    chat = TEST_OWNER_ID
    for n, demo in enumerate(DEMO_ITEMS, start=1):
        created, msg = now - timedelta(hours=demo.age_h), 7000 + n
        name, mime, size = demo.file or (None, "audio/ogg", 190_000)
        if demo.kind == "link":
            item = items.capture_link(
                db, demo.url, demo.own, chat_id=chat, message_id=msg, now=created
            ).item
        elif demo.kind == "note":
            item = items.capture_note(db, demo.title, chat_id=chat, message_id=msg, now=created)
        elif demo.kind == "file":
            preview = (_cover(*demo.cover), "image/png") if demo.cover else None
            item = items.capture_file(
                db, file_id=f"demo-file-{n}", file_name=name, file_mime=mime, file_size=size,
                annotation=demo.own, preview=preview, chat_id=chat, message_id=msg, now=created,
            )  # fmt: skip
        else:
            item = items.capture_voice(
                db, file_id=f"demo-voice-{n}", file_mime=mime, file_size=size,
                duration_s=demo.duration_s, annotation="", preview=None,
                chat_id=chat, message_id=msg, now=created,
            )  # fmt: skip
        if demo.transcript:
            items.store_transcript(db, item.id, demo.transcript)
        stored = items.store_enrichment(
            db, item.id, sections=demo.sections, title=demo.title, gist=demo.gist,
            source=demo.source, author=demo.author, caption=demo.caption, now=created,
        )  # fmt: skip
        if demo.cover:
            items.store_thumb(db, item.id, _cover(*demo.cover), "image/png")
        if demo.due_days is not None and demo.due_days < 0:
            overdue = _at_19(now, demo.due_days).replace(tzinfo=None)
            reminders.fill_from_filing(db, stored, overdue, "UTC", now=now)
        elif demo.due_days is not None:
            items.edit_item(db, item.id, due=_at_19(now, demo.due_days), now=now)
    for n, (title, workflow, name, mime, size, seconds, hours) in enumerate(DEMO_BOT_RESULTS):
        items.capture_bot_result(
            db, title=title, workflow=workflow, file_id=f"demo-result-{n}", file_name=name,
            file_mime=mime, file_size=size, duration_s=seconds, chat_id=chat,
            message_id=7200 + n, now=now - timedelta(hours=hours),
        )  # fmt: skip
    for i in range(40):  # both heatmaps need weeks of history
        title, slug = DEMO_DONE[i % len(DEMO_DONE)]
        created = now - timedelta(days=10 + (i * 37) % 170, hours=(i * 5) % 14)
        note = items.capture_note(db, title, chat_id=chat, message_id=7100 + i, now=created)
        items.store_enrichment(db, note.id, sections=[slug], title=title, now=created)
        done = created + timedelta(days=1 + (i * 5) % 9)
        items.edit_item(db, note.id, status="done", now=done)


def seed_demo_diary(path: Path) -> None:
    """300 Days back through today: gaps, a 29-day Streak, Marks, and a raised highlight per Week."""
    from diary import domain
    from diary.db import Database

    db = Database(str(path))
    db.init()
    today = datetime.now(UTC).date()  # the shoot's browser runs in UTC
    marks = ("fire", "meh", "fire", "meh", None)
    for back in range(300):
        day = today - timedelta(days=back)
        if back > 20 and (back % 11 == 7 or back % 17 == 3):
            continue
        count = 4 if back == 0 else 1 + back % 3
        for k in range(count):
            domain.add_entry(db, day, DEMO_DIARY[(back + k * 3) % len(DEMO_DIARY)])
        mark = "fire" if back == 0 else "dead" if back % 13 == 0 else marks[back % 5]
        if mark:
            domain.set_mark(db, day, mark)
    first_monday = domain.monday(today - timedelta(days=299))
    for w in range((domain.monday(today) - first_monday).days // 7 + 1):
        days = [first_monday + timedelta(weeks=w, days=d) for d in range(7)]
        written = [e for e in (domain.day(db, d).entries for d in days if d <= today) if e]
        if not written:
            continue
        target = "year" if w % 13 == 0 else "month" if w % 4 == 0 else "week"
        pick = written[w % len(written)][0]
        for _ in range(domain.LEVELS.index(target) - domain.LEVELS.index(pick.level)):
            domain.raise_entry(db, pick.id)


def seed_demo_jobs(path: Path) -> None:
    """18 usage rows over 14 days, written as finish_job writes them, so every «Расходы» tile has a number."""
    translation = {
        "source_language": "English",
        "target_language": "Russian",
        "model": "gpt-5.6-luna",
    }
    speech = {"model": "gpt-4o-mini-tts", "voice": "marin"}
    configs = {
        "doc_translator": {"translation": translation},
        "pdf_tts": {"speech": speech, "translation": None},
        "yt_dub": {"speech": speech, "translation": translation},
    }
    now = datetime.now(UTC).replace(microsecond=0)
    midnight = now.replace(hour=0, minute=0, second=0)
    rows = []
    # Oldest first, so ids rise with time as in a real log (DEMO_JOBS lists newest first).
    for n, (agent, name, days, status, cost, secs, chars, tin, tout, tts) in reversed(
        list(enumerate(DEMO_JOBS))
    ):
        failed = status == "error"
        if days < 8:  # finish_job writes no tokens or TTS characters: only older rows carry them
            tin = tout = tts = None
        stats = {} if status != "ok" else {
            "cost": {"total_usd": cost or 0.0, "lines": [{"label": "Total", "usd": cost or 0.0}]},
            "facts": [{"label": "Characters", "value": f"{chars:,}"}] if chars else [],
        }  # fmt: skip
        # A fixed hour of its day (today's rows minutes ago), so the run's hour moves no row across a day.
        created = (
            now - timedelta(minutes=5 + 7 * n)
            if days == 0
            else midnight - timedelta(days=days) + timedelta(hours=9 + n % 9)
        )
        finished = created + timedelta(seconds=int(secs or 0))
        rows.append(
            (
                TEST_OWNER_ID, "owner", agent, name, chars * 2 if chars else None,
                None if failed else chars, None if failed else chars, tin, tout, tts, cost, secs, status,
                DEMO_ERROR if failed else None, json.dumps(configs[agent]), json.dumps(stats),
                f"{created:%Y-%m-%d %H:%M:%S}", f"{finished:%Y-%m-%d %H:%M:%S}",
            )
        )  # fmt: skip
    with sqlite3.connect(path) as conn:
        conn.executemany(
            "INSERT INTO jobs(telegram_id, username, agent, filename, file_size_bytes, "
            "output_size_bytes, char_count, input_tokens, output_tokens, tts_chars_billed, "
            "cost_usd, duration_s, status, error, config_json, stats_json, created_at, finished_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )


def demo_profile() -> dict[str, object]:
    """What the shoot asserts: the «Сделать» count and the rail's captions, read back from the demo DB."""
    with sqlite3.connect(NOTES_DB) as conn:
        todo = conn.execute("SELECT COUNT(*) FROM items WHERE status='todo'").fetchone()[0]
        names = [row[0] for row in conn.execute("SELECT name FROM sections ORDER BY position, id")]
    rail = ["Просрочено", *(name.strip()[:10].rstrip() for name in names), "Готово", "Правка"]
    return {"profile": "demo", "todo": todo, "rail": rail}


def copy_db(src: Path, dst: Path) -> None:
    """A consistent copy through the sqlite backup API (the source may be in WAL mode)."""
    with sqlite3.connect(f"file:{src}?mode=ro", uri=True) as a, sqlite3.connect(dst) as b:
        a.backup(b)


def port_busy(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex((HOST, port)) == 0


def healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/healthz", timeout=2) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_pid() -> int | None:
    try:
        return int(PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return None


def is_ours(pid: int) -> bool:
    """The PID still runs this launcher's server (a stale PID file may name a reused PID)."""
    cmd = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True)
    return "telegram_bot.miniapp" in cmd.stdout


def stop(pid: int) -> None:
    """SIGTERM the server's process group, then SIGKILL if it lingers."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            return
        for _ in range(50):
            if not pid_alive(pid):
                return
            time.sleep(0.1)


def up(args: argparse.Namespace) -> int:
    pid = read_pid()
    if pid is not None and pid_alive(pid):
        print(f"already running (pid {pid}); run `down` first", file=sys.stderr)
        return 1
    if port_busy(args.port):
        print(f"port {args.port} is taken", file=sys.stderr)
        return 1
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True, mode=0o700)
    try:
        return start(args)
    except BaseException:
        pid = read_pid()
        if pid is not None and pid_alive(pid) and is_ours(pid):
            stop(pid)  # a failure after the health check must not strand the server
        shutil.rmtree(WORK, ignore_errors=True)
        raise


def start(args: argparse.Namespace) -> int:
    """Copy or seed the DBs, start the server with a per-run key, write the signed URL."""
    if args.demo:
        seed_demo_notes(NOTES_DB)
        seed_demo_diary(DIARY_DB)
    elif args.from_db_dir:
        source = Path(args.from_db_dir).expanduser()
        if not (source / SOURCE_NOTES).is_file():
            print(f"{source / SOURCE_NOTES} not found", file=sys.stderr)
            return 1
        copy_db(source / SOURCE_NOTES, NOTES_DB)
        if (source / SOURCE_BOT).is_file():
            copy_db(source / SOURCE_BOT, BOT_DB)
        if (source / SOURCE_DIARY).is_file():
            copy_db(source / SOURCE_DIARY, DIARY_DB)
    else:
        seed_notes(NOTES_DB)
        seed_diary(DIARY_DB)
    for db in WORK.glob("*.sqlite3*"):
        db.chmod(0o600)

    # A random token per run: the signed URL is the only way in, and the real token never appears.
    secret = init_secret(f"42:{secrets.token_urlsafe(24)}")
    # Minimal env and a cwd without .env: the real token and keys never reach the server.
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "OWNER_TELEGRAM_ID": str(TEST_OWNER_ID),
        "NOTES_DB_PATH": str(NOTES_DB),
        "TELEGRAM_DB_PATH": str(BOT_DB),
        "DIARY_DB_PATH": str(DIARY_DB),
        "MINIAPP_INIT_SECRET": secret,
    }
    with LOG_FILE.open("wb") as log:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "telegram_bot.miniapp",
                "--host",
                HOST,
                "--port",
                str(args.port),
            ],
            cwd=WORK,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    PID_FILE.write_text(f"{proc.pid}\n")

    deadline = time.monotonic() + 30
    while not healthy(args.port):
        if proc.poll() is not None or time.monotonic() > deadline:
            print(f"server did not become healthy; see {LOG_FILE}", file=sys.stderr)
            stop(proc.pid)
            shutil.rmtree(WORK, ignore_errors=True)
            return 1
        time.sleep(0.3)

    if args.demo:
        seed_demo_jobs(BOT_DB)
        PROFILE_FILE.write_text(json.dumps(demo_profile(), ensure_ascii=False) + "\n")
    elif not args.from_db_dir:
        seed_jobs(BOT_DB)
    URL_FILE.parent.mkdir(parents=True, exist_ok=True)
    URL_FILE.unlink(missing_ok=True)
    URL_FILE.touch(mode=0o600)
    URL_FILE.write_text(launch_url(args.port, secret) + "\n")
    print(f"port: {args.port}\nurl file: {URL_FILE}\npid file: {PID_FILE}")
    return 0


def down(_: argparse.Namespace) -> int:
    pid = read_pid()
    if pid is not None and pid_alive(pid) and is_ours(pid):
        stop(pid)
    shutil.rmtree(WORK, ignore_errors=True)
    URL_FILE.unlink(missing_ok=True)
    print("stopped; throwaway directory removed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_up = sub.add_parser("up", help="seed throwaway DBs, start the server, write the signed URL")
    source = p_up.add_mutually_exclusive_group()
    source.add_argument(
        "--from-db-dir",
        metavar="DIR",
        help="copy notes.sqlite3, telegram_bot.sqlite3 and diary.sqlite3 from DIR",
    )
    source.add_argument(
        "--demo",
        action="store_true",
        help="seed the invented Russian README demo data instead of the 🧪 test data",
    )
    p_up.add_argument("--port", type=int, default=18083)
    p_up.set_defaults(func=up)
    p_down = sub.add_parser("down", help="stop the server and remove the throwaway directory")
    p_down.set_defaults(func=down)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
