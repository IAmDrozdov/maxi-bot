# ADR-024: A long text is saved at once and offered a voice-over

Status: Accepted (2026-10-21). Amends notes ADR-0009 (a dated note there) and narrows ADR-015 §4 (a long text with one link is no link card).

## Context

The Owner forwards long Telegram posts and wants to listen to them on the move. Plain text never opens a
card (notes ADR-0009: it is saved straight away), and a long post usually carries one hidden link, a
`text_link` entity, which made it a link card that scraped that link instead of the post.

## Decision

1. **Save first, offer second.** A text of at least `BOT_POST_VOICE_MIN_WORDS` words (default 200,
   counted by a whitespace split with URLs removed; 0 turns the feature off) is saved by notes exactly as
   any text: reaction, Due line, Enrichment. The bot then *replies* to it with
   `🔊 Voice this post? · N words`, one priced `pdf_tts` button and ✖️. A post is never lost when the
   offer is ignored.
2. **Any long text.** Forwarded or the Owner's own. A media caption is out of scope (at most 1024
   characters, rarely 200 words). A command is unaffected.
3. **Decided in routing.** `Path("capture_text", voice_offer=True)`, checked before the link rules, so a
   long text with one website link is a Note or Link in notes, not a link card. A short text with one link
   is unchanged.
4. **Voice only, never translated.** The text goes to `pdf_tts` as a `.txt` source, with the Owner's
   stored settings but `tts.translate` forced off, at pricing, at Run's re-pricing and in the job
   (`cards.config_for`). Posts are expected in Russian, so there is no translate button, no ⚙️, no 💾.
5. **The offer goes away without a trace.** ✖️ deletes the message (not "Cancelled"). The Owner's next
   message of any kind (text, file, voice, command, link) deletes a live offer, through an outer message
   middleware registered after `AccessMiddleware`; so does a card opened for a newer message than the
   offer's own, or from a button, and a new offer deletes the previous one. Once Run is tapped the message is the job status and stays: ✖️ takes the same
   per-user run lock as Run and only answers for a message that has become a job. A tap on an offer the
   book no longer holds (30 min TTL, a restart) answers that it expired and deletes it for Run; ✖️ just
   deletes it.
6. **A card kind with its own slot.** `CardBook.offer_voice` owns it, sent only once priced (a `.txt` is
   priced offline; if pricing fails, nothing is sent and the cause is logged). The offer lives in a
   per-chat slot apart from the pending job and the live card status, looked up by (chat id, message
   id), so a long text never takes a link or document card's job or status. Staleness keys on the Owner's
   message ids, not on a bot status id outside the button case below (a status is newer than every Owner message already in the
   chat, so a card for an older message would otherwise kill a newer text's offer). The middleware
   records the newest Owner message per chat (`CardBook.saw`); a card opened by a message records that
   message and discards only an offer whose origin is older than it, while a card opened from a button
   (the bot's own message) records its status and discards the live offer. An offer
   is not sent when a newer message arrived while it was priced, one overtaken during the send is
   deleted again, and a Run rejected after a newer message (spend block, queue full) deletes the offer
   instead of putting it back. Notes down: the text gets 👎 as today and the offer still appears.

## Why a bot message is allowed here

Notes ADR-0009 removed the per-Capture message so the chat holds the Owner's messages and the agents'
outputs only. The offer is an agents' card, like the Estimate card of a link: a question with a price
that needs an answer, not an acknowledgement. It is transient (✖️, the next message or Run removes or
reuses it), so an unwanted offer costs no clutter. The Acknowledgement stays the reaction.

## Considered Options

- Ask first with 💾 on a card, as for a link (ADR-015 §4). Not chosen by the Owner (2026-10-21): a post is not saved
  unless he taps, and most long texts are only to be kept.
- Offer translate too. Rejected: posts are expected in Russian, and "Translate first" would silently change
  the text the Owner meant to hear.
- Offer on forwards only. Rejected: the Owner's own long notes are listened to the same way.
- Count characters. Rejected: the Owner thinks in words; a threshold in words matches how he reads a post.
- Delete the offer on a timer. Rejected: the next message already clears it, and a timer is another
  piece of in-memory state.

## Consequences

- Every long text costs one extra bot message until the Owner's next message; nothing is billed until Run.
- A link or document card keeps its pending job and live status when a long text arrives; only the
  offer is separate. The offer's state is in memory: a restart leaves an offer in the chat until it is
  tapped (Run then answers that it expired and deletes it; ✖️ just deletes it).
- The Mini App is unchanged.
