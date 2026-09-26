"""providers — the concrete fetches and extractions of stage 3 (`docs/plans/jev/3-sources.md`).

Three functions and their parsers, not a plugin system: `fetch` one explicit
URL, `arxiv` search or look up papers, and `pages` of a PDF. Nothing here
writes anything or decides what is kept; `main.knowledge` composes them with
the records in `sources`.

The network boundary. HTTPS only. A URL carrying credentials is refused. The
host is resolved once, every address it resolves to must be public, and the
connection goes to that vetted address — never to a second resolution the
check did not see — with the certificate still checked against the host name.
Redirects are followed by hand, each one checked the same way. One deadline
bounds the whole fetch, every read included, and the body stops at its size
ceiling. Proxies from the environment are not used: `http.client` takes none.

A failure is `FetchError` with its `reason`, which a record keeps as the
visible cause: scheme, credentials, address, dns, redirect, redirects,
too_large, timeout, content_type, network, http_<status>, no_pdf_extractor,
extraction_failed, arxiv_error, invalid_feed.
"""

from __future__ import annotations

import html.parser
import http.client
import io
import ipaddress
import re
import socket
import ssl
import threading
import time
from urllib.parse import urlencode, urljoin, urlsplit
from xml.etree import ElementTree

# What `fetch` accepts. Tests widen it to reach a local plain-HTTP server.
SCHEMES = ("https",)
PORTS = {"https": 443, "http": 80}
MAX_BYTES = 5_000_000
PDF_BYTES = 25_000_000
SECONDS = 20.0
REDIRECTS = 5
DOCUMENT_TYPES = ("text/html", "application/xhtml+xml", "text/plain", "text/markdown", "application/pdf")
AGENT = "wiki-agent/1 (explicit research ingestion)"

ARXIV = "https://export.arxiv.org/api/query"
# The arXiv API user manual asks for no more than one request every three seconds.
ARXIV_GAP = 3.0
ARXIV_MAX = 20
ATOM = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
FEED_TYPES = ("application/atom+xml", "application/xml", "text/xml")


