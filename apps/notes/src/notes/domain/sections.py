"""Sections: the categories Items are filed under. Other (ADR-0002) and From the bot (ADR-0013) always exist."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from notes.db import Database
from shared.obs import get_logger

log = get_logger(__name__)

OTHER_SLUG = "other"
FROM_BOT_SLUG = "from_bot"  # slugify never makes an underscore, so the Owner cannot take it
NAME_MAX = 40
# A Section's emoji is also its Acknowledgement, so it must be a reaction the Bot API lets a bot set
# (ReactionTypeEmoji, Bot API 9.x); ✍ 👌 👎 stay the Acknowledgement's own (notes ADR-0009).
REACTIONS = (
    "❤", "👍", "👎", "🔥", "🥰", "👏", "😁", "🤔", "🤯", "😱", "🤬", "😢", "🎉", "🤩", "🤮", "💩",
    "🙏", "👌", "🕊", "🤡", "🥱", "🥴", "😍", "🐳", "❤‍🔥", "🌚", "🌭", "💯", "🤣", "⚡", "🍌",
    "🏆", "💔", "🤨", "😐", "🍓", "🍾", "💋", "🖕", "😈", "😴", "😭", "🤓", "👻", "👨‍💻", "👀",
    "🎃", "🙈", "😇", "😨", "🤝", "✍", "🤗", "🫡", "🎅", "🎄", "☃", "💅", "🤪", "🗿", "🆒", "💘",
    "🙉", "🦄", "😘", "💊", "🙊", "😎", "👾", "🤷‍♂", "🤷", "🤷‍♀", "😡",
)  # fmt: skip
RESERVED_MARKS = ("✍", "👌", "👎")
MARKS = tuple(r for r in REACTIONS if r not in RESERVED_MARKS)
# The emoji Sections had before 2026-10-18, as the Owner chose to replace them.
FORMER_MARKS = {
    "💭": "🤔", "🤖": "⚡", "💻": "🆒", "📕": "🦄", "🛠": "🔥", "💼": "👨‍💻", "🛒": "💅",
    "📖": "🤓", "🍿": "👀", "🍜": "🌭", "📍": "🎉", "📑": "👾", "👽": "👻", "📦": "👻",
}  # fmt: skip
HINT_MAX = 500
COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}")
_TRANSLIT = str.maketrans(
    {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
        "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
        "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
        "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
        "я": "ya",
    }
)  # fmt: skip


class SectionError(ValueError):
    """A Section change the Owner cannot make; the message is Russian and shown as it is."""


# slug, name, emoji, color, hint — position is the list order.
STARTER_SECTIONS: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "watch",
        "Посмотреть",
        "👀",
        "#e0523f",
        "Фильмы, сериалы, видео, стримы, записи лекций и докладов — то, что владелец будет "
        "смотреть. Например: совет посмотреть фильм (даже в рилсе), YouTube-разбор, трейлер, "
        "видеоподкаст. Ролик про место, еду или вещь — в ту секцию.",
    ),
    (
        "read",
        "Почитать",
        "🤓",
        "#3f7fe0",
        "Статьи, книги, лонгриды, треды, посты, рассылки, документация — то, что владелец будет "
        "читать. Например: статья на Хабре или telegra.ph, тред, совет прочитать книгу. Пост про "
        "место или еду — в ту секцию.",
    ),
    (
        "eat",
        "Поесть",
        "🌭",
        "#e09a3f",
        "Рестораны, кафе, бары, кофейни, рецепты, продукты и напитки. Например: рилс с рецептом, "
        "кафе, которое советуют, обзор бара. Заведение, где поесть или выпить, — сюда, а не в "
        "«Сходить».",
    ),
    (
        "buy",
        "Купить",
        "💅",
        "#8f3fe0",
        "Вещи, которые хочется купить или подарить: товары, гаджеты, одежда, мебель, подписки. "
        "Например: ссылка на маркетплейс, обзор наушников, идея подарка.",
    ),
    (
        "go",
        "Сходить",
        "🎉",
        "#3fb8a0",
        "Места и события: выставки, концерты, спектакли, музеи, парки, прогулки, поездки, города "
        "и страны. Например: афиша, место для прогулки, идея для отпуска. Рестораны и кафе — в "
        "«Поесть».",
    ),
    (
        "work",
        "Работа",
        "👨‍💻",
        "#5c6b7a",
        "Полезное для основной работы: инженерные практики, инструменты для команды, процессы, "
        "менеджмент, карьера, собеседования. Например: статья про code review, совет по найму. "
        "Свои проекты — в «Пет-проекты».",
    ),
    (
        "pet",
        "Пет-проекты",
        "🔥",
        "#3fa04a",
        "Свои проекты и идеи, что сделать самому: код, библиотеки, репозитории, туториалы, "
        "ИИ-модели и агенты, которые хочется попробовать. Например: проект на GitHub, идея своего "
        "сервиса или курса. Своя идея важнее темы ссылки. Если у проекта есть своя секция, клади "
        "туда, а не сюда.",
    ),
    (FROM_BOT_SLUG, "От бота", "👾", "#4f8a8b", ""),  # no hint: the Classifier never sees it
    (
        OTHER_SLUG,
        "Остальное",
        "👻",
        "#8a8a8a",
        "Всё, что не подходит ни под одну секцию: мысли, цитаты, личные заметки и дела, юмор, "
        "новости.",
    ),
)

# The starter hints seeded until 2026-10-07; seed_sections replaces one a built-in still holds.
FORMER_HINTS = {
    "watch": "Видео, фильмы, сериалы, ролики, стримы, лекции",
    "read": "Статьи, книги, треды, посты, документация",
    "eat": "Рестораны, кафе, бары, рецепты, еда и напитки",
    "buy": "Товары, гаджеты, одежда, подарки, всё что покупают",
    "go": "Места, мероприятия, выставки, концерты, поездки",
    "work": "Полезное для работы: инструменты, практики, карьера",
    "pet": "Библиотеки, идеи, туториалы и код для своих проектов",
    OTHER_SLUG: "Всё, что не подходит ни под одну секцию",
}


@dataclass(frozen=True)
class Section:
    id: int
    slug: str
    name: str
    emoji: str
    color: str
    hint: str
    is_builtin: bool
    position: int


def _row_to_section(row: sqlite3.Row) -> Section:
    return Section(
        id=int(row["id"]),
        slug=row["slug"],
        name=row["name"],
        emoji=row["emoji"],
        color=row["color"],
        hint=row["hint"],
        is_builtin=bool(row["is_builtin"]),
        position=int(row["position"]),
    )


def seed_sections(db: Database) -> None:
    """Fill an empty store with the starter set; a starter the Owner deleted stays deleted.

    Other and From the bot are the exceptions: each is put back whenever it is missing, Other last
    and From the bot just before it.
    """
    refreshed = 0
    with db.session() as conn:
        empty = conn.execute("SELECT COUNT(*) FROM sections").fetchone()[0] == 0
        for position, (slug, name, emoji, color, hint) in enumerate(STARTER_SECTIONS):
            if empty:
                conn.execute(
                    "INSERT INTO sections(slug, name, emoji, color, hint, is_builtin, position) "
                    "VALUES (?, ?, ?, ?, ?, 1, ?)",
                    (slug, name, emoji, color, hint, position),
                )
            elif slug == OTHER_SLUG:
                conn.execute(
                    "INSERT OR IGNORE INTO sections(slug, name, emoji, color, hint, is_builtin, position) "
                    "SELECT ?, ?, ?, ?, ?, 1, COALESCE(MAX(position), -1) + 1 FROM sections",
                    (slug, name, emoji, color, hint),
                )
            # Only a hint the Owner never changed still equals the former text, so edits stay.
            refreshed += conn.execute(
                "UPDATE sections SET hint=? WHERE slug=? AND is_builtin=1 AND hint=?",
                (hint, slug, FORMER_HINTS.get(slug)),
            ).rowcount
        if not conn.execute("SELECT 1 FROM sections WHERE slug=?", (FROM_BOT_SLUG,)).fetchone():
            from_bot = next(s for s in STARTER_SECTIONS if s[0] == FROM_BOT_SLUG)
            _insert_before_other(conn, from_bot, is_builtin=True)
        _fix_marks(conn)
    if refreshed:
        log.info("notes db: gave %d built-in sections the new starter hint", refreshed)


def mark_of(emoji: str) -> str:
    """The emoji as the Bot API spells a reaction: no variation selector."""
    return emoji.strip().replace("\ufe0f", "")


def _fix_marks(conn: sqlite3.Connection) -> None:
    """Give every Section a distinct reaction emoji: a former one maps through FORMER_MARKS, else the first free."""
    rows = conn.execute("SELECT id, name, emoji FROM sections ORDER BY position, id").fetchall()
    kept: dict[
        int, str
    ] = {}  # valid emoji are claimed first, so a remap never takes one the Owner chose
    for row in rows:
        mark = mark_of(row["emoji"])
        if mark in MARKS and mark not in kept.values():
            kept[int(row["id"])] = mark
    used = set(kept.values())
    changes: list[tuple[str, int]] = []
    for row in rows:
        mark = kept.get(int(row["id"]))
        if mark is None:
            wanted = FORMER_MARKS.get(mark_of(row["emoji"]))
            mark = (
                wanted if wanted and wanted not in used else next(m for m in MARKS if m not in used)
            )
            used.add(mark)
        if mark != row["emoji"]:
            changes.append((mark, int(row["id"])))
            log.info(
                "notes db: section %r gets the emoji %s (was %r)", row["name"], mark, row["emoji"]
            )
    conn.executemany("UPDATE sections SET emoji=? WHERE id=?", changes)


def _insert_before_other(
    conn: sqlite3.Connection, section: tuple[str, str, str, str, str], *, is_builtin: bool
) -> int | None:
    """Insert (slug, name, emoji, color, hint) where Other is, moving Other and the rest down one."""
    other = conn.execute("SELECT position FROM sections WHERE slug=?", (OTHER_SLUG,)).fetchone()
    position = int(other["position"])
    conn.execute("UPDATE sections SET position = position + 1 WHERE position >= ?", (position,))
    cur = conn.execute(
        "INSERT INTO sections(slug, name, emoji, color, hint, is_builtin, position) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (*section, int(is_builtin), position),
    )
    return cur.lastrowid


def list_sections(db: Database) -> list[Section]:
    with db.session(readonly=True) as conn:
        rows = conn.execute("SELECT * FROM sections ORDER BY position, id").fetchall()
    return [_row_to_section(row) for row in rows]


def status_counts(db: Database, status: str) -> dict[int, int]:
    """Section id -> number of Items in `status` filed under it."""
    with db.session(readonly=True) as conn:
        rows = conn.execute(
            "SELECT x.section_id AS section_id, COUNT(*) AS n FROM item_sections x "
            "JOIN items i ON i.id = x.item_id WHERE i.status=? GROUP BY x.section_id",
            (status,),
        ).fetchall()
    return {int(row["section_id"]): int(row["n"]) for row in rows}


def slugify(name: str) -> str:
    """A stable ASCII slug from a name, Cyrillic transliterated: «Подарки» becomes `podarki`."""
    latin = name.strip().lower().translate(_TRANSLIT)
    return re.sub(r"[^a-z0-9]+", "-", latin).strip("-")[:NAME_MAX].strip("-")


def _validated(name: str, emoji: str, color: str, hint: str) -> tuple[str, str, str, str]:
    name, emoji, hint = name.strip(), mark_of(emoji), hint.strip()
    if not name:
        raise SectionError("Назови секцию")
    if len(name) > NAME_MAX:
        raise SectionError(f"Название длиннее {NAME_MAX} символов")
    if not emoji:
        raise SectionError("Выбери иконку")
    if emoji not in MARKS:
        raise SectionError("Эта иконка не годится в реакцию Telegram — выбери из списка")
    if not COLOR_RE.fullmatch(color):
        raise SectionError("Цвет должен быть вида #a1b2c3")
    if len(hint) > HINT_MAX:
        raise SectionError(f"Подсказка длиннее {HINT_MAX} символов")
    return name, emoji, color, hint


def _mark_owner(
    conn: sqlite3.Connection, emoji: str, *, except_id: int | None = None
) -> str | None:
    """The name of another Section that already has this emoji."""
    row = conn.execute(
        "SELECT name FROM sections WHERE emoji=? AND id IS NOT ?", (emoji, except_id)
    ).fetchone()
    return row["name"] if row else None


def _name_taken(conn: sqlite3.Connection, name: str, *, except_id: int | None = None) -> bool:
    rows = conn.execute("SELECT id, name FROM sections").fetchall()
    return any(
        int(row["id"]) != except_id and row["name"].casefold() == name.casefold() for row in rows
    )


def create_section(
    db: Database, *, name: str, emoji: str = "", color: str = "#8a8a8a", hint: str = ""
) -> Section:
    """Add a Section just before Other; the slug comes from the name and never changes."""
    name, emoji, color, hint = _validated(name, emoji, color, hint)
    slug = slugify(name)
    if not slug:
        raise SectionError("В названии нужна хотя бы одна буква или цифра")
    with db.session() as conn:
        if _name_taken(conn, name):
            raise SectionError(f"Секция «{name}» уже есть")
        if owner := _mark_owner(conn, emoji):
            raise SectionError(f"Иконка {emoji} уже у секции «{owner}»")
        twin = conn.execute("SELECT name FROM sections WHERE slug=?", (slug,)).fetchone()
        if twin:
            raise SectionError(f"Название слишком похоже на секцию «{twin['name']}»")
        try:
            new_id = _insert_before_other(conn, (slug, name, emoji, color, hint), is_builtin=False)
        except sqlite3.IntegrityError as exc:  # a double tap created it a moment ago
            raise SectionError(f"Секция «{name}» уже есть") from exc
        row = conn.execute("SELECT * FROM sections WHERE id=?", (new_id,)).fetchone()
        return _row_to_section(row)


def update_section(
    db: Database,
    section_id: int,
    *,
    name: str | None = None,
    emoji: str | None = None,
    color: str | None = None,
    hint: str | None = None,
) -> Section | None:
    """Change what the Owner sees of a Section; None if it is missing. The slug is never touched."""
    with db.session() as conn:
        row = conn.execute("SELECT * FROM sections WHERE id=?", (section_id,)).fetchone()
        if row is None:
            return None
        name, emoji, color, hint = _validated(
            row["name"] if name is None else name,
            row["emoji"] if emoji is None else emoji,
            row["color"] if color is None else color,
            row["hint"] if hint is None else hint,
        )
        if _name_taken(conn, name, except_id=section_id):
            raise SectionError(f"Секция «{name}» уже есть")
        if owner := _mark_owner(conn, emoji, except_id=section_id):
            raise SectionError(f"Иконка {emoji} уже у секции «{owner}»")
        conn.execute(
            "UPDATE sections SET name=?, emoji=?, color=?, hint=? WHERE id=?",
            (name, emoji, color, hint, section_id),
        )
        return _row_to_section(
            conn.execute("SELECT * FROM sections WHERE id=?", (section_id,)).fetchone()
        )


def reorder_sections(db: Database, section_ids: Sequence[int]) -> list[Section]:
    """Set the list order: `section_ids` must name every Section exactly once."""
    with db.session() as conn:
        current = sorted(int(row["id"]) for row in conn.execute("SELECT id FROM sections"))
        if sorted(section_ids) != current:
            raise SectionError("Список секций изменился — обнови экран")
        conn.executemany(
            "UPDATE sections SET position=? WHERE id=?",
            [(position, section_id) for position, section_id in enumerate(section_ids)],
        )
    return list_sections(db)


def delete_section(db: Database, section_id: int) -> bool:
    """Delete a Section; Items left with none go to Other. False if missing.

    Other and From the bot are refused.
    """
    with db.session() as conn:
        row = conn.execute("SELECT slug, name FROM sections WHERE id=?", (section_id,)).fetchone()
        if row is None:
            return False
        if row["slug"] in (OTHER_SLUG, FROM_BOT_SLUG):
            raise SectionError(f"«{row['name']}» удалить нельзя")
        conn.execute(
            "INSERT INTO item_sections(item_id, section_id) "
            "SELECT item_id, ? FROM item_sections GROUP BY item_id "
            "HAVING COUNT(*) = 1 AND MIN(section_id) = ?",
            (_other_id(conn), section_id),
        )
        conn.execute("DELETE FROM sections WHERE id=?", (section_id,))
        return True


# --- Filing: which Sections an Item is in (ADR-0002: never none; Other is the fallback) ---


def _other_id(conn: sqlite3.Connection) -> int:
    """Other's id on this connection; RuntimeError if the store was never initialised."""
    row = conn.execute("SELECT id FROM sections WHERE slug=?", (OTHER_SLUG,)).fetchone()
    if row is None:
        raise RuntimeError("the Other section is missing — was the database initialised?")
    return int(row["id"])


