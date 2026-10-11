# A BotResult is a fifth kind, kept in its own built-in Section

The bot answers a document or a link with a translated document or dubbed audio. The Owner often has no
time for it in the moment, and the message scrolls away. So the bot's result message carries a button, and
pressing it is a Capture: the message becomes a **BotResult**, an Item of a fifth kind.

A BotResult is always in **From the bot** («От бота»), a built-in Section that cannot be deleted. Nothing
else is ever filed there, and the Classifier does not see it. The Owner may file a BotResult under other
Sections too, but cannot take it out of this one.

A BotResult has no Enrichment. What it is was known when it was made: its title, which Workflow made it,
its size or duration. It keeps those, the `file_id` and the message; cost and facts stay in the message's
caption, one «Открыть в чате» away.

The Acknowledgement goes on the bot's own message: 👌, and the button goes away.

A result split over several voice messages is one BotResult. The button is under the first part only, and
the Item is that message.

## Considered Options

- Save it as a File (a document) or a Voice (audio). Rejected: a Voice is transcribed during Enrichment,
  so the Owner would pay to recognise speech the bot had just synthesised; a File's Gist is written from
  its file name alone; and both are defined as things sent to the bot, not made by it.
- A File with a "from the bot" mark that skips Enrichment. Rejected: a second switch beside `kind`, to be
  checked wherever `kind` is.
- A third Place in the Mini App with its own store. Rejected: Status, Due, Search and «Открыть в чате»
  would be written a second time, and ADR-021 has two Places.
- No Section of its own; the Classifier files it. Rejected: the queue of things the bot made would spread
  over the thematic Sections, and there is nothing to classify without Enrichment.
- A closed Filing (From the bot and nothing else). Rejected by the Owner: a translated book belongs in
  «Почитать» as well.
- A button that turns into a link to the Item, or a toggle that un-saves on a second press. Rejected by the
  Owner for the reaction every other Capture already gets (ADR-0009).
- One BotResult per part of a split result, or one that remembers every part. Rejected by the Owner for one
  button and one Item; an Item still refers to one message and one file.

## Consequences

- Amends ADR-0002: there are two built-in Sections that cannot be deleted. Other still holds whatever has
  no Section; From the bot holds exactly the BotResults. A BotResult is never in Other.
- Amends ADR-0009: an Acknowledgement can sit on a message the bot sent.
- The button reads everything from the message it is under and from the bot's own job log, not from memory,
  so it works after a restart. Results delivered before this change have no button.
- Once Captured, a result cannot be Captured again: the button is gone. Deleting the BotResult in the Mini
  App leaves the file in the chat and no way back into notes from there.
- If the messages of a split result are deleted from the chat, «Открыть в чате» can send the first part
  again and no other.
- `kind` has a CHECK, so adding a value rebuilds the `items` table. A Backup comes first.
- The daily Backup copies a BotResult's bytes like any Item with a file, up to Telegram's 20 MB download
  limit; a larger one is listed as `too_big`.
- A BotResult can have a Due, be done, be found by Search and be counted on the Dashboard like any Item.
