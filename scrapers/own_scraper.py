"""Your own site scraper: requests + BeautifulSoup. No paid service, no API key.

fetch_doc(url) returns the same three things Firecrawl does, so the rest of the pipeline is unchanged:
    {"markdown": the page's MAIN content as simple text, "raw_html": the full page (for footer badges),
     "links": every link on the page, "via": "own", "needs_js": True if the page looks empty without JavaScript}

How it behaves (like a polite visitor):
  * It checks robots.txt first and never fetches a page the site asks robots not to fetch.
  * It waits at least DELAY_SECONDS between requests to the same site.
  * It refuses to fetch anything that is not a public website (no localhost or private network addresses).
  * It follows at most 5 redirects, reads at most 2 MB, and only accepts HTML pages.
How it keeps only the useful text (the heuristics from class):
  * It deletes scripts, menus, headers, footers, side bars, forms and cookie/newsletter/pop-up boxes.
  * It reads the <main> (or <article>) part when there is one, otherwise what is left of the page.
  * It writes headings as "# Heading" and list items as "- item", in page order, so the key facts
    that sit near the top of a page stay near the top.
Everything that touches the network is in _http_get(), so tests replace that one function.
"""
import ipaddress
import os
import re
import socket
import threading
import time
from urllib import robotparser
from urllib.parse import urldefrag, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, NavigableString  # (comments are a NavigableString subclass: see _own_text)

USER_AGENT = os.getenv("SCRAPER_USER_AGENT") or "outbound-research-bot/1.0 (company research prototype)"
TIMEOUT_SECONDS = 20
MAX_BYTES = 2_000_000          # never read more than 2 MB of one page
MAX_REDIRECTS = 5
DELAY_SECONDS = 1.0            # at most one request per second to the same site
MAX_CRAWL_DELAY = 10.0         # even if robots.txt asks for longer
MAX_LINKS = 300
MAX_TEXT_CHARS = 60_000
THIN_TEXT_CHARS = 300          # less text than this, plus lots of scripts, means "needs JavaScript"
HERO_MIN_CHARS = 120           # a header with at least this much text (after menus are removed) is kept
FALLBACK_STATUS = {401, 403, 429, 503}  # bot-blocking answers: worth trying Firecrawl instead

_sleep = time.sleep            # tests replace this
_lock = threading.Lock()
_last_request = {}             # host -> when we last asked it for something
_robots = {}                   # origin -> RobotFileParser (or None when there is no robots.txt)
_public_cache = {}             # host -> True/False


class OwnScrapeError(Exception):
    """A page could not be scraped. `status_code` mimics HTTP so the retry logic can read it;
    `fallback_ok` says whether trying a different scraper (Firecrawl) could help."""

    def __init__(self, message, status_code=None, fallback_ok=False):
        super().__init__(message)
        self.status_code = status_code
        self.fallback_ok = fallback_ok


# ---------------------------------------------------------------- safety and politeness


def _is_public_host(host):
    """True only if every address the name points to is a normal public internet address."""
    if host in _public_cache:
        return _public_cache[host]
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
        ok = bool(addresses) and all(ipaddress.ip_address(a.split("%")[0]).is_global for a in addresses)
    except (OSError, ValueError):
        ok = False
    _public_cache[host] = ok
    return ok


def _wait_turn(host, delay):
    """Sleep if needed, so we never hit one site faster than `delay` seconds per request."""
    with _lock:
        wait = _last_request.get(host, 0) + delay - time.monotonic()
        _last_request[host] = time.monotonic() + max(wait, 0)
    if wait > 0:
        _sleep(wait)


def _http_get(url):
    """The ONLY function that touches the network (tests replace it).
    Returns (status_code, headers_lowercase_dict, body_bytes). Does not follow redirects."""
    resp = requests.get(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                                      "Accept-Language": "en"},
                        timeout=TIMEOUT_SECONDS, allow_redirects=False, stream=True)
    try:
        body, size = [], 0
        for chunk in resp.iter_content(chunk_size=65536):
            body.append(chunk)
            size += len(chunk)
            if size >= MAX_BYTES:
                break
        return resp.status_code, {k.lower(): v for k, v in resp.headers.items()}, b"".join(body)[:MAX_BYTES]
    finally:
        resp.close()


