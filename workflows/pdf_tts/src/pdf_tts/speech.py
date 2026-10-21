"""Speech preparation (pure): Markdown chapter -> ordered prose and figure segments (ADR-023)."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, replace
from pathlib import PurePosixPath

MARKDOWN_SUFFIXES = {".md", ".markdown", ".html", ".htm"}
SOURCE_CAP_CHARS = 6000
BEFORE_CAP_CHARS = 600
CAPTION_CAP_CHARS = 300
# Image URLs that name a small thumbnail (".../20px-icon.png") are page chrome, not content.
_ICON_WIDTH_PX = 64

_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})\s*(.*)$")
_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_RULE_RE = re.compile(r"^ {0,3}([-*_])(\s*\1){2,}\s*$")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(\s*([^)\s]*)(?:\s+\"[^\"]*\")?\s*\)")
_LINKED_IMAGE_RE = re.compile(r"\[\s*(!\[[^\]]*\]\([^)]*\))\s*\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]*)\]\((?:[^()]|\([^()]*\))*\)")
_REF_LINK_RE = re.compile(r"\[([^\]]+)\]\[[^\]]*\]")
_AUTOLINK_RE = re.compile(r"<(?:https?://|mailto:)[^>\s]+>")
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>]+")
_URL_TAIL = ".,;:!?)»]\"'”’"
_CLOSERS = {")": "(", "]": "["}
# The lookbehind makes a run of spaces scan once.
_URL_WITH_GAP_RE = re.compile(r"(?<![ \t])[ \t]*(" + _URL_RE.pattern + ")")
_CITE_RE = re.compile(r"\[\^[\w-]+\]|\[\d+(?:\s*[,–-]\s*\d+)*\](?!\()|(?<![\d)\]])\^\d+")
_HTML_TAG_RE = re.compile(r"</?[A-Za-z][^<>]{0,2000}>|<!--.{0,10000}?-->", re.DOTALL)
_HTML_IMG_RE = re.compile(r"<img\b[^<>]{0,2000}>", re.IGNORECASE)
_HTML_ATTR_RE = re.compile(r"""\b([\w-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""")
_FRONT_KEY_RE = re.compile(r"^[\w-]+\s*:")
_FRONT_LINE_RE = re.compile(
    r"^(?:[\w-]+\s*:|[ \t]+\S|- |#)"
)  # a key, a continuation, an item, a comment
_SETEXT_RE = re.compile(r"^ {0,3}(?:={2,}|-{2,})\s*$")
_REF_DEF_RE = re.compile(
    r"""^ {0,3}\[[^\]]+\]:\s*(?:<[^>]+>|(?:https?://|www\.|mailto:|/|\./|\.\./|#)\S*)"""
    r"""(?:\s+(?:"[^"]*"|'[^']*'|\([^)]*\)))?\s*$"""
)
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d{1,3}[.)])\s+")
_CODE_SPAN_RE = re.compile(r"(`+)(.+?)\1", re.DOTALL)
_BOLD_RE = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
_STAR_RE = re.compile(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])")
_UNDER_RE = re.compile(r"(?<![\w_])_(?=\S)([^_\n]+?)(?<=\S)_(?![\w_])")
_STRIKE_RE = re.compile(r"~~(.+?)~~")
_SENTENCE_END = ".!?…:;»)\"'”"
_WIDTH_RE = re.compile(r"(\d+)px-")


@dataclass(frozen=True)
class Figure:
    """Something the listener cannot hear as prose: code, a diagram, a chart, a picture, a table."""

    kind: str  # code | mermaid | chart | svg | image | table
    source: str  # capped source text; for an image, its alt
    heading: str = ""
    before: str = ""
    after: str = ""
    info: str = ""  # fence info string, e.g. `markdown title="SKILL.md"`
    url: str = ""  # image only


@dataclass(frozen=True)
class Segment:
    text: str = ""
    figure: Figure | None = None


