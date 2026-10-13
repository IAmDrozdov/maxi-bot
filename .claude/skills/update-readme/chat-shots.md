# Chat pictures for the README

Rules, mechanics and the script template for the pictures taken in the Owner's real bot chat
(Telegram Web, Owner's Chrome). The `update-readme` skill writes the shot script from the template
below, the Owner approves it, and a `general-purpose` agent (`sonnet`, medium) runs up to three
shots from it. Machine facts (Chrome profile, the chat's URL) are in `CLAUDE.local.md`.

## Rules
- Follow `.claude/skills/live-test/telegram-web.md` (guardrails, snapshots through `snap.py`, the
  Mini App iframe, cleanup ids). Two deviations, both the Owner's call of 2026-10-13:
  - No `🧪` label: the pictures show messages written for the README. Only the script's messages
    are sent, and only the script's bubbles are captured.
  - The paid taps the script names are allowed, nothing else. Stop if a button shows more than
    $0.03 or the total would pass $0.06 (the script's cost line). Never tap `💾 В заметки` on a
    link card or under a result, never `🔊 Test`.
- Never run `telegram-bot` locally (409 Conflict with production). Do not open the Mini App except
  for the Item cleanup.
- Anything the script does not name is not done. An action you cannot run as written is `BLOCKED`.

## Driving the chat (Claude in Chrome)
The `chrome-devtools` MCP needs Full Disk Access for `com.anthropic.claude-code`, which a Desktop
session lacks; the run of 2026-10-13 used `claude-in-chrome` (load it as its skill says), whose
`computer zoom` can save a cropped 2x image. Steps that worked:
- `navigate` to the chat URL opens a tab in the Owner's logged-in Chrome. Close it at the end.
- Send text: click the composer (~(840, 772) in the 1483x812 frame), `type`, `key Return`.
- Send a file: `javascript_tool` builds a `File` and a `DataTransfer` and dispatches a `paste`
  `ClipboardEvent` on `#editable-message-text`; the «Send File» modal opens, click its send arrow
  (~(893, 472)). A `drop` event does nothing; never click the attach button (a native picker).
- Buttons: `scrollIntoView` the button, scale its `getBoundingClientRect` centre by
  `1483 / innerWidth` and `812 / innerHeight`, and click there.
- Waiting: poll `.Message` elements with `data-message-id` above the last known id, 2 s steps, at most
  ~36 s per call (the tool times out at 45 s). Return JSON: a `key=value` text is blocked as a
  query string. The Owner's reaction is `.Reactions` on the `.own` bubble.

## Isolating a frame
Never zoom on the full page: it holds the chat list, the header and the Owner's name.
1. Tag the shot's bubbles `readme-keep` (`.Message[data-message-id]`) and inject
   `<style id="readme-shot">` with `.Message:not(.readme-keep){visibility:hidden}` (never
   `display:none`: the list unmounts), `.sticky-date,.unread-divider,.local-action-message,
   .MiddleHeader,.Composer,button:has(> i.icon-arrow-down){visibility:hidden}` and
   `.readme-keep .Reactions .Avatar,.readme-keep .EmbeddedMessage .embedded-sender-wrapper{display:none}`
   (the bot's avatar in a reaction and the Owner's name in a reply quote). Keep every bubble that sits
   between two kept ones, so no gap shows.
2. Scroll `.MessageList` to its bottom. Take the union of each kept bubble's `.message-content` and
   its inline `button` rects, pad 16 px, convert to the 1483x812 frame (above).
3. `computer zoom` on that region with `save_to_disk`; copy the path from the result to
   `.local/readme-shots/<file>.png`. Over 400 KB: quantize to 256 colours with
   `uv run --no-project --with pillow python` (a resample makes it larger).
4. Remove the style and the tags.

## Pilot
SKILL.md step 4 dispatches the run's first shot alone. Shoot it, write every message id to
`ids.txt`, return the frame's path and stop: no cleanup yet, the later shots reuse the same chat
state. The dispatching session reads the frame against the privacy checklist, then dispatches the
rest or has the shot redone. A frame the checklist rejects restarts that shot only.

## Tracking and cleanup
- Cleanup runs once, after the last shot or when the run is abandoned, by the dispatching session
  or an agent it names: a returned pilot never ends the run, and `ids.txt` is the list.
- Every message the run sends or receives: its Bot API id to `.local/readme/ids.txt`, one per line
  (derive it as `telegram-web.md` "Cleanup" says; an Item's `tg_message_id` comes from
  `infrastructure/droplet.sh sql notes "<SELECT …>"` before the Item is deleted).
- Delete the shoot's Items in the Mini App: «⋮» in the row → «🗑 Удалить» → confirm. Then
  `uv run python .claude/skills/live-test/send_media.py delete --from .local/readme/ids.txt`;
  a message with no id goes through Telegram Web's context menu «Удалить».
- Counts (Items total, Section counts) must be back to the script's "before" numbers. The two job
  rows of the paid runs stay in «Расходы».
- Delete every snapshot file named under `.local/`.

## Privacy checklist
A picture is rejected on any of: the chat list, the chat header, an avatar, a username, a phone
number, a real name, a bubble the script did not send, a `🧪` label, real Item, diary or cost data,
a URL, hostname or `tgWebAppData`, browser chrome, or an image as large as the window (the crop
failed). The Owner sees every chat picture before it is copied to `docs/images/readme/`.

## Shot script template
Write `.local/readme/shot-script.md` in Russian from this block. One case per shot, in order;
keep only the stale shots when the skill re-shoots.

```markdown
# Съёмка для README: <дата>

**Статус:** черновик · одобрено <дата> · снято <дата>
**Стоимость:** <N> запусков агента + <M> вызовов классификатора, не больше $<сумма>
**Перед началом:** Items всего <n>; по секциям: <секция n, …> (`droplet.sh sql notes`)

## Кадры

### 0. Подготовка
- Нажать: `list_pages`; открыть чат бота (URL в `CLAUDE.local.md`); тема Telegram Web светлая
  (если тёмная — переключить на съёмку и вернуть).
- Итог: —

### 1. <файл>.png
- **Файл:** <имя картинки в docs/images/readme/>
- **Отправить:** <точный текст или файл>
- **Нажать:** <кнопка или «—»>
- **Жду:** <что должно появиться и сколько ждать>
- **Кадр:** <какие пузыри попадают в кадр>
- **Платно:** <«нет» или «да: что именно и ~сумма»>
- **Уборка:** <что удалить>
- **Итог:** —

## Уборка
- Items съёмки удалить в Mini App; сообщения: `send_media.py delete --from .local/readme/ids.txt`.
- Сверить числа с «Перед началом».
```