def _check_url(url):
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise OwnScrapeError(f"not a web address: {url}")
    if not _is_public_host(p.hostname):
        raise OwnScrapeError(f"refused: {p.hostname} is not a public website")
    return p


def _robots_for(url):
    """The parsed robots.txt for this site (None if it has none, or we could not read it)."""
    p = urlparse(url)
    origin = f"{p.scheme}://{p.netloc}"
    if origin not in _robots:
        parser = None
        try:
            if _is_public_host(p.hostname):
                _wait_turn(p.netloc, DELAY_SECONDS)
                status, _, body = _http_get(origin + "/robots.txt")
                if status == 200:
                    parser = robotparser.RobotFileParser()
                    parser.parse(body.decode("utf-8", errors="replace").splitlines())
        except Exception:
            parser = None  # no readable robots.txt: the usual rule is that everything is allowed
        _robots[origin] = parser
    return _robots[origin]


def allowed_by_robots(url):
    parser = _robots_for(url)
    return True if parser is None else parser.can_fetch(USER_AGENT, url)


def _fetch_html(url):
    """Download one page (following up to 5 redirects by hand, checking each stop). Returns (final_url, html)."""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        _check_url(current)
        if not allowed_by_robots(current):
            raise OwnScrapeError(f"blocked by robots.txt: {current}")
        parser = _robots_for(current)
        delay = DELAY_SECONDS
        if parser is not None and parser.crawl_delay(USER_AGENT):
            delay = min(max(delay, float(parser.crawl_delay(USER_AGENT))), MAX_CRAWL_DELAY)
        _wait_turn(urlparse(current).netloc, delay)
        status, headers, body = _http_get(current)
        if status in (301, 302, 303, 307, 308) and headers.get("location"):
            current = urljoin(current, headers["location"])
            continue
        if status >= 400:
            raise OwnScrapeError(f"HTTP {status}", status_code=status, fallback_ok=status in FALLBACK_STATUS)
        kind = headers.get("content-type", "text/html").split(";")[0].strip().lower()
        if kind not in ("text/html", "application/xhtml+xml", ""):
            raise OwnScrapeError(f"not an HTML page ({kind})")
        charset = re.search(r"charset=([\w-]+)", headers.get("content-type", ""), re.I)
        try:
            html = body.decode(charset.group(1) if charset else "utf-8", errors="replace")
        except LookupError:
            html = body.decode("utf-8", errors="replace")
        return current, html
    raise OwnScrapeError("too many redirects")


# ---------------------------------------------------------------- reading the page

_PARSER = "lxml"
try:
    import lxml  # noqa: F401  (faster than the built-in parser on big pages)
except ImportError:
    _PARSER = "html.parser"

# Always deleted: they never hold company facts.
_DROP_TAGS = ["script", "style", "noscript", "svg", "iframe", "canvas", "template", "form", "button", "select",
              "input", "textarea", "nav", "aside", "footer", "dialog"]
# Deleted when their class or id looks like page furniture (only if they are small, so we never delete the page).
_NOISE = re.compile(r"cookie|consent|newsletter|subscribe|popup|pop-up|modal|breadcrumb|sidebar|skip-link|social-share|"
                    r"share-buttons|announcement-bar|promo-bar", re.I)
_BLOCKS = ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "pre", "td", "th", "dt", "dd", "figcaption", "div"]
_INLINE = {"a", "span", "strong", "em", "b", "i", "u", "code", "small", "abbr", "mark", "sup", "sub", "time", "label", "br", "img"}


def _clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def _own_text(el):
    """The text that belongs to this element itself: its own words and inline bits (links, bold ...),
    but not the text of block elements nested inside it (those are handled on their own)."""
    parts = []
    for child in el.children:
        if type(child) is NavigableString:  # real text only: not HTML comments (<!-- ... -->), which are a subclass
            parts.append(str(child))
        elif child.name in _INLINE:
            parts.append(child.get_text(" "))
    return _clean(" ".join(parts))


