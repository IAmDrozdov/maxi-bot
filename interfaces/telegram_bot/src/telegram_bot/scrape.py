"""Fetch a web page or a GitHub file and extract its readable content as Markdown.

Interface-layer utility (ADR-005 thin adapter): turns a link into the same
Markdown `.md` payload the document flow already understands, so a URL reuses
the agent-pick → estimate → enqueue path with no workflow changes. Keeps the
scraping dependency out of `shared/` and the workflows. Code, diagrams and pictures
are kept as fenced blocks and image links for the speech stage (ADR-023).
"""

from __future__ import annotations

import ipaddress
import re
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import unquote, urljoin, urlsplit

from shared.doc import decode_html, html_to_markdown
from shared.obs import get_logger

log = get_logger(__name__)

_ALLOWED_SCHEMES = {"http", "https"}
_REDIRECT_CODES = {301, 302, 303, 307, 308}
_MAX_REDIRECTS = 2
_PROBE_TIMEOUT_S = 10
_USER_AGENT = "maxi-bot-bot/1.0"
_FILE_MAX_BYTES = 10 * 1024 * 1024
_FILE_TIMEOUT_S = 20
_FILE_DEADLINE_S = 40
_RAW_HOST = "raw.githubusercontent.com"
_GITHUB_HOSTS = {"github.com", "www.github.com"}
_HTML_FILE = {".html", ".htm"}
_MARKDOWN_FILE = {".md", ".markdown"}
_MIN_BOOK_CHARS = 200


class ScrapeError(Exception):
    """User-facing failure while fetching or extracting a link."""


@dataclass(frozen=True)
class ScrapedArticle:
    title: str
    markdown: str
    url: str
    suffix: str = ".md"  # ".txt" for a GitHub text file, which is read as it is


_SLUG_RE = re.compile(r"[^0-9A-Za-z]+")


def _slugify(value: str, fallback: str = "article") -> str:
    slug = _SLUG_RE.sub("-", value.strip()).strip("-").lower()[:60].strip("-")
    return slug or fallback


def _is_public_address(host: str) -> bool:
    """False for anything that resolves onto our own network or the metadata service."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    if not infos:
        return False
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global or address.is_multicast:
            return False
    return True


def assert_fetchable(url: str) -> None:
    """Guard the one place a user gets to choose what this host connects to.

    The bot shares a Docker network with the Mini App and can reach the
    cloud metadata service, so an unchecked URL turns the scraper into a reader
    of internal endpoints. Raises ScrapeError when the target is not public.
    """
    parts = urlsplit(url)
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise ScrapeError("Only http:// and https:// links are supported.")
    if not parts.hostname:
        raise ScrapeError("That link has no host in it.")
    # `urlsplit` and the urllib3 stack trafilatura fetches through can disagree on
    # which host an authority like "169.254.169.254\@1.1.1.1" names — no legitimate
    # scrape target needs userinfo or a backslash, so refuse both outright, then
    # cross-check the parser trafilatura will actually use before trusting this one.
    if "@" in parts.netloc or "\\" in url:
        raise ScrapeError("That link isn't in a supported form.")
    import urllib3.util

    fetch_host = urllib3.util.parse_url(url).host
    if (fetch_host or "").lower() != parts.hostname.lower():
        raise ScrapeError("That link isn't in a supported form.")
    if not _is_public_address(parts.hostname):
        raise ScrapeError("That link points at a private address, so I won't fetch it.")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _resolve_redirects(url: str) -> str:
    """Walk redirects without reading bodies, vetting each hop; returns the final URL."""
    opener = urllib.request.build_opener(_NoRedirect())
    for _ in range(_MAX_REDIRECTS + 1):
        request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        try:
            with opener.open(request, timeout=_PROBE_TIMEOUT_S):
                return url
        except urllib.error.HTTPError as exc:
            location = exc.headers.get("Location") if exc.code in _REDIRECT_CODES else None
            if not location:
                return url  # 4xx/5xx: let the real fetch report it
            url = urljoin(url, location)
            assert_fetchable(url)
        except (urllib.error.URLError, OSError, ValueError):
            return url
    raise ScrapeError("That link redirects too many times.")


def github_raw_url(url: str) -> str | None:
    """The raw-file URL for a github.com blob/raw link, `url` itself for raw.githubusercontent.com, else None."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host == _RAW_HOST:
        return parts._replace(fragment="", query="").geturl()
    if host not in _GITHUB_HOSTS:
        return None
    segments = [seg for seg in parts.path.split("/") if seg]
    # owner / repo / blob|raw / ref / path...; a ref with a slash is ambiguous, so the first segment wins.
    if len(segments) < 5 or segments[2] not in ("blob", "raw"):
        return None
    owner, repo, _, ref, *path = segments
    return f"https://{_RAW_HOST}/{owner}/{repo}/{ref}/{'/'.join(path)}"


