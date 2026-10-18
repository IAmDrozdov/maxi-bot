# The Acknowledgement is a reaction on the Owner's message; one Capture is one Item; only a single website link gets a card

_Amended by ADR-0010 (2026-10-07): there is no Archive or Trash; a re-sent Link that is `done` stays `done`._
_Amended by ADR-0011: the bot leaves a message for a Reminder — the Due line and the reminder itself._
_Amended by ADR-0013: a BotResult's Acknowledgement goes on the bot's own result message, and a result message carries a 💾 button._
_Amended 2026-10-18: *saved* is shown as the emoji of the Item's first Section, so a Section's emoji must be a reaction the Bot API allows (below)._

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

## Amended 2026-10-18: the reaction names the Section

The Owner wants to see where a Capture landed without the bot writing to the chat. Once the Item is
in notes, the bot's one reaction is the emoji of its first Section in list order instead of 👌 (✍
and 👎 are unchanged; 👌 remains for an Item whose Section has no usable emoji, and for a duplicate
whose Enrichment is still pending, which is only provisionally in Other). A BotResult gets the
emoji of «От бота». A bot may set one reaction, and only from the Bot API's fixed emoji list, so a
Section's emoji must come from that list (`sections.MARKS`: the list minus ✍ 👌 👎) and differ
from every other Section's; the Mini App's Section form offers a grid of them instead of a text
field. On 2026-10-18 the existing Sections were mapped by the Owner (🍿→👀, 📖→🤓, 🍜→🌭, 🛒→💅,
📍→🎉, 💼→👨‍💻, 🛠→🔥, 📑→👾, 👽/📦→👻, 💭→🤔, 🤖→⚡, 💻→🆒, 📕→🦄; `sections.FORMER_MARKS`,
applied by `seed_sections` on start). A reply naming the Section was tried the same day and
rejected by the Owner: it is a message, which this ADR exists to avoid.

## Considered Options

- Keep the Acknowledgement as a message — rejected: one bot message per Capture is the clutter this removes.
- Keep a message only for duplicates and failures — rejected by the Owner: the bot leaves no message in any case.
- A temporary "💾 Сохраняю…" message deleted at the end — rejected: the feed still jumps; ✍ says the same.
- Separate reactions for retry, duplicate and the two failures — rejected: five emoji to remember; the three left say wait, done or look.
- One Item per URL, or a Link from the first URL — rejected: a single reaction cannot report several Items, and the first URL is often the wrong one.

## Consequences

- The chat loses the per-Item `✏️ Открыть`, `🤖 Агенты` on a saved Link, `↩️ Вернуть`, and the Sections and Gist text; they live in the Mini App (📒) only.
- Enrichment and the sweeper update the reaction on `tg_message_id`; `tg_ack_message_id` is no longer written.
- A Note with several URLs gets no oEmbed preview and no URL dedupe.
- Running the Owner's own text through an agent now needs a `.txt` file; plain text no longer opens a card.
- Acknowledgement messages already in the chat stay; "💬 Показать в чате" still answers with a message, because the Owner asked for it.
