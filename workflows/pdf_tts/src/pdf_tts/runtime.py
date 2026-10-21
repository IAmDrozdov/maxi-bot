"""pdf_tts job descriptor: preview, estimate and run over the shared contract."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import cast

from shared.audio import estimate_speech
from shared.config import Settings
from shared.doc import DocumentError, parse_document
from shared.job import (
    AudioDeliverable,
    Cost,
    CostLine,
    DocumentSource,
    Estimate,
    Preview,
    Progress,
    Result,
    Source,
    Workflow,
)
from shared.translate import TRANSLATION_OUTPUT_CHAR_FACTOR, estimate_translation

from .config import PdfTtsConfig
from .graph import build_graph
from .narration import NARRATION_CHARS, estimate_narration
from .speech import Figure, is_markdown_source, plain_speakable, split_segments
from .state import PdfTtsState


def _prose_and_figures(chapters: list[str], filename: str) -> tuple[list[str], list[Figure]]:
    """Speakable prose per chapter (figures left out) and every figure, in order."""
    if not is_markdown_source(filename):
        return [plain_speakable(c) for c in chapters], []
    prose: list[str] = []
    figures: list[Figure] = []
    for chapter in chapters:
        segments = split_segments(chapter)
        prose.append("\n\n".join(s.text for s in segments if s.figure is None and s.text))
        figures.extend(s.figure for s in segments if s.figure is not None)
    return prose, figures


def preview(settings: Settings, config: PdfTtsConfig, source: Source) -> Preview:
    _ = settings
    if not isinstance(source, DocumentSource):
        return Preview.failed("document", "pdf_tts needs a document, not a link")
    try:
        parsed = parse_document(
            source.file_bytes,
            source.filename,
            chapter_char_target=config.chapter_char_target,
            max_document_chars=config.max_document_chars,
        )
    except DocumentError as exc:
        return Preview.failed(source.filename, str(exc))
    _, figures = _prose_and_figures(parsed.chapters, source.filename)
    return Preview(
        title=source.filename,
        char_count=parsed.char_count,
        chapter_count=len(parsed.chapters),
        payload={
            "filename": source.filename,
            "text": parsed.text,
            "chapters": parsed.chapters,
            "figure_count": len(figures),
        },
    )


def estimate(settings: Settings, config: PdfTtsConfig, preview: Preview) -> Estimate:
    _ = settings
    if preview.error:
        return Estimate.failed(preview.error)
    chapters = cast(list[str], preview.payload.get("chapters") or [])
    filename = str(preview.payload.get("filename") or "")
    prose, figures = _prose_and_figures(chapters, filename)
    lines: list[CostLine] = []
    approximate = False
    narrated = min(len(figures), config.narration.max_figures)
    if figures:
        narration_cost, approximate = estimate_narration(config.narration, figures)
        lines.append(narration_cost)
    # Narrations are not written yet: stand in for them with text of a typical length.
    spoken_chapters = [*prose, "x" * (narrated * NARRATION_CHARS)] if narrated else prose
    spoken = "\n\n".join(spoken_chapters)
    if config.translation is not None:
        lines.append(estimate_translation(spoken_chapters, config.translation).cost)
        spoken = "x" * int(len(spoken) * TRANSLATION_OUTPUT_CHAR_FACTOR)
    lines.append(estimate_speech(spoken, config.speech).cost)
    return Estimate(cost=Cost.of(*lines), approximate=approximate)


def run(settings: Settings, config: PdfTtsConfig, preview: Preview, progress: Progress) -> Result:
    if preview.error:
        return Result.failed(preview.error)
    initial: PdfTtsState = {
        "filename": str(preview.payload["filename"]),
        "text": str(preview.payload["text"]),
        "chapters": cast(list[str], preview.payload["chapters"]),
        "cost_lines": [],
        "facts": [],
    }
    state: PdfTtsState = build_graph(settings, config, progress).invoke(initial)
    cost = Cost.of(*state.get("cost_lines", []))
    if state.get("error"):
        return Result.failed(str(state["error"]), cost)
    stem = PurePosixPath(initial["filename"]).stem
    return Result(
        deliverable=AudioDeliverable(
            data=state["audio_bytes"],
            filename=f"{stem}.ogg",
            parts=state.get("audio_parts") or [],
            duration_s=float(state.get("audio_duration_s") or 0.0),
        ),
        cost=cost,
        facts=tuple(state.get("facts", [])),
    )


def speed_profile(config: PdfTtsConfig) -> str:
    translate_model = config.translation.model if config.translation else "off"
    return f"pdf_tts|{config.speech.model}|{translate_model}"


WORKFLOW: Workflow[PdfTtsConfig] = Workflow(
    id="pdf_tts",
    config_type=PdfTtsConfig,
    accepts=frozenset({"document"}),
    preview=preview,
    estimate=estimate,
    run=run,
    speed_profile=speed_profile,
)