def _fetch_file(url: str) -> tuple[bytes, str]:
    """Bytes of a public file (size-capped, redirects vetted hop by hop) and its final URL."""
    assert_fetchable(url)
    opener = urllib.request.build_opener(_NoRedirect())
    for _ in range(_MAX_REDIRECTS + 1):
        request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        try:
            with opener.open(request, timeout=_FILE_TIMEOUT_S) as response:
                return _read_capped(response), url
        except urllib.error.HTTPError as exc:
            location = exc.headers.get("Location") if exc.code in _REDIRECT_CODES else None
            if location:
                url = urljoin(url, location)
                assert_fetchable(url)
                continue
            if exc.code in (401, 403, 404):
                raise ScrapeError(
                    "That file isn't there, or it's private — I can only read public files."
                ) from exc
            raise ScrapeError(f"The server answered {exc.code} for that file.") from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise ScrapeError("Couldn't fetch that file — the host may be offline.") from exc
    raise ScrapeError("That link redirects too many times.")


def _read_capped(response) -> bytes:
    deadline = time.monotonic() + _FILE_DEADLINE_S
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = response.read1(64 * 1024)  # one recv, so the deadline is checked per recv
        if not chunk:
            return b"".join(chunks)
        size += len(chunk)
        if size > _FILE_MAX_BYTES:
            raise ScrapeError("That file is over 10 MB, which is too big to read here.")
        if time.monotonic() > deadline:
            raise ScrapeError("That file took too long to download.")
        chunks.append(chunk)


_MD_LINK_RE = re.compile(r"(!?\[[^\]]*\]\()\s*([^)\s]+)((?:\s+\"[^\"]*\")?\))")
_FENCE_LINE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")


def _absolutize_markdown(markdown: str, base_url: str) -> str:
    """Relative link and image targets made absolute, outside code fences."""

    def fix(match: re.Match[str]) -> str:
        target = match.group(2)
        if target.startswith(("#", "data:", "mailto:", "tel:")) or re.match(
            r"[a-z][a-z0-9+.-]*:", target, re.I
        ):
            return match.group(0)
        return f"{match.group(1)}{urljoin(base_url, target)}{match.group(3)}"

    out: list[str] = []
    fence = ""
    for line in markdown.split("\n"):
        m = _FENCE_LINE_RE.match(line)
        if fence:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                fence = ""
            out.append(line)
        elif m:
            fence = m.group(1)
            out.append(line)
        else:
            out.append(_MD_LINK_RE.sub(fix, line))
    return "\n".join(out)


def _first_h1(markdown: str) -> str:
    fence = ""
    for line in markdown.split("\n"):
        m = _FENCE_LINE_RE.match(line)
        if fence:
            if m and m.group(1)[0] == fence[0]:
                fence = ""
        elif m:
            fence = m.group(1)
        elif line.startswith("# "):
            return line[2:].strip()
    return ""


def _scrape_github_file(raw_url: str, shown_url: str) -> ScrapedArticle:
    """A public file from GitHub: HTML and Markdown are converted, text is read as it is."""
    name = unquote(PurePosixPath(urlsplit(raw_url).path).name)
    suffix = PurePosixPath(name.lower()).suffix
    if suffix not in _HTML_FILE | _MARKDOWN_FILE | {".txt"}:
        raise ScrapeError(
            "I can read .html, .md and .txt files from GitHub. "
            "Send that file to me as a document instead."
        )
    data, final_url = _fetch_file(raw_url)
    if suffix in _HTML_FILE:
        body = html_to_markdown(decode_html(data), base_url=final_url)
    else:
        body = data.decode("utf-8", errors="replace").strip()
        if suffix in _MARKDOWN_FILE:
            body = _absolutize_markdown(body, final_url)
    if not body.strip():
        raise ScrapeError("That file has no readable text in it.")
    title = _first_h1(body) or PurePosixPath(name).stem or name
    log.info("scrape: github file %s -> %d chars (title=%r)", raw_url, len(body), title)
    return ScrapedArticle(
        title=title,
        markdown=body.strip(),
        url=shown_url,
        suffix=".txt" if suffix == ".txt" else ".md",
    )


