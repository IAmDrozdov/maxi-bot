"""HTML to Markdown for listening: prose, tables, code, diagrams and pictures, no markup."""

from __future__ import annotations

import re
import textwrap
from collections import deque
from collections.abc import Callable
from html.parser import HTMLParser
from urllib.parse import urljoin

from shared.obs import get_logger

log = get_logger(__name__)

_MAX_DEPTH = 200
_VOID = frozenset(
    [
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    ]
)
_SKIP = frozenset(
    [
        "head",
        "nav",
        "style",
        "noscript",
        "template",
        "button",
        "select",
        "option",
        "input",
        "textarea",
        "iframe",
        "canvas",
        "audio",
        "video",
        "object",
        "embed",
        "map",
    ]
)
_HEAD_OK = frozenset(["title", "meta", "link", "script", "style", "base", "noscript", "template"])
_INLINE = frozenset(
    [
        "a",
        "abbr",
        "acronym",
        "b",
        "bdi",
        "bdo",
        "big",
        "br",
        "cite",
        "code",
        "data",
        "del",
        "dfn",
        "em",
        "font",
        "i",
        "img",
        "ins",
        "kbd",
        "label",
        "mark",
        "q",
        "s",
        "samp",
        "small",
        "span",
        "strike",
        "strong",
        "sub",
        "sup",
        "time",
        "tt",
        "u",
        "var",
        "wbr",
    ]
)
# A tag that, inside an inline element, makes it a container instead.
_BLOCKISH = frozenset(
    [
        "address",
        "article",
        "aside",
        "blockquote",
        "body",
        "center",
        "dd",
        "details",
        "dialog",
        "div",
        "dl",
        "dt",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hgroup",
        "hr",
        "li",
        "main",
        "menu",
        "ol",
        "p",
        "pre",
        "section",
        "summary",
        "svg",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "ul",
        "script",
    ]
)
_P_CLOSERS = frozenset(
    [
        "address",
        "article",
        "aside",
        "blockquote",
        "center",
        "details",
        "dialog",
        "div",
        "dl",
        "fieldset",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "main",
        "menu",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "ul",
    ]
)
_CODE_TAGS = frozenset(["code", "kbd", "samp", "tt"])
_ROW_PARTS = frozenset(["thead", "tbody", "tfoot"])
_WS = re.compile(r"\s+")
_NBSP = str.maketrans({"\xa0": " ", " ": " ", " ": " ", "​": ""})
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.I)
# Text that looks like a tag or an entity is escaped so the speech stage unescapes it, not strips it.
_CONSENT_CLASS = "_iub_cs_activate"
_CONSENT_ATTRS = frozenset(["data-category", "data-cookieconsent", "data-service"])
_BOOK_ATTRS = frozenset(["data-lang", "data-title"])
_ENTITY_OR_ANGLE = re.compile(r"[<>]|&(?=#?\w+;)")
_ESCAPES = {"<": "&lt;", ">": "&gt;", "&": "&amp;"}
_TAG_RE = re.compile(r"<[^>]{0,2000}>")


def _escape(match: re.Match[str]) -> str:
    return _ESCAPES[match.group(0)]


class _Node:
    __slots__ = ("attrs", "children", "has_block", "parent", "tag")

    def __init__(self, tag: str, attrs: dict[str, str], parent: _Node | None) -> None:
        self.tag = tag
        self.attrs = attrs
        self.parent = parent
        self.children: list[_Node | str] = []
        self.has_block: bool | None = None

    def cls(self) -> set[str]:
        return set(self.attrs.get("class", "").lower().split())

    def text(self) -> str:
        """Concatenated raw text of this subtree."""
        parts: list[str] = []
        stack: list[_Node | str] = [self]
        while stack:
            item = stack.pop()
            if isinstance(item, str):
                parts.append(item)
            else:
                stack.extend(reversed(item.children))
        return "".join(parts)


