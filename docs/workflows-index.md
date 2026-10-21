# Workflows Index

| Workflow | Purpose | Accepts | Framework |
|---|---|---|---|
| `doc_translator` | PDF/DOCX/Markdown/HTML chapter translation to Markdown | document | LangChain |
| `pdf_tts` | PDF/DOCX/Markdown/HTML to audio with optional translation; code, diagrams and pictures are retold | document | LangChain |
| `yt_dub` | YouTube video to translated voice-over audio (captions-first, STT fallback) | link | LangChain |

Every workflow exports `WORKFLOW` (ADR-012) and is runnable via `uv run smoke <id> <path-or-url>`.
