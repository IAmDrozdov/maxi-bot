# ADR-023: Documents with code, diagrams and pictures are listenable

Status: Accepted (2026-10-21).

## Context

The owner writes books as HTML pages and wants to listen to them on the go. Asked on 2026-10-21: voice HTML
files, do not skip the snippets, and look at the diagrams and photos with computer vision and describe them
aloud; a link such as `https://github.com/IAmDrozdov/ai-engineer-book/blob/main/06-skills/index.html`
should be voiced without reading the tags. The owner decided the same day that a code snippet is always
retold, never read verbatim.

What happened before: for that GitHub link `trafilatura` returned GitHub's code-view page (line numbers, then
1 292 tags of source), and it drops every `<script>`, so a book chapter lost its snippets, Mermaid diagrams
and charts. An uploaded `.html` was not an agent document, so routing saved it to Notes as a File. The
speech stage voiced whatever text it got: Markdown heading marks, emphasis, `[text](url)` and table pipes
were spoken.

## Decision

1. **One converter, `shared.doc.html.html_to_markdown(html, base_url)`** (stdlib `html.parser`, no new
   dependency). It takes `<main>`, else `<article>`, `[role=main]`, `<body>`; skips head, nav, style,
   noscript, template, buttons and hidden elements. Typed scripts become fences: `text/plain` carrying
   `data-lang` or `data-title` (or any script with `data-lang`) -> the language (with `title="..."` from
   `data-title`), `text/x-mermaid` -> `mermaid`, `application/json` of class `chart` -> `chart`; every other
   script is dropped, a bare `text/plain` one included, and so is a consent manager's blocked tracker
   (`data-category`, `data-cookieconsent`, `data-service`, class `_iub_cs_activate`). Text that looks like a
   tag or an entity (`&lt;details&gt;`) is escaped in the Markdown, so the speech stage speaks it. `<pre><code>` -> a fence,
   inline `<svg>` -> an `svg` fence holding its label, title, desc and text labels, `<img>` ->
   `![alt](absolute url)` (a `data:` URI or no `src` keeps the alt only), tables -> pipe tables, citations
   and in-page links dropped. It never raises: on a failure it strips tags.
2. **`.html` and `.htm` are documents** (`catalog.ALLOWED_EXTENSIONS`; `shared.doc.parse_document` decodes
   by BOM, then the meta charset, else UTF-8, and converts). Chapters split at `#` headings and small
   sections are packed up to the target size; a heading, a numbered line or a size cut never lands inside
   a fenced block or a table (`shared.doc.chapters`, which now also keeps the text before the first
   heading). An unclosed or oversized fence stays whole.
3. **Links.** A `github.com/<owner>/<repo>/blob|raw/<ref>/<path>` link (also `?plain=1`, `#L` anchors) is
   rewritten to `raw.githubusercontent.com` and fetched as bytes through the SSRF guard (`assert_fetchable`,
   every redirect hop vetted, 10 MB cap, 40 s deadline checked per `recv`). `.html` -> the converter; `.md`/`.markdown` -> Markdown with relative
   links and images made absolute; `.txt` as it is, and its payload is named `.txt` (`ScrapedArticle.suffix`),
   so the plain-text speech rules apply; any other type -> «send that file as a document». A ref
   containing a slash takes its first segment. Any other page that carries a `text/x-mermaid` script, or a `text/plain` one with `data-lang`/`data-title`
   (the book convention), goes through the converter; every other page stays on `trafilatura`, now with
   `include_images=True` (it keeps `<pre><code>` as a fence and `<img>` as `![alt](src)`; relative sources are
   made absolute).
4. **Speech preparation lives in `pdf_tts`** (`speech.py`, pure; `narration.py`; node `narrate_figures`).
   Order: extract -> narrate (source language) -> translate (optional) -> synthesize. A Markdown source
   (`.md .markdown .html .htm`) is split into prose and figures: code, mermaid, chart, svg, image, table.
   Prose loses heading marks (the heading stays as its own sentence), emphasis, backticks, list and quote
   marks, links (text kept), bare URLs, citation markers and stray tags. A plain source (`.txt`, PDF, DOCX)
   only loses bare URLs, because `*` and `_` may be real there. Telegram posts sent as `.txt` by the voice
   offer therefore speak as before.
