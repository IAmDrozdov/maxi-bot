# pdf_tts

Extracts text from a PDF, DOCX, HTML or Markdown document, retells its code, diagrams, charts, tables and pictures, optionally translates it, and synthesizes speech via the shared OpenAI TTS stage. Exposed as `pdf_tts.WORKFLOW` (ADR-012).

## Pipeline

```
extract_text (shared.doc.parse_document) → narrate_figures → translate (optional, shared.translate) → synthesize_audio (shared.audio)
```

`graph.py` is the only composition; `runtime.py` holds the descriptor: `preview` parses
the document (free), `estimate` prices it with the stage estimators, `run` invokes the
graph and returns an `AudioDeliverable` + cost lines + facts.

Framework: LangChain — linear chain, no cycles (ADR-003 compliant).

## Speakable text (ADR-023)

`narrate_figures` runs on the source language, before translation, so TTS never gets raw Markdown.
`speech.py` (pure) splits a Markdown chapter (`.md`, `.markdown`, `.html`, `.htm`) into prose and
figures. Prose loses heading marks (the heading stays as a sentence), emphasis, backticks, list and quote
marks, `[text](url)` (text kept), bare URLs, citation markers and stray tags. A figure is a code fence
(always retold, never read verbatim), `mermaid`, `chart` or `svg` fence, an image `![alt](url)` or a pipe
table; it carries its source (cut at 6 000 characters), the nearest heading, the paragraph before it and a
short paragraph after it. `narration.py` retells each figure with one chat call (6 in parallel; a picture is
sent as an `image_url`, with a text-only fallback from alt, caption and context for `.svg`, non-http
URLs or a failed vision call; a picture with neither alt nor caption is dropped). A figure that cannot be
retold speaks its alt, caption or table cells, or is omitted; it never fails the job. At most
`narration.max_figures` (300) are retold. A plain source (`.txt`, PDF, DOCX) only loses bare URLs.

Estimate: `Figure descriptions` line (approximate when pictures are present) and speech priced on the prose
plus ~450 characters per figure. Preview payload: `figure_count`.

## Config knobs (`PdfTtsConfig`)

| Field | Default | Description |
|---|---|---|
| `speech` | `SpeechSpec()` | Shared speech spec: `model`, `voice`, `spoken_language`, `instructions`, `chunk_size`, `output_format` (`opus`), `max_parallel`, `price_per_1k_chars` (follows the model via `shared.pricing`) |
| `translation` | `None` | `TranslateSpec` (`source_language`, `target_language`, `model`, `max_parallel`, prices) — set to translate before TTS; `speech.spoken_language` then defaults to its target language |
| `narration` | `NarrationSpec()` | `model` (`gpt-5.6-luna`), `max_parallel` (6), `max_figures` (300), prices (follow the model via `shared.pricing`) |
| `chapter_char_target` | `8000` | Chapter size target: small Markdown sections are packed up to it, size-based fallback cut |
| `max_document_chars` | `2,000,000` | Backstop against a huge document |

## Opus stitching

TTS runs chunk by chunk, so a document comes back as N standalone audio blobs. For
MP3 those can simply be concatenated, but Ogg-Opus cannot: byte-joining produces a
*chained* stream whose duration and seek bar only reflect chunk 1.

`shared.audio.concat_ogg_opus()` rewrites the container instead — one `OpusHead`/`OpusTags` pair,
one serial number, a continuous page sequence, and granule positions offset so
timestamps keep climbing. Packet payloads are never touched, so nothing is
re-encoded and no ffmpeg is required. Cost: each chunk boundary keeps ~6.5 ms of
pre-skip padding, which is inaudible in speech.

State keys: `audio_bytes`, `audio_parts` (per-chunk blobs, for size-limited
splitting downstream), and `audio_duration_s` — the playable length
in seconds, or `0.0` when unknown (only Opus is measured).

## Result

`Result.cost` lines: `Figure descriptions` (when the document has figures), `Translation` (when enabled)
and `Speech`. `Result.facts`: Chapters, Figures described and Figures skipped (when non-zero; only
for documents with figures), Tokens (when translated), Chars billed, Chunks, Synthesis time. Progress phase
`describing figures`.

## Smoke test

```bash
uv run smoke pdf_tts path/to/document.pdf
uv run smoke pdf_tts path/to/document.pdf --config '{"translation": {"target_language": "German"}}'
```

Previews and prices first, asks before the paid run. Accepted file types: `.pdf`,
`.docx`, `.md`, `.markdown`, `.txt`, `.html`, `.htm`. A GitHub file link is scraped by the bot, not by
`smoke`: save the page as `.html` and pass the file.

DOCX known limits: body paragraphs only (tables, headers/footers, footnotes and
text boxes are skipped); non-English heading style names fall back to size-based
chapter splitting.
