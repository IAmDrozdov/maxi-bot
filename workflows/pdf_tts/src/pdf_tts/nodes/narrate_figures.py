"""Turn chapters into speakable text: prose cleaned, figures retold (ADR-023)."""

from __future__ import annotations

import re

from langchain_core.runnables import Runnable, RunnableLambda

from shared.config import Settings
from shared.job import NO_PROGRESS, Fact, Progress

from ..config import PdfTtsConfig
from ..narration import narrate_figures, spoken_figure
from ..speech import (
    Figure,
    Segment,
    is_markdown_source,
    picture_cue,
    plain_speakable,
    split_segments,
)
from ..state import PdfTtsState


def build_narrate_figures_node(
    settings: Settings, config: PdfTtsConfig, progress: Progress = NO_PROGRESS
) -> Runnable:
    def _run(state: PdfTtsState) -> dict:
        if state.get("error"):
            return dict(state)
        chapters = state.get("chapters") or []

        if not is_markdown_source(str(state.get("filename", ""))):
            # PDF, DOCX and .txt are plain text: only bare URLs go.
            plain = [plain_speakable(c) for c in chapters]
            return {**state, "chapters": plain, "text": "\n\n".join(plain)}

        per_chapter: list[list[Segment]] = [split_segments(c) for c in chapters]
        figures: list[Figure] = [
            seg.figure for segs in per_chapter for seg in segs if seg.figure is not None
        ]
        narrated: dict[int, str] = {}
        facts = list(state.get("facts", []))
        cost_lines = list(state.get("cost_lines", []))
        if figures:
            progress.phase("describing figures", min(len(figures), config.narration.max_figures))
            result = narrate_figures(settings, config.narration, figures, progress.tick)
            narrated = result.texts
            facts.append(Fact("Figures described", str(result.described)))
            if result.skipped:
                facts.append(Fact("Figures skipped", str(result.skipped)))
            if result.cost is not None:
                cost_lines.append(result.cost)

        spoken: list[str] = []
        index = 0
        for segs in per_chapter:
            parts: list[str] = []
            for seg in segs:
                if seg.figure is None:
                    parts.append(seg.text)
                else:
                    parts.append(spoken_figure(seg.figure, narrated.get(index)))
                    index += 1
            chapter_text = "\n\n".join(p for p in parts if p.strip())
            if chapter_text:
                spoken.append(chapter_text)
        if not spoken:
            # Nothing speakable came out (no prose, no fallback): read the plain text, not fail.
            spoken = [t for t in (plain_speakable(c) for c in chapters) if re.search(r"\w", t)]
        if not spoken and (cue := picture_cue(figures)):
            spoken = [cue]  # only unnamed pictures left: say so rather than fail
        return {
            **state,
            "chapters": spoken,
            "text": "\n\n".join(spoken),
            "cost_lines": cost_lines,
            "facts": facts,
        }

    return RunnableLambda(_run, name="narrate_figures")
