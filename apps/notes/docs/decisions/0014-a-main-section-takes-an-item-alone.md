# A Main Section takes an Item alone; the Sections it would also be in become its Footnote

Status: Accepted (2026-11-08). Amends ADR-0002.

The Owner saves a lot about AI: videos, posts, books. Most of the Owner's Sections are close to AI, so the
Classifier filed one such Item under «Посмотреть», «Почитать», «Работа» or «Пет-проекты», often under two
or three of them at once. The Owner wanted AI content in one place, without crowding the other Sections,
and still wanted to see whether an Item is something to read or to watch.

The Owner can now mark a Section **Main** («Главная»). When the Classifier files an Item and one of its picks
is a Main Section, the Item goes only into the Main picks, plus any Section the Owner's own words name as a
topic or project («по работе…», «в пет-проект…»). Words that say what to do with the Item («посмотреть»,
«почитать») do not keep their Section. The other picks become the Item's **Footnote**: the row and the card
show them («👀 Посмотреть · 👨‍💻 Работа»), but the Item is not in them. Without a Main pick, Filing works as
before.

- The Classifier still picks one to three Sections, the most fitting first. A Main Section that fits is
  always among them, even when the Owner's words name another Section. It also answers `owner_sections`.
  `sections.split_filing` makes the split in code, so the rule does not depend on the model.
- A Footnote is written only when the Classifier files an Item, which is only from Other alone (ADR-0010).
  An Owner edit to the Filing leaves the Footnote alone. A Footnote never shows a Section the Item is in.
- Other and From the bot can never be Main, and a Footnote never names them.
- The Owner chose that AI holds only content about AI. The Owner's own tasks and ideas stay in their
  Sections even when they are about AI. That rule is in the AI Section's hint, not in code.

## Considered Options

- Format Sections («Посмотреть», «Почитать») give way to any topic Section. Rejected by the Owner: it
  would also move work and pet videos out of «Посмотреть». Only AI was the problem.
- Rank every Section and file an Item under its top one only. Rejected: that is folder semantics again,
  which ADR-0002 rejected, and the Owner's cross-cutting Filings («tg bot» + «Подумать») are real.
- Hints alone (tell the model to prefer AI). Rejected: an AI video really does fit «Посмотреть» too, and
  with up to three picks a hint cannot keep it out. The Owner wanted the Footnote, not a lost fact.
- The Classifier returns the Footnote itself. Rejected: the rule should not depend on the model. The model
  only says what the Owner's words name.

## Consequences

- `sections.is_main`; table `item_footnotes` (cascades with the Item and the Section). `Item.footnote`
  never overlaps `Item.sections`.
- Lists, counts, the Dashboard and Search read the Filing only. The Footnote is shown, never filtered on.
- ADR-0002's tag semantics stand: an Item in a Main Section is still in every Main pick and every Section the
  Owner named, and the Owner can file it anywhere afterwards.