class FetchError(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def public(address: str) -> bool:
    """A globally routable unicast address. Loopback, private, link-local,
    shared, reserved and multicast are not — nor an IPv4 one dressed as IPv6."""

    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def checked(url: str) -> tuple[str, str, int, str]:
    """`(scheme, host, port, request target)` of an acceptable URL, else `FetchError`."""

    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise FetchError("address") from None
    if parts.scheme not in SCHEMES:
        raise FetchError("scheme", parts.scheme)
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise FetchError("credentials")
    if not parts.hostname:
        raise FetchError("address")
    target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    return parts.scheme, parts.hostname, port or PORTS[parts.scheme], target


def vetted(host: str, port: int) -> str:
    """The address to connect to. Every address the host resolves to must be
    public: one private answer among public ones refuses the host."""

    try:
        found = list(dict.fromkeys(info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
    except (OSError, UnicodeError):
        raise FetchError("dns") from None
    if not found or not all(public(address) for address in found):
        raise FetchError("address", host)
    return found[0]


class Plain(http.client.HTTPConnection):
    """A connection to the vetted address, the host name kept for `Host`."""

    def __init__(self, host: str, port: int, address: str, timeout: float):
        super().__init__(host, port, timeout=timeout)
        self.address = address

    def connect(self) -> None:
        self.sock = self.raw = socket.create_connection((self.address, self.port), self.timeout)


class Pinned(http.client.HTTPSConnection):
    """TLS to the vetted address, with SNI and the certificate checked against the host name."""

    def __init__(self, host: str, port: int, address: str, timeout: float):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self) -> None:
        sock = socket.create_connection((self.address, self.port), self.timeout)
        self.sock = self.raw = self._context.wrap_socket(sock, server_hostname=self.host)


def fetch(url: str, limit: int = MAX_BYTES, seconds: float = SECONDS,
          types: tuple[str, ...] = DOCUMENT_TYPES) -> dict:
    """`{url, content_type, charset, body}` of one explicit URL, `url` being
    where the redirects ended. `FetchError` for anything else."""

    deadline = time.monotonic() + seconds
    for _hop in range(REDIRECTS + 1):
        scheme, host, port, target = checked(url)
        address = vetted(host, port)
        left = deadline - time.monotonic()
        if left <= 0:
            raise FetchError("timeout")
        conn = (Pinned if scheme == "https" else Plain)(host, port, address, left)
        try:
            conn.request("GET", target, headers={"User-Agent": AGENT, "Accept-Encoding": "identity"})
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                if not location:
                    raise FetchError("redirect")
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise FetchError(f"http_{response.status}")
            kind = (response.getheader("Content-Type") or "").split(";")[0].strip().lower()
            if kind not in types:
                raise FetchError("content_type", kind)
            declared = response.getheader("Content-Length") or ""
            if declared.isdigit() and int(declared) > limit:
                raise FetchError("too_large")
            body = bytearray()
            while len(body) <= limit:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise FetchError("timeout")
                # Each read gets only what is left, so a trickling peer cannot
                # stretch the deadline. `conn.raw`: a closing response has taken `conn.sock`.
                conn.raw.settimeout(left)
                piece = response.read1(65536)
                if not piece:
                    break
                body += piece
            if len(body) > limit:
                raise FetchError("too_large")
            return {"url": url, "content_type": kind, "charset": response.headers.get_content_charset(),
                    "body": bytes(body)}
        except FetchError:
            raise
        except (TimeoutError, socket.timeout):
            raise FetchError("timeout") from None
        except (OSError, http.client.HTTPException, ValueError):
            raise FetchError("network") from None
        finally:
            conn.close()
    raise FetchError("redirects")


def decoded(got: dict) -> str:
    """A fetched text body as text, newlines as `\\n`."""

    text = got["body"].decode(got.get("charset") or "utf-8", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


class _Text(html.parser.HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg", "head"}
    BLOCK = {"p", "div", "br", "li", "tr", "section", "article", "pre", "blockquote", "table", "ul", "ol",
             "header", "footer", "main", "nav", "dd", "dt", "figcaption"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skipping = 0
        self.title: list[str] = []
        self.in_title = False

    def handle_starttag(self, tag, _attrs):
        if tag == "title":
            self.in_title = True
        elif tag in self.SKIP:
            self.skipping += 1
        elif re.fullmatch(r"h[1-6]", tag):
            # Markdown headings, so the chunker cuts the page where the page is cut.
            self.out.append("\n\n" + "#" * min(int(tag[1]), 3) + " ")
        elif tag in self.BLOCK:
            self.out.append("\n\n" if tag != "br" else "\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        elif tag in self.SKIP:
            self.skipping = max(0, self.skipping - 1)
        elif re.fullmatch(r"h[1-6]", tag) or tag in self.BLOCK:
            self.out.append("\n\n")

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)
        elif not self.skipping:
            self.out.append(data)


def html_text(page: str) -> tuple[str, str]:
    """`(title, text)` of an HTML page: its visible text in paragraphs, headings as Markdown."""

    parser = _Text()
    parser.feed(page)
    parser.close()
    lines = [" ".join(line.split()) for line in "".join(parser.out).split("\n")]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return " ".join("".join(parser.title).split()), text


def pages(data: bytes) -> list[str]:
    """The text of each page of a PDF, with the extractor already installed.
    None installed, or one that fails, is a `FetchError` — never an empty text."""

    try:
        from pypdf import PdfReader
    except ImportError:
        raise FetchError("no_pdf_extractor") from None
    try:
        reader = PdfReader(io.BytesIO(data))
        return [(page.extract_text() or "").replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n")
                for page in reader.pages]
    except Exception as error:  # noqa: BLE001 — a damaged or encrypted PDF, however it fails
        raise FetchError("extraction_failed", type(error).__name__) from None


_arxiv_lock = threading.Lock()
_arxiv_last = [0.0]


def arxiv(query: str | None = None, ids: list[str] | None = None, n: int = 5, seconds: float = SECONDS) -> list[dict]:
    """Papers from the arXiv API: a search, or a lookup by identifier (the
    latest version unless one is named), in the API's order. Each is
    `{arxiv_id, version, title, authors, published, updated, summary, abs_url, pdf_url}`.

    Plain words search every field together; a query already in the API's
    syntax (`ti:`, `au:`, `AND` ...) goes as it is.
    """

    if not query and not ids:
        raise ValueError("a query or identifiers")
    params: dict[str, object] = {"start": 0, "max_results": max(1, min(n, ARXIV_MAX))}
    if query:
        words = re.findall(r"[\w.-]+", query)
        params["search_query"] = query if ":" in query else " AND ".join(f"all:{w}" for w in words)
    if ids:
        params["id_list"] = ",".join(ids)
    with _arxiv_lock:
        wait = _arxiv_last[0] + ARXIV_GAP - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            got = fetch(f"{ARXIV}?{urlencode(params)}", MAX_BYTES, seconds, FEED_TYPES)
        finally:
            _arxiv_last[0] = time.monotonic()
    return feed(got["body"])


def feed(body: bytes) -> list[dict]:
    """The entries of an arXiv Atom feed."""

    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        raise FetchError("invalid_feed") from None
    out = []
    for entry in root.findall("atom:entry", ATOM):
        ident = (entry.findtext("atom:id", "", ATOM) or "").strip()
        summary = (entry.findtext("atom:summary", "", ATOM) or "").strip()
        if "/api/errors" in ident:
            raise FetchError("arxiv_error", " ".join(summary.split())[:200])
        match = re.search(r"/abs/(.+?)(v\d+)?$", ident)
        if not match:
            continue
        base, version = match.group(1), match.group(2) or ""
        pdf = next((link.get("href") for link in entry.findall("atom:link", ATOM)
                    if link.get("title") == "pdf"), None)
        out.append({"arxiv_id": base, "version": version,
                    "title": " ".join((entry.findtext("atom:title", "", ATOM) or "").split()),
                    "authors": [" ".join((a.findtext("atom:name", "", ATOM) or "").split())
                                for a in entry.findall("atom:author", ATOM)],
                    "published": entry.findtext("atom:published", None, ATOM),
                    "updated": entry.findtext("atom:updated", None, ATOM),
                    "summary": summary, "abs_url": f"https://arxiv.org/abs/{base}{version}",
                    "pdf_url": (pdf or f"https://arxiv.org/pdf/{base}{version}").replace("http://", "https://")})
    return out
