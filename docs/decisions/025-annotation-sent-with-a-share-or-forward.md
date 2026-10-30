# ADR-025: A short text sent with a share or forward is that Capture's Annotation

Status: Accepted (2026-10-30). Amends notes ADR-0009 (one Item per Capture message): a short text sent in the same second as the message after it is not a Capture of its own.

## Context

Telegram sends the words the Owner types in a share sheet or a forward dialog as a message of its own,
just before the shared link or the forwarded messages. On 2026-10-29 an Instagram reel shared with
«Написать это … по поводу 992» became two Items, a Note in «Остальное» and the Link in «Посмотреть»
(items 188 and 189, messages 521 and 522, same second). The Owner wants one Item, and wants their
words to decide its Section, with the post's content only as the fallback.

## Decision

1. **Held, then paired.** An Owner's own short text (no link, plain or hidden; not forwarded, not a
   command, under `BOT_POST_VOICE_MIN_WORDS`; `routing.may_annotate`) waits `HOLD_S` (1.5 s) before it
   is handled. A message after it whose Telegram date is at most 1 s later and that notes can save
   with words (a link, savable link card, forward, photo, file or voice; `routing.takes_annotation`)
   takes the text as its Annotation. Every such message in the burst takes it, so a comment over several
   forwarded posts goes on each. A text nothing took is captured as before, 1.5 s late.
2. **Telegram's dates, not arrival.** After a restart the bot gets queued messages together; the date
   check keeps a text sent minutes before a link apart from it.
3. **In a middleware, before any await.** `pairing.PairingMiddleware` is the first message middleware
   after access. Polling starts one task per update in order, so a text is held before the next message
   looks for it. The book (`dp["pairing"]`) lives in memory; a restart inside the 1.5 s loses nothing
   but the pairing.
4. **The Owner's words.** The Annotation comes first in the Item's `text`, then the message's own words
   (a Link's words around the URL, an own photo's caption). On a *forwarded* Note, File or Voice the
   Annotation is the whole `text` and the forward's own words go to `caption` («Описание» in the Mini
   App), so the Classifier gets them apart. Enrichment keeps that caption when it fetches nothing.
5. **One Item, one reaction.** The text's message gets no reaction: the Acknowledgement is on the shared
   or forwarded message, which the Item points at. A website link still opens its card; 💾 saves the
   Annotation with it, ✖️ drops both, as for one message with words and a link.
6. **The Classifier.** Its Section rule now says the Owner's words decide when they say what to do with
   the thing or what it is for; the content (caption, title, picture) decides only when they do not.

## Consequences

- Every short text the Owner sends is filed 1.5 s later than before.
- A forward without a comment is unchanged: its words stay the Item's `text`.
- An agent document (`document_card`) never takes a text; the text stays a Note.
- No schema change: `caption` already exists on every Item.