class _TreeBuilder(HTMLParser):
    """Forgiving tree builder: implied end tags, stray end tags ignored, depth capped."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", {}, None)
        self.stack: list[_Node] = [self.root]

    @property
    def top(self) -> _Node:
        return self.stack[-1]

    def _pop_to(self, index: int) -> None:
        del self.stack[index:]

    def _close_scoped(self, targets: frozenset[str], stops: frozenset[str]) -> None:
        for i in range(len(self.stack) - 1, 0, -1):
            tag = self.stack[i].tag
            if tag in targets:
                self._pop_to(i)
                return
            if tag in stops:
                return

    def _implied_close(self, tag: str) -> None:
        if "head" in (n.tag for n in self.stack) and tag not in _HEAD_OK:
            self._close_scoped(frozenset({"head"}), frozenset())
        if tag in _P_CLOSERS:
            for i in range(len(self.stack) - 1, 0, -1):
                t = self.stack[i].tag
                if t == "p":
                    self._pop_to(i)
                    break
                if t not in _INLINE:
                    break
        if tag == "li":
            self._close_scoped(frozenset({"li"}), frozenset({"ul", "ol", "menu"}))
        elif tag in ("dt", "dd"):
            self._close_scoped(frozenset({"dt", "dd"}), frozenset({"dl"}))
        elif tag in ("td", "th"):
            self._close_scoped(frozenset({"td", "th"}), frozenset({"tr", "table"}))
        elif tag == "tr":
            self._close_scoped(frozenset({"tr"}), frozenset({"table"}))
        elif tag in _ROW_PARTS:
            self._close_scoped(_ROW_PARTS, frozenset({"table"}))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        self._implied_close(tag)
        node = _Node(tag, {k.lower(): (v or "") for k, v in attrs}, self.top)
        self.top.children.append(node)
        if tag not in _VOID and len(self.stack) < _MAX_DEPTH:
            self.stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                self._pop_to(i)
                return

    def handle_data(self, data: str) -> None:
        if data:
            self.top.children.append(data)


def _find(node: _Node, pred: Callable[[_Node], bool]) -> _Node | None:
    stack = deque([node])
    while stack:
        cur = stack.popleft()
        for child in cur.children:
            if isinstance(child, _Node):
                if pred(child):
                    return child
                stack.append(child)
    return None


def _content_root(root: _Node) -> _Node:
    for pred in (
        lambda n: n.tag == "main",
        lambda n: n.tag == "article",
        lambda n: n.attrs.get("role", "").lower() == "main",
        lambda n: n.tag == "body",
    ):
        found = _find(root, pred)
        if found is not None:
            return found
    return root


def _hidden(node: _Node) -> bool:
    a = node.attrs
    return (
        "hidden" in a
        or a.get("aria-hidden", "").lower() == "true"
        or bool(_HIDDEN_STYLE.search(a.get("style", "")))
    )


def _norm(text: str) -> str:
    return _WS.sub(" ", text.translate(_NBSP))


def _fence(body: str, info: str = "") -> str:
    longest = max((len(m) for m in re.findall(r"`+", body)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}{info}\n{body}\n{ticks}"


def _is_fence(block: str) -> bool:
    """Whether `block` is one fenced code block made by `_fence`."""
    m = re.match(r"(`{3,}|~{3,})", block)
    if m is None or "\n" not in block:
        return False
    last = block.rsplit("\n", 1)[-1]
    return last.startswith(m.group(1)) and set(last) == {m.group(1)[0]}


def _by_fence(parts: list[str]) -> list[tuple[bool, str]]:
    """Parts as (is_fence, text): fences alone, runs of other parts joined by a blank line."""
    groups: list[tuple[bool, str]] = []
    run: list[str] = []
    for part in parts:
        if _is_fence(part):
            if run:
                groups.append((False, "\n\n".join(run)))
                run = []
            groups.append((True, part))
        else:
            run.append(part)
    if run:
        groups.append((False, "\n\n".join(run)))
    return groups


def _wrap(text: str, mark: str) -> str:
    core = text.strip()
    if not core:
        return text
    lead = text[: len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()) :]
    return f"{lead}{mark}{core}{mark}{trail}"


def _inline_code(text: str) -> str:
    text = _norm(text).strip()
    if not text:
        return ""
    ticks = "`" * (max((len(m) for m in re.findall(r"`+", text)), default=0) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{ticks}{pad}{text}{pad}{ticks}"


class _Renderer:
    def __init__(self, base_url: str | None) -> None:
        self.base_url = base_url
        self._cells = 0  # table cells being rendered: a table inside one is flattened to text

    # ---- classification -------------------------------------------------

    def _has_block(self, node: _Node) -> bool:
        if node.has_block is None:
            node.has_block = any(
                isinstance(c, _Node) and (c.tag in _BLOCKISH or self._has_block(c))
                for c in node.children
            )
        return node.has_block

    def _is_inline(self, node: _Node) -> bool:
        return node.tag in _INLINE and not self._has_block(node)

    def _is_cite(self, node: _Node) -> bool:
        if node.tag == "a" and "cite" in node.cls():
            return True
        if node.tag == "sup":
            if node.cls() & {"footnote", "footnote-ref", "fn", "reference", "citation", "cite"}:
                return True
            if node.attrs.get("id", "").lower().startswith(("fnref", "cite_ref")):
                return True
            return (
                _find(node, lambda n: n.tag == "a" and n.attrs.get("href", "").startswith("#"))
                is not None
            )
        return False

    # ---- urls -----------------------------------------------------------

    def _abs(self, url: str) -> str:
        url = url.strip()
        if self.base_url and url and not url.startswith(("data:", "#")):
            try:
                return urljoin(self.base_url, url)
            except ValueError:
                return url
        return url

    # ---- inline ---------------------------------------------------------

    def inline(self, nodes: list[_Node | str]) -> str:
        out: list[str] = []
        prev: _Node | None = None
        for child in nodes:
            if isinstance(child, str):
                out.append(_ENTITY_OR_ANGLE.sub(_escape, _norm(child)))
                prev = None
                continue
            piece = self._inline_node(child)
            if piece and prev is not None and prev.tag in ("b", "strong") and child.tag == "span":
                # <b>Label</b><span>detail</span> has no whitespace between them in the source.
                tail = "".join(out).rstrip().rstrip("*")
                out.append(" " if tail.endswith(("?", "!", ".", ":", ";")) else " — ")
            if piece:
                out.append(piece)
            prev = child if piece else prev
        return "".join(out)

    def _inline_node(self, node: _Node) -> str:
        tag = node.tag
        if tag in _SKIP or _hidden(node) or self._is_cite(node):
            return ""
        if tag in ("script", "style"):
            return ""
        if tag == "br":
            return "\n"
        if tag == "img":
            return self._image(node)
        if node.tag not in _INLINE or self._has_block(node):
            # A block that sits inside inline flow: flatten it to a phrase.
            return " " + " ".join(self.blocks([node])).replace("\n", " ") + " "
        inner = self.inline(node.children)
        if tag in ("b", "strong"):
            return _wrap(inner, "**") if inner.strip() else inner
        if tag in ("em", "i", "cite", "dfn"):
            return _wrap(inner, "*") if inner.strip() else inner
        if tag in _CODE_TAGS:
            return _inline_code(node.text())
        if tag == "a":
            href = node.attrs.get("href", "").strip()
            label = inner.strip()
            if not label:
                return ""
            has_scheme = re.match(r"[a-z][a-z0-9+.-]*:", href, re.I) is not None
            web = href.lower().startswith(("http://", "https://"))
            if not href or href.startswith("#") or (has_scheme and not web):
                return inner
            lead = inner[: len(inner) - len(inner.lstrip())]
            trail = inner[len(inner.rstrip()) :]
            return f"{lead}[{label}]({self._abs(href).replace(' ', '%20')}){trail}"
        if tag == "sup":
            return f"^{inner.strip()}" if inner.strip() else ""
        if tag == "q":
            return f"«{inner.strip()}»"
        return inner

    def _image(self, node: _Node) -> str:
        alt = _norm(node.attrs.get("alt", "")).strip().replace("[", "(").replace("]", ")")
        src = node.attrs.get("src", "").strip()
        lazy = node.attrs.get("data-src", "").strip()
        if (not src or src.startswith("data:")) and lazy:
            src = lazy
        if not src or src.startswith("data:"):
            return f"![{alt}]()" if alt else ""
        return f"![{alt}]({self._abs(src).replace(' ', '%20').replace(')', '%29')})"

    # ---- blocks ---------------------------------------------------------

    def blocks(self, nodes: list[_Node | str]) -> list[str]:
        out: list[str] = []
        buf: list[_Node | str] = []

        def flush() -> None:
            if buf:
                text = re.sub(r"[ \t]{2,}", " ", self.inline(buf))
                buf.clear()
                for para in re.split(r"\n{2,}", text):
                    para = "\n".join(line.strip() for line in para.strip().split("\n")).strip()
                    if para:
                        out.append(para)

        for child in nodes:
            if isinstance(child, str) or (self._is_inline(child) and not _hidden(child)):
                buf.append(child)
                continue
            if child.tag in _SKIP or _hidden(child):
                continue
            flush()
            out.extend(self._block(child))
        flush()
        return out

    def _block(self, node: _Node) -> list[str]:
        tag = node.tag
        if tag == "script":
            return self._script(node)
        if tag in ("style", "head"):
            return []
        if "mermaid" in node.cls() and tag in ("pre", "div"):
            body = node.text().strip("\n")
            return [_fence(body, "mermaid")] if body.strip() else []
        if tag == "svg":
            return self._svg(node)
        if tag == "pre":
            return self._pre(node)
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            text = self.inline(node.children).replace("\n", " ").strip()
            return [f"{'#' * int(tag[1])} {text}"] if text else []
        if tag in ("ul", "ol", "menu"):
            return self._list(node)
        if tag == "blockquote":
            out: list[str] = []
            for is_fence, text in _by_fence(self.blocks(node.children)):
                if is_fence:  # a fence stays at column 0 so the speech stage still sees it
                    out.append(text)
                elif text:
                    out.append("\n".join(f"> {line}" if line else ">" for line in text.split("\n")))
            return out
        if tag == "table":
            return self._table(node)
        if tag == "details":
            return self._details(node)
        if tag == "figure":
            return self._figure(node)
        if tag == "dl":
            return self._dl(node)
        if tag in ("dt", "summary"):
            text = self.inline(node.children).replace("\n", " ").strip()
            return [f"**{text}**"] if text else []
        if tag == "hr":
            return []
        return self.blocks(node.children)

    def _script(self, node: _Node) -> list[str]:
        kind = node.attrs.get("type", "").strip().lower()
        body = node.text().strip("\n")
        if not body.strip():
            return []
        if kind in ("text/x-mermaid", "text/mermaid"):
            return [_fence(body, "mermaid")]
        if kind == "application/json" and "chart" in node.cls():
            return [_fence(body, "chart")]
        if _CONSENT_CLASS in node.cls() or _CONSENT_ATTRS & node.attrs.keys():
            return []  # a consent manager's blocked tracker, not content
        if kind == "text/plain" and _BOOK_ATTRS & node.attrs.keys():
            lang = re.sub(r"[^\w+#.-]", "", node.attrs.get("data-lang", "")) or "text"
            title = node.attrs.get("data-title", "").replace('"', "'").replace("\n", " ").strip()
            info = f'{lang} title="{title}"' if title else lang
            return [_fence(textwrap.dedent(body).rstrip(), info)]
        return []

    def _pre(self, node: _Node) -> list[str]:
        lang = ""
        code = _find(node, lambda n: n.tag == "code")
        for holder in (code, node):
            if holder is None:
                continue
            for c in holder.attrs.get("class", "").split():
                low = c.lower()
                if low.startswith(("language-", "lang-")):
                    lang = re.sub(r"[^\w+#.-]", "", c.split("-", 1)[1])
                    break
            if lang:
                break
        parts: list[str] = []
        stack: list[_Node | str] = [node]
        while stack:
            item = stack.pop()
            if isinstance(item, str):
                parts.append(item)
            elif item.tag == "br":
                parts.append("\n")
            elif not _hidden(item):
                stack.extend(reversed(item.children))
        body = "".join(parts).replace("\r\n", "\n").strip("\n").rstrip()
        return [_fence(body, lang)] if body.strip() else []

    def _svg(self, node: _Node) -> list[str]:
        lines: list[str] = []

        def add(label: str, value: str) -> None:
            value = _norm(value).strip()
            if value and (not lines or lines[-1].split(": ", 1)[-1] != value):
                lines.append(f"{label}: {value}" if label else value)

        add("label", node.attrs.get("aria-label", ""))
        stack: list[_Node | str] = list(reversed(node.children))
        while stack:
            item = stack.pop()
            if isinstance(item, str):
                continue
            if item.tag in ("title", "desc") and item.parent is node:
                add(item.tag, item.text())
            elif item.tag in ("text", "tspan") and (
                item.parent is None or item.parent.tag != "text"
            ):
                add("", item.text())
            else:
                stack.extend(reversed(item.children))
        return [_fence("\n".join(lines), "svg")] if lines else []

    def _list(self, node: _Node) -> list[str]:
        ordered = node.tag == "ol"
        try:
            number = int(node.attrs.get("start", "1"))
        except ValueError:
            number = 1
        out: list[str] = []
        chunk: list[str] = []  # list lines since the last fence

        def flush() -> None:
            if chunk:
                out.append("\n".join(chunk))
                chunk.clear()

        for child in node.children:
            if not isinstance(child, _Node) or child.tag != "li" or _hidden(child):
                continue
            parts = self.blocks(child.children)
            if not parts:
                continue
            marker = f"{number}. " if ordered else "- "
            pad = " " * len(marker)
            number += 1
            lead = True
            for is_fence, text in _by_fence(parts):
                if is_fence:  # a fence stays at column 0 so the speech stage still sees it
                    flush()
                    out.append(text)
                    lead = False
                elif lead:
                    first, *rest = text.split("\n")
                    chunk.append(marker + first)
                    chunk.extend((pad + line) if line else "" for line in rest)
                    lead = False
                else:
                    flush()
                    out.append(text)
        flush()
        return out

    def _details(self, node: _Node) -> list[str]:
        out: list[str] = []
        rest: list[_Node | str] = []
        for child in node.children:
            if isinstance(child, _Node) and child.tag == "summary":
                out.extend(self._block(child))
            else:
                rest.append(child)
        out.extend(self.blocks(rest))
        return out

    def _figure(self, node: _Node) -> list[str]:
        body: list[_Node | str] = []
        captions: list[_Node | str] = []
        for child in node.children:
            if isinstance(child, _Node) and child.tag == "figcaption":
                captions.append(child)
            else:
                body.append(child)
        return [*self.blocks(body), *self.blocks(captions)]

    def _dl(self, node: _Node) -> list[str]:
        return self.blocks(node.children)

    def _cell(self, node: _Node) -> str:
        outer = self._cells == 0
        self._cells += 1
        try:
            text = " ".join(self.blocks(node.children)).replace("\n", " ")
        finally:
            self._cells -= 1
        text = _WS.sub(" ", text).strip()
        return text.replace("|", "\\|") if outer else text

    def _table(self, node: _Node) -> list[str]:
        rows: list[tuple[bool, list[_Node]]] = []  # (in thead, cells)
        captions: list[str] = []
        order: list[tuple[_Node, bool]] = []

        def walk(cur: _Node, in_head: bool) -> None:
            for child in cur.children:
                if not isinstance(child, _Node) or child.tag == "table":
                    continue
                if child.tag == "caption":
                    captions.append(self.inline(child.children).replace("\n", " ").strip())
                elif child.tag == "tr":
                    order.append((child, in_head))
                elif child.tag in _ROW_PARTS:
                    walk(child, child.tag == "thead")

        walk(node, False)
        caption = captions[0] if captions else ""
        for tr, in_head in order:
            cells = [c for c in tr.children if isinstance(c, _Node) and c.tag in ("td", "th")]
            if cells:
                rows.append((in_head, cells))
        if not rows:
            return [caption] if caption else []
        width = max(len(cells) for _, cells in rows)
        grid = [[self._cell(c) for c in cells] + [""] * (width - len(cells)) for _, cells in rows]
        first_in_head, first_cells = rows[0]
        if self._cells:
            flat = "; ".join(", ".join(c for c in row if c) for row in grid)
            return [flat] if flat.strip(" ;,") else []
        has_header = first_in_head or all(c.tag == "th" for c in first_cells)
        if not has_header:
            grid.insert(0, [""] * width)
        lines = ["| " + " | ".join(grid[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
        lines += ["| " + " | ".join(r) + " |" for r in grid[1:]]
        table = "\n".join(lines)
        return [caption, table] if caption else [table]


def _fallback(html: str) -> str:
    text = re.sub(r"(?is)<(script|style|head)\b.*?</\1\s*>", " ", html)
    text = _TAG_RE.sub(" ", text)
    import html as html_lib

    return _WS.sub(" ", html_lib.unescape(text)).strip()


def html_to_markdown(html: str, base_url: str | None = None) -> str:
    """Markdown for the readable content of `html`; never raises on malformed markup."""
    try:
        builder = _TreeBuilder()
        builder.feed(html)
        builder.close()
        root = _content_root(builder.root)
        blocks = _Renderer(base_url).blocks(root.children)
        text = "\n\n".join(b for b in blocks if b.strip())
        return re.sub(r"\n{3,}", "\n\n", text).strip()
    except Exception:
        log.exception("html_to_markdown failed, stripping tags instead")
        return _fallback(html)
