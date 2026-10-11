# maxi-bot

Turn long documents and YouTube videos into translations and voice-overs from a
private Telegram bot that shows the price before it spends anything.

Three workflows, one job contract, two interfaces:

| Workflow | In | Out |
|---|---|---|
| `doc_translator` | PDF / DOCX / Markdown / text, or a pasted article link | chapter-by-chapter translation as Markdown |
| `pdf_tts` | same | narrated audio (Telegram voice message), optionally translated first |
| `yt_dub` | YouTube link | voice-over of the video in your language (captions first, speech-to-text fallback) |

- **Telegram bot** (`interfaces/telegram_bot`): send a file or link, get a card with
  every applicable workflow priced and ETA'd, tap one to run. The bot serves one account, its
  **Owner**, and answers nobody else: there is no way to add users, and anyone else runs their own
  instance (ADR-022). The Owner also gets a Mini App for notes, a diary and usage.
- **Smoke runner** (`interfaces/smoke`): the same contract from the terminal —
  preview, price, confirm, run, write the result.

All paid calls go to OpenAI (chat, TTS, STT). Every run is estimated first and gated
by a per-job cap.

## Prerequisites

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- An OpenAI API key — <https://platform.openai.com/api-keys>
- For the bot: a Telegram account

## Quick start (terminal)

```bash
git clone <this repo> maxi-bot && cd maxi-bot
uv sync --all-packages
cp .env.example .env            # fill OPENAI_API_KEY at minimum
uv run smoke doc_translator README.md
```

The runner prints a free preview and a price, asks before spending, and writes the
result to `dist/smoke/`. Any workflow knob can be overridden with `--config`:

```bash
uv run smoke pdf_tts book.pdf --config '{"translation": {"target_language": "German"}}'
uv run smoke yt_dub https://www.youtube.com/watch?v=...
```

## Run the bot

