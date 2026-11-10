# A Section the Owner's words name outranks a Main Section

Status: Accepted (2026-11-10). Amends ADR-0014.

On 2026-11-10 the Owner rebuilt the Sections. «AI» became «ПО», a Main Section for everything about
software, development and computers, not only AI. «Книгописатель» was deleted with its Items.
«Работа» and «Пет-проекты» now take an Item only when the Owner's own words tie it to work or to the
Owner's own project. An article on a work topic goes by its topic. Anything about a pet project that has
its own Section goes there, not into «ПО».

When the Classifier picks a Main Section and the Owner's words name a Section that is not Main («по
работе», «в пет-проект», a project's name), the named Sections take the Item alone. The Main pick drops
out: it is neither in the Filing nor in the Footnote. The other picks become the Footnote, as they do
under a Main Section. When the Owner names no Section, or names only Main ones, ADR-0014 stands.

- `sections.split_filing` makes the split in code. The model only reports what the Owner named
  (`owner_sections`).
- What «ПО», «Работа» and «Пет-проекты» hold is in their hints, not in code. The built-in hints of
  «Работа» and «Пет-проекты» changed in `STARTER_SECTIONS`. The production hints were rewritten by the
  one-off migration that renamed «AI».

## Considered Options

- The named Section plus the Main pick (ADR-0014 as it was). Rejected by the Owner: a link for work
  would sit in «ПО» too.
- The named Section takes the Item and the Main pick goes into the Footnote. Rejected by the Owner:
  «ПО» should not show on such an Item at all.
- A hint alone («не сюда, если владелец назвал работу»). Rejected for the reason in ADR-0014: the rule
  should not depend on the model.

## Consequences

- A software link the Owner marked «по работе» lands in «Работа» alone, with «👀 Посмотреть» or
  «🤓 Почитать» as its Footnote. When the Classifier does not pick a Main Section at all, the Item is
  filed under every pick, as before ADR-0014.
