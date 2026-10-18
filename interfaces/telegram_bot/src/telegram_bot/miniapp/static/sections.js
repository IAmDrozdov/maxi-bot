// The Section form: create, edit, delete (Other and From the bot cannot be deleted, ADR-0002 and ADR-0013).

import { api, attempt, confirmAction, el, haptic, setBack, toast } from "./core.js";
import { FROM_BOT, OTHER } from "./cards.js";

const DEFAULT_COLOR = "#8a8a8a";
// A Section's emoji is its reaction in the chat too, so only these: notes.domain.sections.MARKS (the Bot API's
// reaction emoji without ✍ 👌 👎, which stay the Acknowledgement's own).
const MARKS = [
  "❤", "👍", "🔥", "🥰", "👏", "😁", "🤔", "🤯", "😱", "🤬", "😢", "🎉", "🤩", "🤮", "💩", "🙏", "🕊", "🤡",
  "🥱", "🥴", "😍", "🐳", "❤\u200d🔥", "🌚", "🌭", "💯", "🤣", "⚡", "🍌", "🏆", "💔", "🤨", "😐", "🍓", "🍾",
  "💋", "🖕", "😈", "😴", "😭", "🤓", "👻", "👨\u200d💻", "👀", "🎃", "🙈", "😇", "😨", "🤝", "🤗", "🫡", "🎅",
  "🎄", "☃", "💅", "🤪", "🗿", "🆒", "💘", "🙉", "🦄", "😘", "💊", "🙊", "😎", "👾", "🤷\u200d♂", "🤷",
  "🤷\u200d♀", "😡",
]; // prettier-ignore

// A grid of MARKS; one another Section has is shown off with its name. Returns { value() }.
function markPicker(box, current, taken) {
  const wrap = el("div", "field-label");
  wrap.append(el("span", "hint", "Иконка (она же реакция бота в чате)"));
  const grid = el("div", "mark-grid");
  grid.setAttribute("role", "radiogroup");
  let value = MARKS.includes(current) ? current : "";
  const buttons = MARKS.map((mark) => {
    const button = el("button", "mark-pick", mark);
    button.type = "button";
    button.setAttribute("role", "radio");
    const owner = taken.get(mark);
    if (owner) {
      button.disabled = true;
      button.title = `Уже у секции «${owner}»`;
    }
    button.onclick = () => {
      value = mark;
      paint();
    };
    return button;
  });
  function paint() {
    buttons.forEach((button, i) => {
      button.classList.toggle("on", MARKS[i] === value);
      button.setAttribute("aria-checked", String(MARKS[i] === value));
    });
  }
  paint();
  grid.append(...buttons);
  wrap.append(grid);
  box.append(wrap);
  return { value: () => value };
}

function labelled(box, label, type, value, attrs = {}) {
  const wrap = el("label", "field-label");
  wrap.append(el("span", "hint", label));
  const input = type === "textarea" ? el("textarea", "textarea") : el("input", "input");
  if (type !== "textarea") input.type = type;
  input.value = value;
  Object.assign(input, attrs);
  wrap.append(input);
  box.append(wrap);
  return input;
}

// Grows a textarea to its text, so a long hint reads without scrolling inside the box.
function fitHeight(area, scroller) {
  const y = scroller.scrollTop; // the "auto" reset shortens the content for a moment and would move it
  area.style.height = "auto";
  area.style.height = `${area.scrollHeight + area.offsetHeight - area.clientHeight}px`;
  scroller.scrollTop = y;
}

// Shows the form for `section` (null: a new one) in `host` instead of `list`, as a nested screen (ADR-021): Telegram's
// back button closes it; `scrolls` are the list's scrollers, restored on close; `sections` all of them (whose emoji is taken). saved() runs when a change lands, closed() when the form goes away. Returns { close }.
export function openSectionForm({ list, scrolls, host, section, sections, saved, closed }) {
  const listScroll = scrolls.map((node) => node.scrollTop); // display:none drops a scroller's offset, so it is put back on close
  let busy = false;
  let open = true;

  function close() {
    if (!open) return;
    open = false;
    host.hidden = true;
    host.replaceChildren();
    list.hidden = false;
    setBack(null);
    scrolls.forEach((node, i) => (node.scrollTop = listScroll[i]));
    closed();
  }

  async function act(fn) {
    if (busy) return undefined;
    busy = true;
    box.classList.add("busy");
    try {
      return await attempt(fn);
    } finally {
      busy = false;
      box.classList.remove("busy");
    }
  }

  const box = el("article", "detail");
  box.append(el("h2", "title", section ? "Изменить секцию" : "Новая секция"));
  const name = labelled(box, "Название", "text", section?.name ?? "", { name: "name", maxLength: 40 });
  const taken = new Map(sections.filter((other) => other.id !== section?.id).map((other) => [other.emoji, other.name]));
  const emoji = markPicker(box, section?.emoji ?? "", taken);
  const color = labelled(box, "Цвет", "color", section?.color ?? DEFAULT_COLOR, { name: "color" });
  // The Classifier never files into From the bot, so that Section has no hint to give it.
  const fromBot = section?.slug === FROM_BOT;
  const hint = fromBot
    ? null
    : labelled(box, "Подсказка для разбора: что сюда относится", "textarea", section?.hint ?? "", {
        name: "hint",
        maxLength: 500,
        rows: 3,
      });
  if (hint) hint.oninput = () => fitHeight(hint, host);

  const actions = el("div", "actions");
  const save = el("button", "btn", "Сохранить");
  save.onclick = async () => {
    const body = { name: name.value, emoji: emoji.value(), color: color.value };
    if (hint) body.hint = hint.value;
    const done = await act(() =>
      section
        ? api(`/notes/sections/${section.id}`, { method: "PATCH", body })
        : api("/notes/sections", { method: "POST", body }),
    );
    if (!done) return;
    haptic("success");
    toast(section ? "Сохранено" : "Секция создана");
    saved();
    close();
  };
  actions.append(save);

  if (section && section.slug !== OTHER && !fromBot) {
    const remove = el("button", "btn danger", "Удалить");
    remove.onclick = async () => {
      const question = `Удалить секцию «${section.name}»? Заметки, у которых она единственная, уйдут в «Остальное».`;
      if (!(await confirmAction(question))) return;
      const gone = await act(async () => {
        await api(`/notes/sections/${section.id}`, { method: "DELETE" });
        return true;
      });
      if (!gone) return;
      toast("Удалено");
      saved();
      close();
    };
    actions.append(remove);
  } else if (section) {
    const why = fromBot
      ? "Здесь лежат результаты бота, которые ты отложил, поэтому её нельзя удалить."
      : "Сюда попадает всё, что не подошло другим секциям, поэтому её нельзя удалить.";
    box.append(el("div", "hint", why));
  }
  box.append(actions);

  host.replaceChildren(box);
  list.hidden = true;
  host.hidden = false;
  if (hint) fitHeight(hint, host);
  setBack(close);
  host.scrollTop = 0;
  return { close };
}