def is_markdown_source(filename: str) -> bool:
    """Markdown rules apply to .md/.markdown/.html sources; plain-text sources only lose bare URLs."""
    return PurePosixPath(filename.lower()).suffix in MARKDOWN_SUFFIXES


def strip_urls(text: str) -> str:
    """Bare URLs removed, along with the horizontal space in front of them."""
    return _URL_WITH_GAP_RE.sub(_url_tail, text)


def _url_tail(match: re.Match[str]) -> str:
    """What follows a URL but is not part of it: closing punctuation stays in the text."""
    url = match.group(1)
    end = len(url)
    while end and url[end - 1] in _URL_TAIL:
        last = url[end - 1]
        pair = _CLOSERS.get(last)
        if pair and url.count(last, 0, end) <= url.count(pair, 0, end):
            break  # a bracket the URL itself opened, e.g. .../Foo_(bar)
        end -= 1
    return url[end:]


def _tidy(text: str) -> str:
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"""\s+([,.;:!?])(?=\s|$|[»"'”’)\]])""", r"\1", text)
    return text.strip()


def speakable_inline(text: str) -> str:
    """One paragraph of Markdown as text for the ear: no marks, links, URLs or markers."""
    codes: list[str] = []

    def keep_code(match: re.Match[str]) -> str:
        codes.append(match.group(2).replace("`", "").strip())
        return f"\x00{len(codes) - 1}\x00"

    text = _CODE_SPAN_RE.sub(keep_code, text)
    text = html.unescape(_HTML_TAG_RE.sub(" ", text))
    text = _LINKED_IMAGE_RE.sub(r"\1", text)
    text = _IMAGE_RE.sub(lambda m: m.group(1), text)
    text = _AUTOLINK_RE.sub("", text)
    text = _LINK_RE.sub(r"\1", text)
    text = _REF_LINK_RE.sub(r"\1", text)
    text = _CITE_RE.sub("", text)
    text = strip_urls(text)
    for pattern, repl in (
        (_BOLD_RE, r"\2"),
        (_STAR_RE, r"\1"),
        (_UNDER_RE, r"\1"),
        (_STRIKE_RE, r"\1"),
    ):
        text = pattern.sub(repl, text)
    text = re.sub(r"\\([\\`*_{}\[\]()#+\-.!|>~])", r"\1", text)
    text = re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], text)
    return _tidy(text)


def _sentence(text: str) -> str:
    return text if not text or text[-1] in _SENTENCE_END else text + "."


def _prose_lines(lines: list[str]) -> str:
    """List/quote/heading marks off a run of prose lines, one speakable paragraph."""
    cleaned: list[str] = []
    for line in lines:
        line = re.sub(r"^\s*(?:>\s?)+", "", line)
        line = _BULLET_RE.sub("", line)
        if _RULE_RE.match(line):
            continue
        cleaned.append(line.strip())
    return speakable_inline(" ".join(part for part in cleaned if part))


def _is_icon(url: str) -> bool:
    m = _WIDTH_RE.search(url)
    return m is not None and int(m.group(1)) < _ICON_WIDTH_PX


def _cap(text: str) -> str:
    return text if len(text) <= SOURCE_CAP_CHARS else text[:SOURCE_CAP_CHARS] + "\n[...cut]"


def _figure_kind(info: str) -> str:
    word = info.split(maxsplit=1)[0].lower() if info.strip() else ""
    return word if word in ("mermaid", "chart", "svg") else "code"


def _indented(line: str) -> bool:
    return line.startswith(("    ", "\t"))


def _dedent(line: str) -> str:
    return line[1:] if line.startswith("\t") else line[4:]


