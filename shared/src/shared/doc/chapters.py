"""Chapter splitting for long documents."""

from __future__ import annotations

import re
import sys
from bisect import bisect_right

_HEADING_PATTERN = re.compile(
    r"^(?:#{1,6}\s+\S|(?:chapter|глава)\b.*|\d+\.\s+\S)",
    re.MULTILINE | re.IGNORECASE,
)
_ATX_PATTERN = re.compile(r"^#{1,6}\s+\S", re.MULTILINE)
_FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_TABLE_LINE = re.compile(r"^\s*\|")

# A numbered list (e.g. every line starting "1. ", "2. ") matches the heading pattern
# once per line, so an adversarial or just very long list could turn one document into
# hundreds of thousands of "chapters" — one completion request each. Above this count,
# fall back to size-based chunking instead.
MAX_CHAPTERS = 2000


def _fenced_ranges(text: str) -> list[tuple[int, int]]:
    """Character spans of fenced code blocks, fences included; an unclosed fence runs to the end."""
    ranges: list[tuple[int, int]] = []
    open_at: int | None = None
    fence = ""
    pos = 0
    for line in text.split("\n"):
        end = pos + len(line)
        if open_at is None:
            m = _FENCE_OPEN.match(line)
            if m:
                open_at, fence = pos, m.group(1)
        else:
            stripped = line.strip()
            if stripped.startswith(fence[0] * len(fence)) and set(stripped) == {fence[0]}:
                ranges.append((open_at, end))
                open_at = None
        pos = end + 1
    if open_at is not None:
        ranges.append((open_at, len(text)))
    return ranges


def _inside(ranges: list[tuple[int, int]], index: int) -> bool:
    """Whether `index` is in one of the sorted, non-overlapping `ranges` (binary search)."""
    at = bisect_right(ranges, (index, sys.maxsize)) - 1
    return at >= 0 and ranges[at][0] <= index < ranges[at][1]


def _blocks(text: str) -> list[str]:
    """Paragraph-level blocks; a fenced block or a table is one block even with blank lines in it."""
    blocks: list[str] = []
    current: list[str] = []
    fence: str | None = None
    for line in text.split("\n"):
        if fence is None:
            m = _FENCE_OPEN.match(line)
            if m:
                fence = m.group(1)
            elif not line.strip():
                if current:
                    blocks.append("\n".join(current))
                    current = []
                continue
        else:
            stripped = line.strip()
            if stripped.startswith(fence[0] * len(fence)) and set(stripped) == {fence[0]}:
                fence = None
        current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def _has_structure(text: str) -> bool:
    return "```" in text or "~~~" in text or bool(re.search(r"^\s*\|", text, re.MULTILINE))


def _split_by_size(text: str, target_chars: int) -> list[str]:
    chunks: list[str] = []
    remaining = text.strip()
    while remaining:
        if len(remaining) <= target_chars:
            chunks.append(remaining)
            break
        split_at = remaining.rfind(" ", 0, target_chars)
        if split_at == -1:
            split_at = target_chars
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip()
    return chunks


def _pack(parts: list[str], target_chars: int) -> list[str]:
    """Greedy join of consecutive parts up to the target; an oversized part stays whole."""
    chunks: list[str] = []
    current = ""
    for part in parts:
        if current and len(current) + 2 + len(part) > target_chars:
            chunks.append(current)
            current = part
        else:
            current = f"{current}\n\n{part}" if current else part
    if current:
        chunks.append(current)
    return chunks


def _split_blocks_by_size(text: str, target_chars: int) -> list[str]:
    """Size-based split that never cuts a fenced block or a table (an oversized one stays whole)."""
    parts: list[str] = []
    for block in _blocks(text.strip()):
        if len(block) <= target_chars or _FENCE_OPEN.match(block) or _TABLE_LINE.match(block):
            parts.append(block)
        else:
            parts.extend(_split_by_size(block, target_chars))
    return _pack(parts, target_chars)


def split_into_chapters(
    text: str, target_chars: int = 8000, *, markdown: bool = False
) -> list[str]:
    """Chapters at headings. Markdown (`#` headings) packs small sections up to the target size."""
    stripped = text.strip()
    if not stripped:
        return []

    fenced = _fenced_ranges(stripped)
    atx = [m for m in _ATX_PATTERN.finditer(stripped) if not _inside(fenced, m.start())]
    pack = markdown or len(atx) >= 2
    if pack:
        matches = atx
    else:
        matches = [m for m in _HEADING_PATTERN.finditer(stripped) if not _inside(fenced, m.start())]
    if 2 <= len(matches) <= MAX_CHAPTERS:
        starts = [m.start() for m in matches]
        # Text before the first heading belongs to the first chapter, not nowhere.
        starts[0] = 0
        sections = [
            stripped[start : (starts[i + 1] if i + 1 < len(starts) else len(stripped))].strip()
            for i, start in enumerate(starts)
        ]
        chapters = [s for s in sections if s]
        if chapters:
            return _pack(chapters, target_chars) if pack else chapters

    if _has_structure(stripped):
        size_chunks = _split_blocks_by_size(stripped, target_chars)
    else:
        size_chunks = _split_by_size(stripped, target_chars)
    return size_chunks if size_chunks else [stripped]