def file_in_other(conn: sqlite3.Connection, item_id: int) -> None:
    """A new Item's Filing: Other, until the Classifier files it (ADR-0006)."""
    conn.execute(
        "INSERT INTO item_sections(item_id, section_id) VALUES (?, ?)", (item_id, _other_id(conn))
    )


def refile(conn: sqlite3.Connection, item_id: int, slugs: Sequence[str]) -> None:
    """Replace the Filing: unknown slugs are dropped; an empty Filing falls back to Other (ADR-0002).

    From the bot holds every BotResult, which is never in Other, and no other Item (ADR-0013).
    """
    item = conn.execute("SELECT kind FROM items WHERE id=?", (item_id,)).fetchone()
    if item is not None and item["kind"] == "bot_result":
        slugs = [FROM_BOT_SLUG, *(s for s in slugs if s not in (FROM_BOT_SLUG, OTHER_SLUG))]
    elif FROM_BOT_SLUG in slugs:
        slugs = [s for s in slugs if s != FROM_BOT_SLUG]
        if not slugs:  # named alone: the Item stays where it was
            return
    rows = (
        conn.execute(
            f"SELECT id FROM sections WHERE slug IN ({','.join('?' * len(slugs))})", tuple(slugs)
        ).fetchall()
        if slugs
        else []
    )
    section_ids = [int(row["id"]) for row in rows] or [_other_id(conn)]
    conn.execute("DELETE FROM item_sections WHERE item_id=?", (item_id,))
    conn.executemany(
        "INSERT INTO item_sections(item_id, section_id) VALUES (?, ?)",
        [(item_id, section_id) for section_id in section_ids],
    )