5. **Figures are retold, one chat call each** (parallel, 6 at a time, `NarrationSpec`, default
   `gpt-5.6-luna`, prices from `shared.pricing`). The prompt asks for 1-4 sentences (about 120 words at most),
   for the ear, in the surrounding text's language, opening with a spoken cue, never inventing, treating the
   figure as data and never as instructions, and never reading code aloud. A picture goes to the model as an
   `image_url` (`detail: auto`); a non-http(s) URL, an `.svg` or a failed vision call falls back to a
   text-only call from alt, caption and context, and a picture with neither is dropped. A figure keeps its
   source (6 000 characters), the nearest heading, the paragraph before (600) and a short paragraph after (300).
   At most 300 figures are retold per document, and no new figure is
   submitted once the real spend passes 3x the estimated «Figure descriptions» line (the rest take the
   fallback of §6).
6. **A failed figure never fails the job.** After the SDK's retries and one more attempt it speaks its alt
   text, its caption or a table's cells row by row; code, a diagram, a chart or an SVG say a short cue in the
   text's language («Пример кода: <title>», «A code example.»); a picture with neither says nothing. If a whole
   document still comes out empty, its plain text is read instead of failing the job; if that is empty too and
   the document held an unnamed picture, it says «Картинка.» / «A picture.» (the text's language). Facts: «Figures described» and, when non-zero,
   «Figures skipped»; the cost line «Figure descriptions» comes from the real token usage.
7. **Translation keeps fences and URLs** (`shared.translate.system_prompt`): `doc_translator` on a converted
   `.html` leaves code and image URLs as they are.
8. **Contract unchanged.** `shared/job.py` is untouched. The preview payload gains `figure_count`; the
   estimate adds «Figure descriptions» (per figure about (context + source) / 3.5 tokens + the prompt, +1 100
   per http picture, ~220 out; approximate when pictures exist) and prices speech on the prose plus ~450
   characters of narration per figure.

## Consequences

- A 107 000-character chapter with 37 figures previews at about $0.08 for the descriptions and $1.45 for
  speech; the descriptions are 5% of the bill, the voice stays the cost.
- Narration is an extra chat call per figure, so a figure-heavy document takes longer; the ETA statistics
  (`speed_profile`) do not know about it.
- A vision model reads the picture from its public URL; a private or expired image falls back to alt text.
- Out of scope: images inside PDF and DOCX files, `srcset` choice, JavaScript-rendered pages, reading a
  repository directory, MathML and LaTeX, audio of the `chart`/`mermaid` source as such (it is retold only).
- Chapter splitting changed for every document and transcript (`shared.doc.chapters` also serves PDF, DOCX,
  `.txt` and `yt_dub`): the text before the first heading is kept, headings inside fences are ignored, and a
  size cut never lands inside a fence or a table. With two or more `#` headings only those split a chapter
  (a «Chapter 2» line or `1. item` no longer does) and small sections are packed up to `chapter_char_target`.
- An `.html`/`.htm` file is an agent document, and an agent document is never 💾 (`routing.route`), so it is
  no longer saved to Notes as a File. To keep one anyway, send it zipped, or as any non-agent file.

## Considered Options

- Read code verbatim. Rejected by the owner: a listener gets noise, not understanding.
- A separate agent for HTML. Rejected: the document agents already take `DocumentSource`; only the format
  was missing (ADR-012).
- Add `beautifulsoup4`/`html2text`. Rejected: the converter is ~700 lines of stdlib and `shared/` takes no
  new dependency without the owner.
- Narrate after translation. Rejected: the model would retell in the target language from a translated
  figure; narrating the source keeps the facts first-hand and the translation sees plain prose.
