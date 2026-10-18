# The Acknowledgement is a reaction on the Owner's message; one Capture is one Item; only a single website link gets a card

_Amended by ADR-0010 (2026-10-07): there is no Archive or Trash; a re-sent Link that is `done` stays `done`._
_Amended by ADR-0011: the bot leaves a message for a Reminder — the Due line and the reminder itself._
_Amended by ADR-0013: a BotResult's Acknowledgement goes on the bot's own result message, and a result message carries a 💾 button._
_Amended 2026-10-18: once Enrichment has filed an Item the bot also replies with its Sections line, «👌 🍿 Посмотреть» (see below)._

The chat should hold the Owner's messages and the agents' outputs, nothing else. So the bot no
longer answers a Capture with a message. It sets one reaction on the Owner's own message and
changes it as the Item moves: ✍ while it works (Enrichment retries included), 👌 once the Item is
in notes (a duplicate included), 👎 when the Owner should look (Capture failed: send again;
Enrichment ended `failed`: the Item is in Other with no Gist). Three states, because the Owner
should not have to remember more. The Bot API offers no ✅, ❌ or ♻️ to bots, and a bot may set
only one reaction per message.

A Capture becomes exactly one Item: a Link when the message holds one URL, otherwise a Note with
the whole text. A forwarded post's first URL is often the channel's own footer link, so the bot
does not guess the main one. A duplicate Link that is archived or trashed goes back to active,
because sending it again means it is wanted again.

Only a message with exactly one non-social URL gets the ask-first card (ADR-0007 narrowed). Plain
text, several URLs, social links, files and voice are saved straight away. 💾 on the card deletes
the card; Cancel and "Replaced" cards stay as they were, because invitees share them.

## Amended 2026-10-18: the Sections line

A reaction cannot name the Section an Item landed in (Telegram allows about 73 fixed emoji, and no
Section emoji is among them), and the Owner wants to see it. So after Enrichment the bot replies to
the Capture with one line: 👌 and each Section's emoji and name from the Section settings, joined by
" · " (e.g. «👌 🍿 Посмотреть»). The reaction stays.

- It is sent from `notify`, once the Item is filed, not at Capture: a new Item sits in Other until the Classifier refiles it. An Item left in «📦 Остальное» gets the line too.
- A Notice that also carries a Due makes ONE two-line message: the Sections line, then «⏰ …» with `📅 Перенести`.
- A duplicate link whose Enrichment is `done` gets the line at once, as a reply under the NEW message (no Enrichment runs). A pending or failed duplicate gets none.
- A BotResult has no chat message: 💾 answers with a toast, «👌 📑 От бота» (or «Уже в заметках · …» for a duplicate).
- A failed Enrichment (👎) gets no line. A later successful re-enrich sends it, because nothing was recorded.
- Once per Item: the sent message id is kept in `items.tg_ack_message_id`, and a re-enrich from the Mini App re-sets the reaction but sends no second line (an Item saved before 2026-10-18 has no id, so its first re-enrich sends one line).
- The line is silent (`disable_notification`) unless it carries a Due, as the reaction never pinged the phone.

## Considered Options

- Keep the Acknowledgement as a message — rejected: one bot message per Capture is the clutter this removes.
- Keep a message only for duplicates and failures — rejected by the Owner: the bot leaves no message in any case.
- A temporary "💾 Сохраняю…" message deleted at the end — rejected: the feed still jumps; ✍ says the same.
- Separate reactions for retry, duplicate and the two failures — rejected: five emoji to remember; the three left say wait, done or look.
- One Item per URL, or a Link from the first URL — rejected: a single reaction cannot report several Items, and the first URL is often the wrong one.

## Consequences

- The chat loses the per-Item `✏️ Открыть`, `🤖 Агенты` on a saved Link, `↩️ Вернуть`, and the Sections and Gist text; they live in the Mini App (📒) only.
- Enrichment and the sweeper update the reaction on `tg_message_id`; `tg_ack_message_id` is no longer written (written again by the 2026-10-18 amendment: the id of the Sections reply).
- A Note with several URLs gets no oEmbed preview and no URL dedupe.
- Running the Owner's own text through an agent now needs a `.txt` file; plain text no longer opens a card.
- Acknowledgement messages already in the chat stay; "💬 Показать в чате" still answers with a message, because the Owner asked for it.
