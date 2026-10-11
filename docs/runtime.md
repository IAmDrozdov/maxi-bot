# Runtime: what runs, what each part does, how they talk

`docs/architecture.md` is the import layering. This file covers the running system: processes,
data and message flow, and the state that lives only in memory. Read it before touching the bot,
the notes path or the deploy. Shorthand: `telegram_bot/x.py` and `handlers/x.py` mean
`interfaces/telegram_bot/src/telegram_bot/…`; `deploy.sh` means `infrastructure/deploy.sh`.

## Processes (one image, `infrastructure/docker/docker-compose.yml`)

| Service | Entry point | Does | Reads / writes |
|---|---|---|---|
| `bot` | `telegram-bot` (`telegram_bot/__main__.py`) | Telegram long polling. Answers the Owner, prices cards, runs one job at a time (`worker.worker_loop`), and runs notes enrichment plus the notes sweeper, show loop, reminder loop and, once per start, the Thumbnail backfill, and the diary Nudge loop | `telegram_bot.sqlite3` rw, `notes.sqlite3` rw, `diary.sqlite3` rw (the `nudges` table only), OpenAI, YouTube, the web |
| `miniapp` | `telegram-miniapp` (`telegram_bot/miniapp`) | The Mini App: a public static shell plus a signed JSON API for notes, the diary and usage, `127.0.0.1:8083` (ADR-016, ADR-020, ADR-021) | `telegram_bot.sqlite3` read, `notes.sqlite3` rw, `diary.sqlite3` rw (the only process that writes Entries and Marks) |
| `funnel` (profile) | `tailscale/tailscale` | Publishes `miniapp` over HTTPS through Tailscale Funnel, outbound only (ADR-016) | `tsstate` volume |
| `warp` (profile) | wireproxy | SOCKS5 egress for yt-dlp only (ADR-014) | — |
| — | `smoke`, `notes-smoke` | Terminal-only reference runs (ADR-001) | local files |

The services share only the `appdata` volume (`/data/*.sqlite3`, WAL mode, a connection per
call). The bot reaches `warp` through `YTDLP_PROXY=socks5h://warp:40000`, and `funnel` reaches
`miniapp` at `http://miniapp:8083`. The droplet accepts no inbound connections: Telegram is polled
outbound, the Mini App is published by the funnel sidecar's outbound tunnel, and SSH is opened
for the operator's address only while `infrastructure/ssh-gate.sh` runs a command (ADR-017).

## The three agents (workflows, ADR-012)

| id | Accepts | Delivers | Paid calls |
|---|---|---|---|
| `doc_translator` | Document | Translated Markdown, one chapter at a time | chat |
| `pdf_tts` | Document | Voice message(s), optionally translated first | chat + TTS |
| `yt_dub` | Link (YouTube only) | Dubbed voice-over; captions first, STT fallback | chat + TTS (+ STT) |

A non-YouTube link is scraped into a Markdown `DocumentSource` (`telegram_bot/scrape.py`) and
then offered to the document agents. Plain text reaches them only as a `.txt` file. Every agent is
`preview` (free) → `estimate` (free) → `run` (paid).

## Who gets what (the Path)

Updates pass through `AccessMiddleware` first: only the Owner's private chat gets through. Every
other update is dropped with no reply, no database read and no log line above debug (ADR-022).
Commands and callbacks then match their own routers. A plain message
becomes one **Path**, decided by `routing.route` (a pure function of `routing.Incoming`, built by
`routing.incoming_of`); each handler takes the Paths it names through `routing.takes(...)`, so
router order is not part of the rule. One row per `Kind`:

| Kind | Message | Taken by |
|---|---|---|
| `document_card` | an agent document (.pdf .docx .md .markdown .txt) → card, never 💾 | `documents.document_handler` |
| `link_card` | text with exactly one website URL → a *savable* card (with 💾); a command that carries a URL → a card without 💾 | `documents.link_handler` |
| `capture_text` | text with no URL, several, or exactly one Instagram / YouTube / TikTok URL → a Note or a Link, no card | `notes.direct_text_handler` |
| `capture_file` | a photo, video or non-agent document → a File | `notes.file_handler` |
| `capture_voice` | a voice or round video → a Voice, transcribed in Enrichment | `notes.voice_handler` |
| `ignore` | everything else: a command, a sticker, audio… | nothing |