def _blocks(chapter: str) -> list[tuple[str, str, str]]:
    """(kind, info, text) blocks in order: heading, fence, table, paragraph (kind 'p')."""
    out: list[tuple[str, str, str]] = []
    lines = chapter.replace("\r\n", "\n").split("\n")
    para: list[str] = []
    in_list = False  # the last paragraph was a list item or its indented continuation
    i = 0
    if lines[0].rstrip() == "---" and len(lines) > 1 and _FRONT_KEY_RE.match(lines[1]):
        # YAML front matter is metadata, not speech; a block with a non-YAML line stays prose
        for end in range(1, len(lines)):
            if lines[end].rstrip() in ("---", "..."):
                i = end + 1
                break
            if not _FRONT_LINE_RE.match(lines[end]):
                break

    def flush() -> None:
        nonlocal in_list
        if para:
            out.append(("p", "", "\n".join(para)))
            if _BULLET_RE.match(para[0]):
                in_list = True
            elif not para[0].startswith((" ", "\t")):
                in_list = False
            para.clear()

    while i < len(lines):
        line = lines[i]
        fence = _FENCE_RE.match(line)
        heading = _HEADING_RE.match(line)
        is_table = (
            line.lstrip().startswith("|")
            and i + 1 < len(lines)
            and _TABLE_SEP_RE.match(lines[i + 1]) is not None
        )
        if fence:
            flush()
            in_list = False
            mark, info = fence.group(1), fence.group(2).strip()
            body: list[str] = []
            i += 1
            while i < len(lines):
                closing = lines[i].strip()
                if closing.startswith(mark[0] * len(mark)) and set(closing) == {mark[0]}:
                    break
                body.append(lines[i])
                i += 1
            out.append(("fence", info, "\n".join(body).strip("\n")))
        elif is_table:
            flush()
            in_list = False
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append(lines[i].strip())
                i += 1
            out.append(("table", "", "\n".join(rows)))
            continue
        elif heading:
            flush()
            in_list = False
            out.append(("heading", "", heading.group(2)))
        elif (
            para
            and _SETEXT_RE.match(line)
            and not _BULLET_RE.match(para[0])
            and not _RULE_RE.match(para[0])
        ):
            text = " ".join(part.strip() for part in para)
            para.clear()
            in_list = False
            out.append(("heading", "", text))
        elif not line.strip():
            flush()
        elif _REF_DEF_RE.match(line):
            pass  # a link reference definition: nothing to say
        elif not para and not in_list and _indented(line):
            body = []
            while i < len(lines) and (_indented(lines[i]) or not lines[i].strip()):
                body.append(_dedent(lines[i]) if lines[i].strip() else "")
                i += 1
            out.append(("fence", "", "\n".join(body).strip("\n")))
            continue
        else:
            para.append(line)
        i += 1
    flush()
    return out


def _html_image(match: re.Match[str]) -> str:
    """A raw <img> as Markdown image syntax, so it becomes a figure like any other picture."""
    attrs = {
        m.group(1).lower(): m.group(2) or m.group(3) or m.group(4) or ""
        for m in _HTML_ATTR_RE.finditer(match.group(0))
    }
    src = (attrs.get("src") or attrs.get("data-src") or "").strip()
    alt = html.unescape(attrs.get("alt", "")).replace("[", "(").replace("]", ")").strip()
    if src.startswith("data:"):
        src = ""
    return f"![{alt}]({src.replace(' ', '%20').replace(')', '%29')})" if (src or alt) else ""


def _paragraph_segments(text: str, heading: str, before: str) -> list[Segment]:
    """Prose and inline pictures of one paragraph, in order."""
    text = _LINKED_IMAGE_RE.sub(r"\1", _HTML_IMG_RE.sub(_html_image, text))
    segments: list[Segment] = []
    pos = 0
    ctx_before = before
    for m in _IMAGE_RE.finditer(text):
        lead = _prose_lines(text[pos : m.start()].split("\n"))
        if lead:
            segments.append(Segment(text=lead))
            ctx_before = lead
        alt = html.unescape(m.group(1)).strip()
        url = m.group(2).strip()
        if not _is_icon(url):
            segments.append(
                Segment(
                    figure=Figure(
                        kind="image",
                        source=alt,
                        heading=heading,
                        before=ctx_before[-BEFORE_CAP_CHARS:],
                        url=url,
                    )
                )
            )
        pos = m.end()
    tail = _prose_lines(text[pos:].split("\n"))
    if tail:
        segments.append(Segment(text=tail))
    return segments


