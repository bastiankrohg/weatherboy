"""Web search and page fetching for the local model (Claude has its own, server-side). Free: DuckDuckGo's plain
HTML results by default, or a self-hosted SearXNG when WEATHERBOY_SEARXNG_URL is set (sturdier, more private).

Fetching is limited to public internet addresses: the model picks the URLs, and a web page can try to steer it,
so nothing it reads may send it to the printer, the router or anything else on the home network."""
import envfile  # noqa: F401 - .env loaded before the settings below are read
import ipaddress
import os
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urlparse

import requests

SEARXNG = os.environ.get("WEATHERBOY_SEARXNG_URL", "").rstrip("/")  # e.g. http://robotlab:8888
UA = {"User-Agent": "Mozilla/5.0 (compatible; weatherboy/1.1; personal assistant)"}
MAX_PAGE = 6000  # characters of a fetched page handed to the model: local models have small contexts


class _Text(HTMLParser):
    """Readable text of a page: skips scripts, styles, navigation and other chrome."""
    SKIP = {"script", "style", "noscript", "svg", "nav", "header", "footer", "form", "head"}

    def __init__(self):
        super().__init__()
        self.out, self.depth, self.title, self._in_title = [], 0, "", False

    def handle_starttag(self, tag, attrs):
        self.depth += tag in self.SKIP
        self._in_title = tag == "title"

    def handle_endtag(self, tag):
        self.depth -= tag in self.SKIP and self.depth > 0
        self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.depth and data.strip():
            self.out.append(" ".join(data.split()))


class _DDG(HTMLParser):
    """DuckDuckGo's HTML results: <a class="result__a" href=…>title</a> … <a class="result__snippet">…</a>"""

    def __init__(self):
        super().__init__()
        self.results, self.field = [], None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class") or ""
        if tag == "a" and "result__a" in cls:
            self.results.append({"title": "", "url": a.get("href", ""), "snippet": ""})
            self.field = "title"
        elif tag == "a" and "result__snippet" in cls and self.results:
            self.field = "snippet"

    def handle_endtag(self, tag):
        if tag == "a":
            self.field = None

    def handle_data(self, data):
        if self.field:
            self.results[-1][self.field] += data


def web_search(query: str, max_results: int = 5) -> str:
    """Search the web. Returns titles, URLs and short snippets; fetch a page with web_fetch to read more.

    Args:
        query: What to search for, in whatever language fits best.
        max_results: How many results, up to 8.
    """
    n = max(1, min(int(max_results), 8))
    if SEARXNG:
        r = requests.get(f"{SEARXNG}/search", params={"q": query, "format": "json"}, headers=UA, timeout=15)
        r.raise_for_status()
        hits = [{"title": h.get("title", ""), "url": h.get("url", ""), "snippet": h.get("content", "")}
                for h in r.json().get("results", [])]
    else:
        r = requests.post("https://html.duckduckgo.com/html/", data={"q": query}, headers=UA, timeout=15)
        r.raise_for_status()
        p = _DDG()
        p.feed(r.text)
        hits = [h for h in p.results if h["url"].startswith("http")]  # drops ads, which are redirects
    if not hits:
        return f"No results for {query!r}."
    return "\n\n".join(f"{h['title'].strip()}\n{h['url']}\n{' '.join(h['snippet'].split())}" for h in hits[:n])


def public(url):
    """Only http(s) to hosts that resolve to public addresses: not this machine, the LAN or link-local."""
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        return False
    try:
        addrs = {info[4][0] for info in socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80))}
    except OSError:
        return False
    return all(ipaddress.ip_address(a.split("%")[0]).is_global for a in addrs)


def web_fetch(url: str) -> str:
    """Read a web page (one from web_search, or one the user mentioned) as plain text, shortened.

    Args:
        url: The full http(s) address of the page.
    """
    if not public(url):
        return "Refused: only public web pages can be fetched."
    r = requests.get(url, headers=UA, timeout=15, stream=True, allow_redirects=False)
    for _ in range(4):  # follow redirects by hand, so each hop gets the same public-address check
        if r.status_code not in (301, 302, 303, 307, 308):
            break
        url = requests.compat.urljoin(url, r.headers.get("Location", ""))
        if not public(url):
            return "Refused: the page redirects to a non-public address."
        r = requests.get(url, headers=UA, timeout=15, stream=True, allow_redirects=False)
    r.raise_for_status()
    raw = r.raw.read(2_000_000, decode_content=True)  # 2 MB is plenty for text
    text = raw.decode(r.encoding or "utf-8", errors="replace")
    if "html" not in r.headers.get("Content-Type", "html"):
        return text[:MAX_PAGE]
    p = _Text()
    p.feed(text)
    body = re.sub(r"\s+\n", "\n", "\n".join(p.out))
    return (f"{p.title.strip()}\n{url}\n\n{body}")[:MAX_PAGE]