1. Message [@BotFather](https://t.me/BotFather), send `/newbot`, put the token into
   `TELEGRAM_BOT_TOKEN`.
2. Message [@userinfobot](https://t.me/userinfobot) to get your numeric id, put it into
   `OWNER_TELEGRAM_ID`. That account is the Owner: the bot answers it and nobody else,
   and does not start without it.
3. `uv run telegram-bot`, open your bot in Telegram, press Start.

Commands: `/settings` (languages, models, voice), `/status`,
`/cancel`, `/help`. Details: `interfaces/telegram_bot/README.md`.

The Mini App (notes, diary and usage) needs a public HTTPS URL: set up Tailscale Funnel as in
`infrastructure/README.md`, then put the address in `BOT_MINIAPP_URL`.

## Configuration

Everything an operator changes lives in `.env` (`.env.example` is the commented
reference). Workflow behaviour knobs (chunk sizes, backstops, prompts) stay in code under
`workflows/<name>/src/<name>/config.py` on purpose (ADR-004).

| Variable | Default | Meaning |
|---|---|---|
| `OPENAI_API_KEY` | — | required |
| `TELEGRAM_BOT_TOKEN` | — | required for the bot |
| `OWNER_TELEGRAM_ID` | — | required for the bot: the numeric id of the one account it answers |
| `BOT_LANGUAGES` | English, Russian, Chinese, Japanese, Korean, French, German, Spanish | languages offered in `/settings` |
| `BOT_DEFAULT_SOURCE_LANGUAGE` / `BOT_DEFAULT_TARGET_LANGUAGE` | English / Russian | defaults until changed in `/settings` |
| `BOT_MAX_JOB_COST_USD` | 10 | refuse a single job estimated above this |
| `YTDLP_PROXY` | — | optional proxy for YouTube requests, e.g. the bundled WARP sidecar (ADR-014) |
| `WARP_ACCEPT_TOS` | — | `yes` starts the WARP sidecar on deploy; you accept Cloudflare's terms |
| `BOT_MINIAPP_URL` | — | public HTTPS URL of the Mini App (the 📒 button); empty = no button |
| `TS_AUTHKEY` | — | Tailscale auth key: deploy starts the Funnel sidecar that publishes the Mini App (ADR-016) |
| `TELEGRAM_DB_PATH` | `data/telegram_bot.sqlite3` | sqlite location |
| `LOG_LEVEL` | `INFO` | |
| `OTEL_*` | off | optional tracing, see below |
| `DO_API_KEY` | — | Terraform only, never read by app code |

Models, prices and voices are one catalogue in `shared/src/shared/pricing.py`. Prices
are estimates; check your OpenAI invoice.

## Notes

The bot also works as a save-for-later store (`apps/notes`, ADR-015). Any text or
link you send gets one card: 💾 save to notes, the priced agents (translate, voice, dub), or
Cancel. A saved item is enriched with its title, author and caption and filed into
Sections; its 🤖 button brings the agents back later. Browse, sort and
tidy them in the Mini App (the 📒 button in your chat, ADR-016).
Details: `apps/notes/README.md`.

## Security model

- The bot answers one account, the Owner (`OWNER_TELEGRAM_ID`), in a private chat. Every other
  update is dropped without a reply (ADR-022).
- The Mini App is public over HTTPS (Tailscale Funnel), but every API call needs Telegram-signed
  data from the Owner: HMAC-checked, at most 24 h old, the Owner's id only (ADR-016). Its container
  holds a key derived from the bot token, never the token.
- Link enrichment for notes fetches pages from the bot process through an SSRF guard
  (public addresses only, every redirect re-checked).
- In production the containers run read-only and non-root with memory and pid caps, and the
  droplet accepts no inbound connections: SSH is opened for the operator's address only for the
  length of a deploy (ADR-017, `infrastructure/README.md`).
- A secrets scan (trufflehog) runs on every commit, push and deploy, and a deploy stops on a
  High dependency vulnerability (grype).
- Untrusted documents are parsed inside the bot process; the caps above are the
  blast radius.

## Data handling

- Document text, transcripts and pasted-link content are sent to OpenAI to translate,
  transcribe or synthesize. No document text, transcript or audio is stored by the bot.
- The bot's sqlite database keeps, indefinitely: your Telegram id and username,
  filenames, per-job cost and error text, and your `/settings` choices. It's visible in
  the Mini App's «Расходы» screen.
- Notes keeps every captured text and link, its fetched title, author,
  caption and thumbnail URL in `notes.sqlite3`, until you delete it in the Mini App.

## Legal note

`yt_dub` reads captions when a video has them. When it has none, it downloads the
audio track for transcription, which is against YouTube's Terms of Service. Running
that fallback is your decision and your risk (ADR-010).

## Deploy (optional)

`infrastructure/` provisions one DigitalOcean droplet with Terraform and deploys the bot
and the Mini App with Docker Compose (`./infrastructure/deploy.sh`). See
`infrastructure/README.md`. AI-agent users can run the `/deploy-bot` skill.

## Observability (optional)

OpenTelemetry export is wired and off by default (`OTEL_ENABLED`), but the code does
not create spans of its own yet; `tools/observability/` holds a local ELK + APM stack as
scaffolding. See `docs/observability/tracing-elk.md`.

## Layout

- `shared/` — settings, logging, the job contract, pricing, document intake, translate/speech/STT stages
- `workflows/<name>/` — workflow business logic (graph/nodes) + its job descriptor (`runtime.py`)
- `interfaces/telegram_bot/` — the Telegram bot + the Mini App
- `interfaces/smoke/` — terminal runner over the same job contract
- `infrastructure/` — Terraform + Docker deployment to a DigitalOcean droplet
- `docs/` — architecture, conventions, ADRs
- `.skills/` — scaffolding instructions for AI agents
- `tools/check_layers.py` — layer enforcement
- `tools/observability/` — local ELK + APM stack

New interfaces plug in as thin adapters under `interfaces/` holding a registry of
workflow descriptors (ADR-005, ADR-012).

## Docs

- [Architecture](docs/architecture.md), [Conventions](docs/conventions.md), [Tooling](docs/tooling.md)
- [Decisions (ADRs)](docs/decisions/) — binding
- [Workflows index](docs/workflows-index.md), [Glossary](CONTEXT.md)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). AI agents working in this repo start at `AGENTS.md`.

## License

MIT — see [LICENSE](LICENSE).