def split_segments(chapter: str) -> list[Segment]:
    """Ordered prose and figure segments of a Markdown chapter."""
    segments: list[Segment] = []
    heading = ""
    before = ""
    for kind, info, text in _blocks(chapter):
        if kind == "heading":
            heading = speakable_inline(text)
            if heading:
                segments.append(Segment(text=_sentence(heading)))
                before = ""
        elif kind == "p":
            for seg in _paragraph_segments(text, heading, before):
                segments.append(seg)
                if seg.figure is None:
                    before = seg.text
        elif text.strip():
            fig_kind = "table" if kind == "table" else _figure_kind(info)
            segments.append(
                Segment(
                    figure=Figure(
                        kind=fig_kind,
                        source=_cap(text),
                        heading=heading,
                        before=before[-BEFORE_CAP_CHARS:],
                        info=info,
                    )
                )
            )
    return _with_captions(segments)


def _with_captions(segments: list[Segment]) -> list[Segment]:
    """A short paragraph right after a figure is its likely caption: give it to the figure as context."""
    out: list[Segment] = []
    for idx, seg in enumerate(segments):
        fig = seg.figure
        if fig is not None and idx + 1 < len(segments):
            nxt = segments[idx + 1]
            if nxt.figure is None and 0 < len(nxt.text) <= CAPTION_CAP_CHARS:
                fig = replace(fig, after=nxt.text)
        out.append(Segment(text=seg.text, figure=fig))
    return out


_CUES = {  # fallback cue per figure kind, (Russian, English)
    "code": ("Пример кода", "A code example"),
    "mermaid": ("Схема", "A diagram"),
    "svg": ("Схема", "A diagram"),
    "chart": ("График", "A chart"),
}
_TITLE_RE = re.compile(r"""\btitle\s*=\s*(?:"([^"]*)"|'([^']*)')""")


def _is_russian(*texts: str) -> bool:
    letters = re.findall(r"[^\W\d_]", " ".join(texts))
    return bool(letters) and sum("\u0400" <= ch <= "\u04ff" for ch in letters) * 2 > len(letters)


def _cue(figure: Figure) -> str:
    """A short spoken cue for a figure that could not be retold, in the text's language."""
    ru, en = _CUES.get(figure.kind, _CUES["code"])
    title = _TITLE_RE.search(figure.info)
    name = speakable_inline((title.group(1) or title.group(2)) if title else "")
    cue = ru if _is_russian(figure.before, figure.after, figure.heading) else en
    return _sentence(f"{cue}: {name}" if name else cue)


def fallback_text(figure: Figure) -> str:
    """What to say when a figure cannot be narrated: alt, caption, a table's cells, or a short cue."""
    if figure.kind == "image":  # its caption paragraph is spoken as prose already
        return _sentence(figure.source) if figure.source else ""
    if figure.kind == "table":
        rows = [
            ", ".join(
                c.replace("\\|", "|").strip()
                for c in re.split(r"(?<!\\)\|", row.strip().strip("|"))
                if c.strip()
            )
            for row in figure.source.split("\n")
            if not _TABLE_SEP_RE.match(row)
        ]
        return " ".join(_sentence(speakable_inline(r)) for r in rows if r)
    return _cue(figure)


def picture_cue(figures: list[Figure]) -> str:
    """A cue for a document that is only unnamed pictures, in the document's language."""
    if not any(f.kind == "image" for f in figures):
        return ""
    context = [part for f in figures for part in (f.before, f.after, f.heading)]
    return "Картинка." if _is_russian(*context) else "A picture."


def plain_speakable(text: str) -> str:
    """Speech text of a plain-text source: bare URLs removed, nothing else changed."""
    return strip_urls(text)
