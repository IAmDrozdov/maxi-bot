# doc_translator

Translates long PDF, DOCX, HTML or Markdown documents chapter-by-chapter via the shared translation stage, then merges the result into one Markdown file. Exposed as `doc_translator.WORKFLOW` (ADR-012).

## Pipeline

```
extract_text (shared.doc.parse_document) → translate (shared.translate)
```

Framework: LangChain — linear chain, no cycles (ADR-003 compliant).

## Config knobs (`DocTranslatorConfig`)

| Field | Default | Description |
|---|---|---|
| `translation` | `TranslateSpec()` | `source_language`, `target_language`, `model` (`gpt-5.6-luna`), `max_parallel`, prices (follow the model via `shared.pricing`) |
| `chapter_char_target` | `8000` | Chapter size target: small Markdown sections are packed up to it, size-based fallback cut |
| `max_document_chars` | `2,000,000` | Backstop against a huge document |

HTML (`.html`, `.htm`) reaches the translator as Markdown (`shared.doc.html`, ADR-023): code, Mermaid, charts
and SVG labels become fences and pictures become `![alt](url)`. The translation prompt leaves fenced
blocks and URLs untranslated, so the result keeps them as they were.

DOCX known limits: body paragraphs only (tables, headers/footers, footnotes and
text boxes are skipped); non-English heading style names fall back to size-based
chapter splitting.

## Result

`FileDeliverable` (`<stem>.translated.md`), one `Translation` cost line, facts:
Chapters, Tokens in/out.

## Smoke test

```bash
uv run smoke doc_translator path/to/document.md
```
