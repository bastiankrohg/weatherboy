"""The news on paper: NRK's "Dette skjedde i natt" (the night's stories, each with a line from its own page) and the
main headlines from an international newspaper (The New York Times' front page, or any RSS feed set in
WEATHERBOY_WORLD_FEED). Two receipts, printed by the buttons on the page and, if switched on, every morning.

    python news.py        both, as text
"""
import envfile  # noqa: F401 - .env loaded before the settings below are read
import html
import os
import re
from datetime import date, datetime

import requests

NRK_SERIES = "https://www.nrk.no/nyheter/dette-skjedde-i-natt-1.11996745"  # lists the latest editions, newest first
WORLD_FEED = os.environ.get("WEATHERBOY_WORLD_FEED", "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml")
PAPERS = {"NYT": "The New York Times", "BBC News": "BBC News"}  # a feed's own name -> what goes on the paper
UA = {"User-Agent": "weatherboy/0.1 (personal receipt printer)"}
MONTHS = "januar februar mars april mai juni juli august september oktober november desember".split()
TOP = 6  # stories on the world receipt


def _get(url):
    r = requests.get(url, headers=UA, timeout=15)
    r.raise_for_status()
    if "charset" not in r.headers.get("Content-Type", ""):
        r.encoding = "utf-8"  # NRK doesn't say; requests would guess Latin-1, and "•", æ, ø and å come out garbled
    return r.text


def _text(fragment):
    """HTML -> one line of plain text."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _short(text, limit=220):
    """At most `limit` characters, cut at a word, with … if it was longer."""
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:–-") + " …"


def _description(url):
    """A story's own one-line summary (its meta description), or "" if it can't be had."""
    try:
        m = re.search(r'<meta name="description" content="([^"]*)"', _get(url))
        return _short(html.unescape(m.group(1))) if m else ""
    except requests.RequestException:
        return ""


def nrk_edition(day=None):
    """The URL of NRK's "Dette skjedde natt til <date>": today's (or `day`'s) if given, else the newest.
    LookupError if that one isn't out yet."""
    links = list(dict.fromkeys(re.findall(r'href="(https://www\.nrk\.no/nyheter/dette-skjedde-natt-til-[^"]+)"',
                                          _get(NRK_SERIES))))
    if not links:
        raise LookupError("Fant ikke «Dette skjedde i natt» hos NRK.")
    if day is None:
        return links[0]
    slug = f"natt-til-{day.day}.-{MONTHS[day.month - 1]}"
    hit = next((u for u in links if slug in u or f"-{day.day}.-{MONTHS[day.month - 1]}-" in u), None)
    if not hit:
        raise LookupError(f"NRK har ikke publisert «Dette skjedde natt til {day.day}. {MONTHS[day.month - 1]}» ennå.")
    return hit


def nrk_night(today_only=False):
    """{"title", "source", "items": [{"headline", "summary"}]}: the night's stories in NRK's own order.
    today_only: refuse an older edition (the morning print waits for today's)."""
    url = nrk_edition(date.today() if today_only else None)
    page = _get(url)
    title = _text(re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S).group(1))
    article = page[page.find("<h1"):]  # the page's menus above it have "•" in them too
    bullets = next((p for p in re.findall(r"<p[^>]*>(.*?)</p>", article, re.S) if "•" in p), "")
    headlines = [h.strip() for h in _text(bullets).split("•") if h.strip()]
    anchors = [(u, _text(inner)) for u, inner in re.findall(r'<a[^>]*href="(https://www\.nrk\.no/[^"]+)"[^>]*>(.*?)</a>',
                                                             page, re.S)]
    items = []
    for h in headlines:
        link = next((u for u, t in anchors if h in t and u != url), None)  # the story's own "Les også" link
        items.append({"headline": h, "summary": _description(link) if link else ""})
    if not items:
        raise LookupError("Fant ingen saker i «Dette skjedde i natt».")
    return {"title": "Dette skjedde i natt", "subtitle": title.replace("Dette skjedde ", "").strip(),
            "source": "nrk.no", "items": items}


def world(feed=WORLD_FEED, top=TOP):
    """{"title", "source", "items": [...]}: the first `top` stories of an RSS feed (a paper's front page), each
    with its standfirst."""
    xml = _get(feed).replace("<![CDATA[", "").replace("]]>", "")  # BBC and others wrap their text in CDATA
    channel = _text(re.search(r"<title>(.*?)</title>", xml, re.S).group(1))
    items = []
    for item in re.findall(r"<item>(.*?)</item>", xml, re.S)[:top]:
        title = _text(re.search(r"<title>(.*?)</title>", item, re.S).group(1))
        desc = re.search(r"<description>(.*?)</description>", item, re.S)
        desc = html.unescape(desc.group(1)) if desc else ""
        first = re.search(r"<p>(.*?)</p>", desc, re.S)  # the standfirst: the paper's own one-line summary
        items.append({"headline": title, "summary": _short(_text(first.group(1) if first else desc))})
    if not items:
        raise LookupError(f"Ingen saker i {feed}.")
    name = re.split(r"\s*[>|-]\s", channel)[0].strip()  # "NYT > Top Stories" -> "NYT"
    if "|" in channel:  # "International | The Guardian": the paper's name comes last
        name = channel.split("|")[-1].strip()
    return {"title": PAPERS.get(name, name), "subtitle": datetime.now().strftime("%A %d %B").replace(" 0", " "),
            "source": re.sub(r"^https?://(www\.|rss\.|feeds\.)?([^/]+).*", r"\2", feed), "items": items}


if __name__ == "__main__":
    for get in (nrk_night, world):
        n = get()
        print(f"\n{n['title']} - {n['subtitle']} ({n['source']})")
        for it in n["items"]:
            print(f"  * {it['headline']}\n      {it['summary']}")