def in_other_alone(conn: sqlite3.Connection, item_id: int) -> bool:
    """Whether the Classifier may still file the Item: it is in Other alone, or in nothing (ADR-0010)."""
    rows = conn.execute(
        "SELECT s.slug FROM item_sections x JOIN sections s ON s.id = x.section_id "
        "WHERE x.item_id=?",
        (item_id,),
    ).fetchall()
    return [row["slug"] for row in rows] in ([], [OTHER_SLUG])


def filing_of(conn: sqlite3.Connection, item_id: int) -> tuple[Section, ...]:
    """The Item's Sections in list order."""
    rows = conn.execute(
        "SELECT s.* FROM sections s JOIN item_sections i ON i.section_id = s.id "
        "WHERE i.item_id=? ORDER BY s.position, s.id",
        (item_id,),
    ).fetchall()
    return tuple(_row_to_section(row) for row in rows)


def filings_of(conn: sqlite3.Connection, item_ids: Sequence[int]) -> dict[int, tuple[Section, ...]]:
    """The Sections of many Items in list order, in one read per 500 ids; an Item with none is absent."""
    found: dict[int, list[Section]] = {}
    for start in range(0, len(item_ids), 500):
        chunk = item_ids[start : start + 500]
        rows = conn.execute(
            "SELECT i.item_id, s.* FROM sections s JOIN item_sections i ON i.section_id = s.id "
            f"WHERE i.item_id IN ({','.join('?' * len(chunk))}) "
            "ORDER BY i.item_id, s.position, s.id",
            list(chunk),
        ).fetchall()
        for row in rows:
            found.setdefault(int(row["item_id"]), []).append(_row_to_section(row))
    return {item_id: tuple(sections) for item_id, sections in found.items()}