Other routers: `start` (`/start` and `/help`, one help text), `settings_menu` and `status`
(`/settings`, `/status`, `/cancel`).
`notes` also takes the callbacks `JobCB save`, `ResultCB` (💾 under a delivered result, below) and
`NotesCB offer` / `restore` / `done` (a Reminder's ✅ Готово marks the Item done and edits the
message to «✅ сделано»). Router order still matters
for commands that carry text (`/settings https://x`, `/help https://x`) and for
`MenuCB(page='job')`, which `documents` claims.

Callback-data prefixes (`keyboards.py`, `notes_ui.py`, max 64 bytes): `m` menu, `o` option
picker, `s` set value, `t` try voice/model, `j` job actions (`run`/`settings`/`cancel`/`save`),
`n` notes item actions (`offer`/`restore`/`done`), `r` a delivered result (the job log's row id).
Pick a new letter for a new family.

## The card lifecycle (`telegram_bot/cards.py`, handlers in `handlers/documents.py`)

`CardBook` (`dp["cards"]`) owns every chat's card state; handlers call it and never touch its
dicts. It takes an `Intake` port (`scrape`, `preview`): `LiveIntake` in production, a stub in the
offline harness.

1. **Open.** An answer is sent at once: a status line, with `[💾 В заметки] [✖️ Cancel]` for a
   savable card. That message becomes the card, *live* for its chat.
2. **Intake.** Scrape, parse, preview and estimate run on a 2-thread `_INTAKE_EXECUTOR`, one per
   user, with a 120 s timeout.
3. **Card.** The status line is edited into the card, *priced*: 💾 (savable), one priced button
   per agent, then ⚙️ Settings and ✖️ Cancel. The job sits in the book's pending map, **one per
   chat**.
4. **Outcome:**
   - **Run** (*job*) puts a `Job` on the FIFO `JobQueue` (max 5 queued or running, one running at a time).
     The worker edits the card with progress and delivers a document or voice messages. The
     result's first message carries 💾 В заметки (`ResultCB`); a result that cannot be delivered
     carries none.
   - **Cancel** *closes* the card.
   - **💾** takes the card's savable (a `notes_capture.Draft`), saves one Item, deletes the card
     and reacts on the Owner's original message; a failed save gives the savable back.
   - **Settings → Back** re-prices the same card.

Guards: only a card that owns the chat's pending job can be run, re-priced or sent to
Settings. A card closed while it was still pricing is never overwritten. A card *superseded* by a
newer message is edited to "⏭ Replaced…" if it was still pricing, and keeps 💾.

Spend gate (`_spend_block_reason`): the per-job cap (`BOT_MAX_JOB_COST_USD`), checked at Run on the
re-priced estimate. There is no daily cap (ADR-022).

## Notes path (`apps/notes`, ADR-015)

The three Capture Paths (`direct_text_handler`, `file_handler`, `voice_handler`) call
`notes_capture.capture_message`; 💾 on a card (`save_handler`) takes the card's `Draft` from the
card book. Both reach `notes.capture.capture` (a `Text` or an `Attachment` with an `Origin`): one
Item per Capture, `capture_link` with dedupe for exactly one URL, else a Note with the whole text,
a File or a Voice. `notes_capture.settle` then shows what the Capture decided (the Due line, the
reaction) and `NotesRuntime.enrich_later` (`telegram_bot/notes_runtime.py`) starts
`notes.enrich.pipeline.enrich_item`. The bot sends no message of its own (a Reminder and the diary Nudge are the exceptions): the Acknowledgement
is its one reaction on the Owner's message (`notes_ui.react`): ✍ working, 👌 in notes, 👎 take a
look (notes ADR-0009). A duplicate gets 👌 at once with no Enrichment (👎 if its Enrichment had
failed), and keeps its Status, `done` included, unless its own words ask for a reminder (below). A
Capture that throws, or notes being down, gets 👎. The bot writes a message of its own only for Reminders
(notes ADR-0011, below) and for the diary Nudge (ADR-020 amended, «Дневник» below). Enrichment:

1. The provider fetch runs through the SSRF guard: YouTube and TikTok oEmbed, the Instagram
   captioned embed page, or a generic page. A Voice is downloaded from Telegram instead (the bot
   passes `telegram_download` in, max 20 MB) and transcribed by `shared.audio.transcribe`; the
   Transcript is stored at once, so a Classifier retry does not pay for STT again.
2. The Classifier (`NOTES_CLASSIFIER_PROVIDER`: `openai`, or `fake` offline) chooses the
   Sections, the Russian Gist and the title, and reads a Due when the text asks to be reminded.
   It is told the Capture moment in the Owner's zone; the fake reads «напомни YYYY-MM-DD HH:MM».
3. `store_enrichment` saves the result and `reminders.fill_from_filing` decides the Due (it fills an
   empty Due only, and one not after the Capture moment is dropped). It returns a `Notice`, and
   `notify(notice)` (`NotesRuntime.notify_for`) sets the reaction from `enrichment_status` on
   `tg_message_id`; when the Notice carries a Due (Enrichment filled one that is still ahead) the bot
   also replies to the Capture with «⏰ пт, 10 окт, 19:00» and a web_app `📅 Перенести`.

Failures go to `schedule_retry` (backoff 1 min → 12 h, then `failed`). The sweeper
(`NOTES_ENRICH_SWEEP_SECONDS`) retries anything due and anything a restart interrupted; a retry
leaves ✍ in place. `apps/*` never imports aiogram: the bot passes `notify` and `download` in as
callbacks. If notes fails to start, the bot runs without it: direct messages get 👎, a link still
gets its card, and 💾 answers "Notes are unavailable". The `NotesCB` `offer` and `restore`
handlers stay only for buttons on old Acknowledgement messages: 🤖 still works, ↩️ only removes
itself (notes ADR-0010); `done` is current (Reminders, below).

**A result put aside (notes ADR-0013).** 💾 under a delivered result (`result_save_handler`) makes that
message a BotResult: `notes_capture.bot_result_of` reads the file facts off the message itself and the
title and Workflow id from the job log (`db.job_title`; with no row, the file name or the caption's first
line), and `notes.capture.capture` takes it as a `BotResult` payload. The Item is filed in «От бота»
(`from_bot`, built in, never deleted, never the Classifier's choice) and has no Enrichment. The button
goes away and 👌 lands on the bot's own message; a second press answers «Уже в заметках», and a
partial unique index on the chat and message keeps it to one Item. A failed save keeps the button. A
split audio result gets the button under part 1 once every part is sent, so its BotResult is part 1. The Owner may file a
BotResult under other Sections too; `sections.refile` never takes it out of «От бота». «Открыть в
чате» and a Reminder reply to the result message; with it gone, the show pass sends the file again
by `file_id` (audio as a voice message, anything else as a document), or says the original is gone.

**Reminders (notes ADR-0011).** An Item with a Due (`due_at`, UTC) is a Reminder.
`NotesRuntime.start_reminder_loop` runs `remind_once` every 30 s: `reminders.claim_due` marks
`reminded_at` and returns the todo Items whose Due has come in one `UPDATE … RETURNING`, so each goes
out once, and the bot replies to the Item's Capture message with `✅ Готово` (`NotesCB done`) and
`📅 Перенести` (a web_app to `/?item=<id>`); if the original is gone it sends a message naming the Item
instead, and a network error hands the claim back for the next pass. Moving or removing a Due clears
`reminded_at`; back to `todo` after the Due sets it, so nothing fires late. A re-sent Link whose words
ask for a reminder goes through `reminders.request_due` (`Classifier.due`), and the saved Item gets the
Due and reopens; it never fails a Capture that is already saved. The Owner's Zone is the Mini App's
last `tz` (`clock.owner_zone`, UTC until the app first opens; every Due is stored in UTC). Overdue
(todo, Due passed; `reminders.is_overdue`) is read off the clock and never stored.

Language: the bot UI is English; notes texts and the Mini App are Russian.

## Mini App (`telegram_bot/miniapp`, ADR-016)

While `BOT_MINIAPP_URL` is set, the bot gives the Owner's chat a `📒` menu button
(`__main__.set_owner_menu_button`, at startup, for that chat only). It opens
the `miniapp` service through the funnel sidecar: a static shell (`miniapp/static`, vanilla JS)
that calls `/api/usage`, `/api/notes/*` and `/api/diary/*`. One 48 px title row (`nav.js`) is all the
permanent navigation (ADR-021): a button naming the Place and its Mode («Заметки · записи ▾») and, to its
right, the working view's own actions. The button opens the place menu: «Заметки» and «Дневник», each with a
`[Записи | Дашборд]` segment, then «⚙️ Расходы». A Place's name opens it in the Mode it was left in. Each
Place and Mode is mounted once per launch and kept until the app closes. A nested screen (the Item view, the
Section form, the diary's Week, Day, Month and Year, Расходы) has no title row: Telegram's back button
returns, and a client below Bot API 6.1 gets «← Назад» from the shell. The app remembers the last Place, each
Place's Mode, the selected Section and whether the Section rail is shown (`nav_v1`, kept next to the
snapshots; a 401 or 403 wipes both). A launch opens what is remembered, «Заметки · записи» the first time;
`📅 Перенести` (`?item=<id>`) opens that Item over the notes list whatever is remembered, and the diary
Nudge's `✍️ Записать` (`?place=diary`) opens «Дневник · записи» the same way; neither writes the memory.

Funnel costs 250–450 ms a request, so the app is built to need few requests (ADR-019):
- **Shell and assets.** The shell `/` is revalidated on every launch (ETag = a hash of the shell). Assets live
  under `/static/<build>/` and are cached for good. All modules load in one round trip
  (`modulepreload`).
- **Compression.** Responses over 500 B are gzipped.
- **Snapshot.** The last answers are kept on the phone (`DeviceStorage`, else `localStorage`: Telegram Web answers DeviceStorage `UNSUPPORTED`;
  `core.js` snapshots). The Dashboards, the Sections, the selected Section's list, the current diary Week
  (today's editor reads it) and the top diary suggestions paint from them at once, then refresh behind them.

«Заметки» (Modes «записи» and «дашборд»):

- **Записи** (`notes.js`) reads Items through `/api/notes/items`, one `notes.domain.lists` List per request:
  `InSection` (`section`, `status`), `Overdue` (`overdue=true`), `Search` (`q`) and `Recheck` (`ids`).
  On the left is the Section rail (64 px): «⏰» while something is Overdue (every Section, todo only, earliest
  Due first), a button per Section in the Owner's order (its emoji, or the first two letters of its name, over
  the first ten characters of the name; dimmed when it has nothing to do), then «✓» and «✏️», captioned the
  same way. The rest of the width is the selected Section's List, newest first, paged by «Ещё»; an Item under
  several Sections shows in each. The Section's name and the count for the Status shown (`todo_count` /
  `done_count`) stand in the title row after the Place button («Заметки · записи ▾ 💼 Работа 7»): the name is
  cut with «…» when the row is short (at 320 px only the count is left), and «✓» comes before the count on the
  «Готово» list.
  «✓» on the rail shows «Готово» instead of «Сделать» (one Status
  per view, notes ADR-0010) until it is tapped again, «⏰» is picked, the Dashboard opens a Section, or the app
  closes; «⏰» is always «Сделать». «◧» in the
  title row hides the rail, and the Section's name in that row then opens the list of Sections with their counts. «🔍»
  (or the «/» key) puts the Search field in place of the title row: Search (`/api/notes/items?q=`, 300 ms
  after typing stops, at most 100 results) covers both Statuses and every text field but the URL, folded by
  the `fold()` SQL function the notes `Database` registers on each connection (casefold, «ё» as «е»). «✏️» is
  edit mode, Section headers only: drag ⋮⋮ to reorder (`PUT /api/notes/sections/order`), ✏️ for the Section
  form, «+ Новая секция» at the end; «Готово» in the title row or Esc leaves it.
- **Дашборд** is infographics only (`GET /api/notes/dashboard?tz=<IANA zone>`): the todo
  count with «просрочено: N» beside it when anything is Overdue, two 26-week heatmaps of Items
  Captured and done per day (days in the Owner's time zone, from `created_at` and `done_at`), and a
  bar per Section with todo Items (`todo_count` from `/api/notes/sections`). A bar is tappable: it
  opens «записи» on that Section in «Сделать»; so is «просрочено: N», which opens «⏰». The request's `tz`
  is also stored as the Owner's zone, so the app sends it on every launch, whatever it opens.

«Дневник» (Modes «записи» and «дашборд», `apps/diary`, vocabulary `apps/diary/CONTEXT.md`). Days are the
Owner's local dates: the page sends `tz` and the router (`miniapp/api_diary.py`) turns it into «today» before
calling the domain.

- **Записи** (`diary.js`) is today's Day editor: the Mark 💀 😐 🔥 (tapping the set one clears it), a field
  that adds an Entry with prefix suggestions from `/api/diary/suggestions?prefix=` (the top five before
  typing), and the Day's Entries, each with «+» (raise) and «✏️» (edit; delete is inside). With a mouse or a
  desktop client the field takes the focus. «Неделя ›» in the title row, like the line «Эта неделя: N дней с
  записями ›» under the editor, opens the Week as a nested screen (`GET /api/diary/week?day=&tz=`; today's
  Week is read with the top five suggestions in one round): ‹ › between Weeks, a chip per Month the Week
  touches, the Week's Summary under the Days; «+» raises an Entry, «−» lowers it. Over the Week, with
  Telegram's back button returning: any Day's editor, the Month and the Year (Summary and candidates,
  `GET /api/diary/month`, `/year`). Writes (`POST/PATCH/DELETE /api/diary/entries`,
  `…/{id}/raise|lower`, `PUT /api/diary/marks`) show at once, go one at a time, and answer the Day or
  the Entry; a refused write (422) or a failure reloads what is true.
- **Дашборд** (`diaryboard.js`, `GET /api/diary/dashboard?year=&tz=`): two half-year Day maps coloured
  by Mark (drawn by `dashboard.js`'s `dayMap`, like the notes heatmaps), the counts (Entries, Days with an
  Entry, Streak, 🔥 Days), the most repeated Entry and the best Month; ‹ › change the Year. A Day on the
  map opens «записи» with that Day's Week on top.

**Nudge (ADR-020, amended 2026-10-11).** The one thing the `bot` process does with the diary.
`DiaryRuntime.nudge_loop` (`telegram_bot/diary_runtime.py`) runs `nudge_once` every 60 s: the Owner's Zone
comes from notes (`clock.owner_zone`), `domain.nudge_day` answers today's Day from 22:00 local until
midnight, and `domain.claim_nudge` writes the Day into `nudges` only if it has no Entry and no Mark and was
not nudged yet, in one `INSERT … SELECT`, so a Day gets at most one Nudge whatever restarts in between. The bot then
sends the Owner «📔 Сегодня в дневнике пусто» with `✍️ Записать` (`diary_ui.send_nudge`, a web_app to
`/?place=diary`; no button without `BOT_MINIAPP_URL`). A network error hands the claim back for the next
pass (`domain.release_nudge`); a lasting Bot API refusal keeps it. Nothing follows the Nudge: no repeat, no
edit when an Entry appears. The Nudge is off when notes did not start (no Zone) or the diary store cannot
be opened (`diary nudge disabled` in the log); the rest of the bot runs on.

«⚙️ Расходы» in the place menu opens Расходы (`/api/usage`) as a nested screen; Telegram's back button
returns to the Place and Mode left.

An Item is a row (`cards.js` `row()`): the title (a Link's is underlined and opens it), up to two lines of
where it came from and its Gist, its Due and the state of its Enrichment, and its Thumbnail
(`/api/notes/items/{id}/thumb?v=<etag>`, notes ADR-0012), never a third-party URL. Every row has one menu
(`rowactions.js`), opened by «⋮», a right click or a long press: «✓ Готово» (or «↩ Вернуть в «Сделать»»),
«⏰ Напомнить», «🗂 Секции…», «💬 Открыть в чате», «↻ Разобрать заново» after a failed Enrichment, and
«🗑 Удалить» behind a confirm. A swipe (a finger, a mouse drag or two fingers on a trackpad) is its shortcut:
left shows «Ещё» and «Готово», further left closes the Item, right opens its Due. A Status change shows at
once and is sent at once (it also sets or clears `done_at`); a toast offers «↩ Вернуть» for 4 s, which is a
second real change.

The Item view (`detail.js`) has a row «Задача» with «⋮» (show in the chat, enrich again, delete behind a
confirm) and edits the Item: its Sections (its own are shown, «Изменить…» lists them all), its text or
Annotation, and the Due («+ Напомнить» reveals a date-time input, «×» removes it, a past moment is a 422).
«✓ Готово» / «↩ Вернуть в «Сделать»» is Telegram's main button: it saves an unsaved text with the Status,
returns to the list and offers the same «↩ Вернуть». The view reports each change back, and the list
refreshes its counts and rows. While an item
on screen is still being enriched, the app asks about that item alone (`/api/notes/items?ids=`). The
gap grows 3 s → 30 s, or lasts until its scheduled retry. Once it settles, the lists refresh.
The app also reloads the Place and Mode on screen when it becomes visible again; there is no push.
"💬 Открыть в чате" sets `show_requested_at` and closes the app; the bot's
show loop (`NotesRuntime.start_show_loop`, every 2 s) replies to the Item's original message, or sends
the file again by `file_id` if that message is gone (notes ADR-0008). Enrichment files an Item only
while it is in Other alone; once it is anywhere else, re-enrich keeps its Sections.

Every `/api` call carries `Authorization: tma <initData>`. `miniapp/auth.py` checks Telegram's
HMAC against `MINIAPP_INIT_SECRET`, requires `auth_date` under 24 h and admits only
`OWNER_TELEGRAM_ID` (401 or 403). The API only parses and serialises; the rules stay in
`apps/notes`. A re-enrich from the app flips the Item to `pending` and the bot's sweeper does
the work. Edits made in the app do not change the reaction in the chat.

## State that lives only in memory

These are lost on every restart, and a deploy is a restart:
- The card book (`dp["cards"]`): pending cards (TTL 30 min, 60 MB cap), savables (the Owner's
  Drafts), and the live/closed marks.
- The job queue.

After a restart, old card buttons answer "expired", and a job left running is marked `interrupted`
at startup (`db.reconcile_running_jobs`). 💾 under a delivered result keeps working: it reads the
message it is on and the job log, nothing in memory. Notes enrichment survives, because the sweeper picks
up anything left `pending` from sqlite.

## Touch points when you change…

- **An env var:**
  - `shared/src/shared/config.py` (`Settings`, a singleton read at import: a value that fails
    validation kills *every* process, so prefer plain `str`/`int` fields);
  - `.env.example`;
  - the allow-list regex in `infrastructure/deploy.sh` (only `OPENAI_API_KEY`,
    `TELEGRAM_BOT_TOKEN`, `OWNER_TELEGRAM_ID`, `LOG_LEVEL`, `YTDLP_PROXY`, `BOT_*` and `NOTES_*`
    reach the droplet as `bot.env`). Two files go up separately: `miniapp.env`
    (`OWNER_TELEGRAM_ID`, `LOG_LEVEL` and the derived `MINIAPP_INIT_SECRET`) and `funnel.env`
    (`TS_AUTHKEY`). A variable the Mini App needs goes into the first;
  - the README tables.
  Compose `environment:` overrides `bot.env`. `DIARY_DB_PATH` is set only there, for `bot` (the Nudge)
  and `miniapp`, so it is not in `bot.env`.
- **A service or workspace package:**
  - the manifests stage in `infrastructure/docker/Dockerfile` (one `COPY <pkg>/pyproject.toml`
    per member);
  - a service in compose;
  - the health wait in `deploy.sh`.
  Workspace packages are installed editable, so templates and static files are served from
  `/app/<path>` rather than site-packages.
- **A router, filter or Path:** a new message kind is a `Kind` in `telegram_bot/routing.py` plus a handler that `takes` it; the order in `setup_routers` only matters for commands and `MenuCB`. Keep `start` first, and keep each
  router's `owner_filter`.
- **A new agent:** `.skills/create-workflow.md`. It appears on cards through
  `telegram_bot/registry.py`; there are no id switches.

## Production facts

- **Droplet:** 1 vCPU, 961 MB RAM plus 2 GB swap. Memory caps are bot 700 MB, miniapp 160 MB,
  funnel 96 MB and warp 64 MB; together they exceed RAM, so keep new services small.
- **Funnel:** the Mini App's name resolves to three ingress IPs, picked at random per connection,
  and they differ a lot. Measured 2026-10-07 (TLS handshake, 5 samples each): `185.40.234.55` 0.11 s,
  `.198` 0.45–0.77 s, `.75` 3.9–5.3 s. A slow launch can be the ingress, not the app (ADR-019).
  Compare with `curl --resolve <host>:443:<ip> -w '%{time_appconnect}'`.
- **YouTube:** metadata and audio from the droplet IP need `warp`. oEmbed, the Instagram embed
  page and TikTok oEmbed work directly.
- **Telegram:** at most one poller per token. Running `telegram-bot` locally while production
  is up causes 409 Conflict. Occasional `Bad Gateway` lines in the log are Telegram-side noise.
- **Data:** the databases live only in the `appdata` volume, and the File and Voice bytes only in
  Telegram. The Owner's Mac pulls a daily Backup of both into iCloud Drive (ADR-018;
  `infrastructure/README.md` "Backups", with the restore procedure).