def extract_links(soup, base_url):
    """Every http(s) link on the page, absolute, without #fragments, no repeats."""
    seen, out = set(), []
    for a in soup.find_all("a", href=True):
        href = urldefrag(urljoin(base_url, a["href"].strip()))[0]
        if urlparse(href).scheme in ("http", "https") and href not in seen:
            seen.add(href)
            out.append(href)
        if len(out) >= MAX_LINKS:
            break
    return out


def extract_main_text(soup):
    """The page's main content as simple text: '# Heading' lines, '- item' lines, paragraphs."""
    title = _clean(soup.title.get_text()) if soup.title else ""
    meta = soup.find("meta", attrs={"name": re.compile("^description$", re.I)}) or soup.find("meta", attrs={"property": "og:description"})
    description = _clean(meta.get("content", "")) if meta else ""

    for tag in soup.find_all(_DROP_TAGS):
        tag.decompose()
    # A header is usually the menu bar (its links were just deleted, so little text is left). But a header
    # that holds the page's main headline (an <h1>) or real text is the hero: keep it, wherever it is.
    for header in soup.find_all("header"):
        if not header.find("h1") and len(_clean(header.get_text())) < HERO_MIN_CHARS:
            header.decompose()
    for el in soup.find_all(attrs={"hidden": True}) + soup.find_all(attrs={"aria-hidden": "true"}):
        if not el.decomposed and el.name not in ("html", "body"):
            el.decompose()
    for el in soup.find_all(True):
        if el.decomposed or el.name in ("html", "body", "main", "article"):
            continue  # (an element inside something already deleted is gone too)
        label = " ".join([" ".join(el.get("class") or []), el.get("id") or ""])
        style = (el.get("style") or "").replace(" ", "").lower()
        if "display:none" in style or (label.strip() and _NOISE.search(label) and len(el.get_text()) < 1500):
            el.decompose()

    # the page's main part: the biggest <main> or <article> if it has real content, else whatever is left
    candidates = soup.find_all(["main", "article"]) or soup.select("[role=main]")
    best = max(candidates, key=lambda r: len(r.get_text()), default=None)
    if best is not None and len(best.get_text()) > 200:
        # the main part, plus any hero header that sits outside it (it comes first on the page)
        heroes = [h for h in soup.find_all("header") if not h.decomposed and not h.find_parent(["main", "article"]) and best not in h.parents]
        roots = [*heroes, best]
    else:
        roots = [soup.body or soup]

    lines = []
    for root in roots:
        for el in root.find_all(_BLOCKS):
            text = _own_text(el)
            if not text or (lines and lines[-1].lstrip("-# ").strip() == text):
                continue
            if el.name[0] == "h" and el.name[1:].isdigit():
                text = f"{'#' * int(el.name[1])} {text}"
            elif el.name == "li":
                text = f"- {text}"
            lines.append(text)
    body = "\n".join(lines)[:MAX_TEXT_CHARS]
    head = "\n".join(x for x in (title, description) if x)
    return (head + "\n\n" + body).strip() if head and head not in body[:400] else body


def _javascript_signals(soup):
    """Read BEFORE cleaning: how many <script> tags, and is there an empty app container (#root, #__next ...)?"""
    scripts = len(soup.find_all("script"))
    empty_app = any(soup.find(id=i) is not None and len(_clean(soup.find(id=i).get_text())) < 50
                    for i in ("root", "__next", "app", "___gatsby", "__nuxt"))
    return scripts, empty_app


def fetch_doc(url):
    """Fetch one page and return {"markdown", "raw_html", "links", "via", "needs_js"}. Raises OwnScrapeError."""
    final_url, html = _fetch_html(url)
    soup = BeautifulSoup(html, _PARSER)
    links = extract_links(soup, final_url)  # taken first: the menu links are exactly what discovery needs
    scripts, empty_app = _javascript_signals(soup)
    text = extract_main_text(soup)
    # very little text plus lots of scripts (or an empty app container) means the page is built in the browser
    needs_js = len(text) < THIN_TEXT_CHARS and (scripts >= 3 or empty_app)
    return {"markdown": text, "raw_html": html, "links": links, "via": "own", "needs_js": needs_js}