# A script open tag, bounded and free of `<`, so a never-closed tag cannot backtrack (linear scan).
_SCRIPT_OPEN_RE = re.compile(r"<script\b[^<>]{0,4000}>", re.IGNORECASE)
_TYPE_ATTR_RE = re.compile(r"""\btype\s*=\s*["']?([\w/+.-]*)""", re.IGNORECASE)
_BOOK_ATTR_RE = re.compile(r"\bdata-(?:lang|title)\b", re.IGNORECASE)


def _has_book_script(html: str) -> bool:
    """Only the book convention: Mermaid, or a text/plain script naming its language or title.

    A bare text/plain script is a consent manager's blocked tracker.
    """
    for match in _SCRIPT_OPEN_RE.finditer(html):
        tag = match.group(0)
        kind = _TYPE_ATTR_RE.search(tag)
        kind_value = kind.group(1).lower() if kind else ""
        if kind_value in ("text/mermaid", "text/x-mermaid"):
            return True
        if kind_value == "text/plain" and _BOOK_ATTR_RE.search(tag):
            return True
    return False


def scrape_url(url: str) -> ScrapedArticle:
    """Blocking: download `url` and extract its main content as Markdown.

    Raises ScrapeError with a message that is safe to show the user.
    """
    import trafilatura

    assert_fetchable(url)
    raw_url = github_raw_url(url)
    if raw_url is not None:
        return _scrape_github_file(raw_url, url)
    url = _resolve_redirects(url)
    response = trafilatura.fetch_response(url, decode=True)
    if response is None or not response.html:
        raise ScrapeError(
            "Couldn't fetch that link — it may be offline, private, or blocking bots."
        )
    # Redirects were vetted hop by hop above; this catches a server that answers
    # differently the second time. `response.url` is often just a path, so resolve
    # it against the request URL before comparing hosts.
    final_url = urljoin(url, response.url or "")
    if urlsplit(final_url).hostname != urlsplit(url).hostname:
        assert_fetchable(final_url)
    downloaded = response.html

    # Book-style pages keep code, diagrams and charts in typed <script> tags, which
    # trafilatura drops; those go through the shared HTML converter instead.
    if _has_book_script(downloaded):
        book = html_to_markdown(downloaded, base_url=final_url)
        if len(book) >= _MIN_BOOK_CHARS:
            title = _first_h1(book)
            log.info("scrape: %s -> %d chars via html (title=%r)", url, len(book), title)
            return ScrapedArticle(title=title, markdown=book, url=url)

    markdown = trafilatura.extract(
        downloaded,
        output_format="markdown",
        include_comments=False,
        include_tables=True,
        include_images=True,
        favor_precision=True,
    )
    if not markdown or not markdown.strip():
        raise ScrapeError("Couldn't find readable article text on that page.")
    markdown = _absolutize_markdown(markdown, final_url)

    title = ""
    try:
        meta = trafilatura.extract_metadata(downloaded)
        if meta and meta.title:
            title = meta.title.strip()
    except Exception:
        log.exception("scrape: metadata extraction failed")

    body = markdown.strip()
    # Prepend the title as an H1 when the extraction didn't already lead with one,
    # so chapter splitting and the delivered file both carry a heading.
    if title and not body.lstrip().startswith("#"):
        body = f"# {title}\n\n{body}"

    log.info("scrape: %s -> %d chars (title=%r)", url, len(body), title)
    return ScrapedArticle(title=title, markdown=body, url=url)


def filename_for(article: ScrapedArticle) -> str:
    """A filename derived from the article title (URL as fallback): `.md`, or `.txt` for plain text."""
    return f"{_slugify(article.title or article.url)}{article.suffix}"
