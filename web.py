"""Weatherboy's web page: every receipt, Claude, recipes and photo printing from a browser on the LAN.

    python web.py --printer 192.168.0.108      just the web page (main.py starts it too, see --web)

Every endpoint that makes a receipt returns its PNG preview, decoded from the actual job bytes. Printing is
?print=1 on the same request, so the page previews first and prints as a second, deliberate tap. A printer that
is off, busy or out of paper doesn't lose the job: it lands in printq's queue and goes out when it can, and the
reply says so in X-Queued.
ponytail: no login. Anyone on the LAN can print and spend API credit. Add a shared password if the flat grows.
"""
import hmac
import io
import json
import os
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import anthropic
import qrcode
import requests
from PIL import Image, ImageEnhance, ImageOps

import agent
import cookbook
import daily
import home
import issues
import orb
import layout
import local
import presets
import printer
import printq
import router
import shoplist
import websearch
import words

PORT = 8615
MAX_UPLOAD = 25_000_000  # bytes; a big phone photo is ~10 MB
PAGE = Path(__file__).with_name("web.html")
ORB_PAGE = Path(__file__).with_name("orb.html")
PRINTER = None           # printer IP, set by start(); None = previews only
URL = f"http://127.0.0.1:{PORT}"  # this page's address for the QR code, set by start()
PUBLIC_URL = os.environ.get("WEATHERBOY_PUBLIC_URL", "")  # the tunnel's address, e.g. https://weatherboy.example.no
AWAY = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>Weatherboy</title><body style='font:18px system-ui;margin:3em 1.5em;max-width:30em'>"
        "<h1>Weatherboy er hjemme</h1><p>Siden virker bare fra WiFi-en hjemme. Koble til den, og last inn på nytt.</p>")


def lan_ip():
    """The address other devices on the WiFi reach this machine at. No packet is sent: connect() on UDP
    only picks the outgoing interface."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 9))  # TEST-NET, never routed anywhere
            return s.getsockname()[0]
        except OSError:  # no network at all
            return "127.0.0.1"

def _at(when):
    """nlp.match() answers with a When (a start and an end); the receipt header stamps the start."""
    return when.start if when else None


def _hours(when, default=24):
    """How much forecast a receipt asked for a time needs: 'i kveld' is 7 hours, 'i morgen' is 18."""
    return default if not when else max(1, min(48, round((when.end - when.start).total_seconds() / 3600)))


def _weather(place=None, when=None):
    """The forecast, for the place nlp worked out or the configured one, over the time it worked out.
    Both are None when nothing was said, which is the same receipt the page's own button gets."""
    lat, lon = (place.lat, place.lon) if place else (agent.LAT, agent.LON)
    return layout.weather(place.name if place else agent.PLACE, agent.forecast(lat, lon, _hours(when)), _at(when))


def _departures(stop=None, mode=None, when=None):
    """Departures from the stop nlp named, else the configured first one; only the mode it heard.
    agent.calls resolves the stop and returns it with the board, so nothing is named twice, and no mode
    at all is every mode - a stop configured without one, or an utterance that didn't say, still gets all."""
    if stop is None:  # nothing heard at all: the default board, with whatever the default mode is
        stop, mode = agent.STOPS[0][0], agent.STOPS[0][1]
    return layout.departures(*agent.calls(stop, 10, mode), _at(when))


def _flights(place=None, when=None):
    """Aircraft over the place nlp worked out, or the configured one."""
    lat, lon = (place.lat, place.lon) if place else (agent.LAT, agent.LON)
    return layout.radar(place.name if place else agent.PLACE, lat, lon,
                        agent.aircraft(lat, lon, radius_km=40), 40, _at(when))


def _art(when=None):
    """The day's maze, seeded by its date so the same day always prints the same one."""
    day = when.start.date() if when else date.today()
    return layout.maze(day, _at(when))


def _word(lang=None):
    """Today's word, in the language asked for, else the daily setting's. Cached after the first call."""
    return word_card(lang or daily.settings()["lang"])


CARDS = {  # the keyword receipts, shared with main.py. The keyword arguments are what nlp.match() works out.
    "weather": _weather, "departures": _departures, "flights": _flights, "art": _art,
    "qr": lambda when=None: layout.qr(URL, _at(when)), "word": _word,
}


def photo(data):
    """An uploaded photo as a grayscale page (dithered later, in render, after any edits)."""
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))  # phones store rotation in EXIF
    return layout.photo(img.convert("L"))  # stamped when rendered: at upload for the preview, at print for paper


def edits(q):
    """The page's adjustments, from the query string. Anything missing means "leave it"."""
    clamp = lambda v, lo, hi: min(max(v, lo), hi)
    return {"brightness": clamp(float(q.get("brightness", 1)), 0.2, 3.0),
            "contrast": clamp(float(q.get("contrast", 1)), 0.2, 4.0)}


def copies(q):
    """?copies=3: how many of it to print. Capped, so a typo doesn't empty the paper roll."""
    return min(max(int(q.get("copies", 1)), 1), 20)


LABELS = {"weather": "vær", "departures": "avganger", "flights": "fly", "art": "kunst", "qr": "qr-kode",
          "word": "dagens ord"}
BY_KIND = {"card": "kvittering", "answer": "svar", "print": "utskrift"}


def file_issue(preset, wish):
    """A GitHub issue for a new wish -> its URL, or None (no token, or GitHub said no: the wish still waits)."""
    if not issues.enabled():
        return None
    try:
        number, url = issues.open_issue(preset, wish)
        presets.set_issue(preset["id"], number)
        return url
    except requests.RequestException as e:
        print(f"wishes: no GitHub issue for {preset['name']!r} ({e})")
        return None


def decide(pid, approved):
    """The admin's verdict, from the page: the wish becomes a button or goes; its issue is closed to match."""
    wish = presets.get(pid, pending=True)
    presets.approve(pid) if approved else presets.remove(pid)
    if wish.get("issue") and issues.enabled():
        try:
            issues.close(wish["issue"], approved)
        except requests.RequestException as e:
            print(f"wishes: couldn't close issue #{wish['issue']} ({e})")


def sync_wishes():
    """The other way round: a wish whose issue the admin closed on GitHub is approved or turned down here."""
    for w in presets.items(pending=True):
        if w.get("issue"):
            try:
                v = issues.verdict(w["issue"])
            except requests.RequestException:
                continue
            if v:
                presets.approve(w["id"]) if v == "approved" else presets.remove(w["id"])


def keep_syncing_wishes(every=120):
    while True:
        sync_wishes()
        time.sleep(every)


def wishes_json(admin):
    return {"admin": admin, "github": issues.enabled(),
            "pending": [{k: w[k] for k in ("id", "name", "prompt", "icon") if k in w} for w in presets.items(pending=True)]}


def esp():
    """-> (the phone base on the USB cable, or None; why not). The same board main.py's voice loop reads."""
    import importlib.util
    if importlib.util.find_spec("serial") is None:
        return None, "pyserial mangler: uv sync --extra phone"
    import phone
    try:
        board = phone.shared(serial_only=True)
    except Exception as e:  # noqa: BLE001 - e.g. the port is busy (an Arduino serial monitor has it open)
        return None, f"Får ikke åpnet porten: {e}"
    return (board, None) if board else (None, "Ingen ESP8266 på USB-kabelen")


def esp_state():
    board, why = esp()
    if not board:
        return {"found": False, "why": why}
    out = {"found": True, "where": board.link.where, "lifted": board.lifted()}
    try:
        out["config"] = board.config()
    except (TimeoutError, RuntimeError, ValueError) as e:
        out["why"] = str(e)
    return out


def esp_post(d):
    """{set: {host, port, ssid, pass}} saves them on the board (an empty pass leaves it as it is),
    {test: true} has it knock on the server, {restart: true} reboots it."""
    board, why = esp()
    if not board:
        raise RuntimeError(why)
    if "set" in d:
        changes = {k: str(v).strip() for k, v in d["set"].items() if k in ("host", "port", "ssid", "pass")}
        if not changes.get("pass"):
            changes.pop("pass", None)
        board.set(**changes)
        return esp_state() | {"said": "Lagret på brettet."}
    if d.get("test"):
        reply = board.ask("TEST", timeout=6)
        return esp_state() | {"said": reply.removeprefix("OK ").removeprefix("ERR "), "ok": reply.startswith("OK")}
    if d.get("restart"):
        board.ask("RESTART")
        return {"found": True, "said": "Starter på nytt …"}
    raise ValueError("Ukjent ESP-kommando")


def label_for(kind, cmd):
    """What a queued receipt should be called in the list: the card's name, or the sort of thing."""
    return LABELS.get(cmd) or BY_KIND.get(kind, "kvittering")


def render(img, do_print, brightness=1.0, contrast=1.0, dither=False, label="kvittering", source=None,
           queue=False, printer_ip=None, copies=1):
    """Every receipt goes through here: grayscale page -> brightness/contrast -> threshold (text) or Atkinson
    dither (pictures) -> printer rows. Returns (the PNG of exactly what the head fires, what happened to it).

    do_print sends it now and queue puts it in the queue without trying. A printer that won't take it leaves it
    in the queue either way - nothing is lost - so outcome is {"printed", "id" of the queued job or None, "why"}.
    printer_ip defaults to the server's; main.py passes its own, since it prints from the handset.
    On text, brightness moves the threshold across the antialiased edges: brighter prints thinner strokes, darker
    prints bolder ones. Crops, rotations and drawings happen in the page's editor (/api/image).
    copies: that many of it, each its own job (and so its own cut). Once the printer turns one away, the rest
    go straight into the queue behind it, in order, rather than knocking again; the outcome is the last one's."""
    img = img.convert("L")
    if brightness != 1:
        img = ImageEnhance.Brightness(img).enhance(brightness)
    if contrast != 1:
        img = ImageEnhance.Contrast(img).enhance(contrast)
    if dither:
        img = layout.dither(img)
    job = printer.encode(layout.to_rows(img))
    outcome = {"printed": False, "id": None, "why": None}
    for _ in range(max(1, copies) if queue or do_print else 1):
        if queue or outcome["id"]:
            outcome = {"printed": False, "id": printq.add(img, label, source, dither=dither)["id"],
                       "why": outcome["why"] or "lagt i køen"}
        elif do_print:
            outcome = printq.print_or_queue(img, label, source, printer_ip or PRINTER, dither=dither)
    buf = io.BytesIO()
    layout.from_rows(printer.decode(job)).save(buf, "PNG")
    return buf.getvalue(), outcome


def answer_json(answer, dollars, icon=None, model=None):
    """What the page needs from one of Claude's answers."""
    theme, answer = layout.theme(answer)  # the "tema: x" line becomes the receipt's icon
    return {"answer": answer, "icon": icon or theme, "cost": dollars, "spent": agent.spent,
            "model": agent.MODELS[model or agent.model]["name"],
            # the recipe it read or drafted, so the page shows that card rather than a short note
            "recipe": cookbook.last and cookbook.last.relative_to(cookbook.ROOT).with_suffix("").as_posix()}


def printer_here():
    """Is this machine on the printer's network, i.e. at home? A knock on the printer's port, as patient as
    printing is: on WiFi the printer can take seconds to answer, and a short knock would call that away."""
    if not PRINTER:
        return True  # no printer configured: the network we're on is all we have to go by
    try:
        printer.connect(PRINTER).close()
        return True
    except OSError:
        return False


def status():
    """green: ready. yellow: on the network but won't print. red: can't reach it. off: no printer configured.
    Anything waiting rides along on the printer's own light, which is where you'd look for it anyway."""
    if not PRINTER:
        s = {"state": "ingen skriver valgt", "level": "off"}
    else:
        try:
            asb = printer.status(PRINTER)
            if not asb:
                s = {"state": "opptatt, svarer ikke på status", "level": "yellow"}
            else:
                bad = printer.problems(asb)
                s = {"state": ", ".join(bad) or "klar", "level": "yellow" if bad else "green"}
        except ConnectionRefusedError:  # what this printer does with its cover open
            s = {"state": "avviser tilkobling (lokket åpent?)", "level": "yellow"}
        except OSError:
            s = {"state": f"svarer ikke på {PRINTER}", "level": "red"}
    jobs, _ = printq.count()
    if jobs:
        s["state"] += f" · {jobs} i kø"
    return s


def check_local():
    """The free model: green on the first server (the desktop), yellow on a fallback or without the model pulled."""
    for i, url in enumerate(local.SERVERS):
        try:
            r = requests.get(url.rstrip("/") + "/models", timeout=2)
            r.raise_for_status()
        except requests.RequestException:
            continue
        host = urlparse(url).hostname
        names = {m.get("id", "") for m in r.json().get("data", [])}
        if local.MODEL not in names and f"{local.MODEL}:latest" not in names:
            return {"level": "yellow", "state": f"{host} svarer, men {local.MODEL} er ikke lastet ned"}
        return {"level": "green" if i == 0 else "yellow", "state": host + ("" if i == 0 else " (reserve)")}
    return {"level": "red", "state": "ingen svarer: " + ", ".join(urlparse(u).hostname or u for u in local.SERVERS)}


def check_claude():
    """Is the key there and accepted? Looking a model up is free."""
    if not (agent.client.api_key or agent.client.auth_token):
        return {"level": "off", "state": "ingen API-nøkkel"}
    try:
        agent.client.with_options(timeout=5, max_retries=0).models.retrieve("claude-haiku-4-5")
        return {"level": "green", "state": f"klar · brukt ≈ ${agent.spent:.4f}"}
    except anthropic.AuthenticationError:
        return {"level": "red", "state": "nøkkelen ble avvist"}
    except anthropic.APIError as e:
        return {"level": "yellow", "state": f"svarer ikke ordentlig ({type(e).__name__})"}


def check_tunnel():
    """Round trip through Cloudflare back to this server. A 403 still means the tunnel works (we're not home)."""
    if not PUBLIC_URL:
        return {"level": "off", "state": "ikke satt opp"}
    try:
        code = requests.get(PUBLIC_URL + "/api/url", timeout=8).status_code
    except requests.RequestException:
        return {"level": "red", "state": "nede"}
    if code in (200, 403):
        return {"level": "green", "state": urlparse(PUBLIC_URL).hostname}
    return {"level": "red", "state": f"nede (HTTP {code})"}


def check_websearch():
    """What the local model searches with: SearXNG if configured, else DuckDuckGo."""
    target = websearch.SEARXNG or "https://html.duckduckgo.com/html/"
    try:
        requests.get(target, timeout=5, headers=websearch.UA).raise_for_status()
        return {"level": "green", "state": "SearXNG" if websearch.SEARXNG else "DuckDuckGo"}
    except requests.RequestException:
        return {"level": "red", "state": "svarer ikke"}


def check_daily():
    s, done, today = daily.settings(), daily.done(), date.today().isoformat()
    jobs = [j for j in daily.JOBS if s[j]]
    if not jobs:
        return {"level": "off", "state": "av"}
    if all(done.get(j) == today for j in jobs):
        return {"level": "green", "state": "skrevet ut i dag"}
    hh, mm = map(int, s["time"].split(":"))
    now = datetime.now()
    if (now.hour, now.minute) >= (hh, mm) and PRINTER:  # due, but not out yet: retrying every minute
        return {"level": "yellow", "state": f"venter på skriveren (fra kl. {s['time']})"}
    return {"level": "green", "state": f"kl. {s['time']}"}


CHECKS = {"printer": ("Skriver", status), "local": ("Lokal modell", check_local), "claude": ("Claude", check_claude),
          "tunnel": ("Tunnel", check_tunnel), "websearch": ("Websøk", check_websearch), "daily": ("Daglig", check_daily)}
_health = {"at": 0.0, "result": None}
_health_lock = threading.Lock()


def health():
    """Every component checked on its own, all at once: one slow or broken part can't hold up the others or
    take them down. Cached for 15 s, since every open page asks every 10 s."""
    with _health_lock:
        if _health["result"] is None or time.time() - _health["at"] > 15:
            def run(check):
                try:
                    return check()
                except Exception as e:  # a check that crashes is itself a red light, not a broken page
                    return {"level": "red", "state": f"feil i sjekken: {type(e).__name__}"}
            with ThreadPoolExecutor(len(CHECKS)) as pool:
                found = dict(zip(CHECKS, pool.map(run, [c for _, c in CHECKS.values()])))
            _health["result"] = [{"id": k, "label": CHECKS[k][0], **v} for k, v in found.items()]
            _health["at"] = time.time()
        return _health["result"]


def recipes():
    rows = [r.split(" | ") for r in cookbook.list_recipes().splitlines() if " | " in r]
    return [{"name": r[0], "title": r[1], "tags": r[2], "draft": len(r) > 3} for r in rows]


def queue_state(**extra):
    """What the page needs to draw the queue: the jobs in order, and how many are on hold."""
    jobs, held = printq.count()
    return {"items": printq.items(), "count": jobs, "held": held, **extra}


def queue_post(q, body):
    """The queue, from the page: one verb per request, in the query string like ?print=1, and a raw PNG
    body where there's an image. -> queue_state(), so the list redraws from every one of them.

    Only what the page actually does. Printing one job out of turn isn't here on purpose: hold the rest
    instead, so there's one order to reason about."""
    if "replace" in q:      # the page's editor changed this one; same id, same place in the order
        printq.replace_img(q["replace"], Image.open(io.BytesIO(body)))
    elif "print" in q:      # everything not held, in order; stops at the first refusal, keeps the rest
        if not PRINTER:
            raise ValueError("Ingen skriver er valgt: start med --printer <ip>")
        return queue_state(printed=printq.print_now(PRINTER))
    elif "clear" in q:
        printq.remove([j["id"] for j in printq.items()])
    elif "remove" in q:
        printq.remove([i for i in q["remove"].split(",") if i])
    elif "move" in q:       # one step up the queue (-1) or down (+1); nothing happens at either end
        printq.move(q["move"], int(q.get("delta", -1)))
    elif "hold" in q:       # held jobs are skipped by the printer, never dropped
        printq.hold(q["hold"], q.get("on") == "1")
    else:
        raise ValueError("Ukjent kommando for utskriftskøen")
    return queue_state()


class NeedsKey(Exception):
    """A paid model asked for through the tunnel without the visitor's own key: the page asks for one."""


class Handler(BaseHTTPRequestHandler):
    def reply(self, code, body, ctype, headers=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def online(self):
        """Came in through the tunnel (print.<domain>), not straight off the local network."""
        return self.headers.get("CF-Connecting-IP") is not None

    def admin(self):
        """The admin: whoever sends WEATHERBOY_ADMIN from .env as X-Admin, or someone at this machine's own
        keyboard (a request from itself, not through the tunnel)."""
        want, given = os.environ.get("WEATHERBOY_ADMIN", ""), self.headers.get("X-Admin", "")
        if want and given and hmac.compare_digest(want.encode(), given.encode()):
            return True
        return not self.online() and self.client_address[0] in ("127.0.0.1", "::1")

    def model(self, d):
        """-> (model, API key) for a question from the page. The page sends its own pick; through the tunnel,
        a paid model only runs on the visitor's own key (X-Api-Key), never on the flat's."""
        m, key = d.get("model") or agent.model, self.headers.get("X-Api-Key") or None
        if m not in agent.MODELS:
            raise ValueError(f"Ukjent modell {m!r}")
        if m != "local" and self.online() and not key:
            raise NeedsKey("Claude-modellene koster penger. Utenfra kjører de på din egen API-nøkkel: "
                           "legg den inn under modellvelgeren, eller velg den lokale modellen (gratis).")
        return m, key

    def json(self, obj, code=200):
        self.reply(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def home_only(self):
        """Visits through the tunnel carry Cloudflare's CF-Connecting-IP: only the flat's own address gets in.
        Without it, the request came straight from the local network (as before the tunnel). Nobody can leave
        the header out from outside: the only way in from the internet is through Cloudflare, which sets it."""
        visitor = self.headers.get("CF-Connecting-IP")
        if visitor is None or home.allowed(visitor, at_home=printer_here):
            return True
        self.reply(403, AWAY.encode(), "text/html; charset=utf-8")
        return False

    def do_GET(self):
        if not self.home_only():
            return
        try:
            self._get(urlparse(self.path).path)
        except Exception as e:  # e.g. an unknown recipe name: tell the page instead of dropping the connection
            self.json({"error": f"{type(e).__name__}: {e}"}, 500)

    def _get(self, path):
        if path == "/":
            self.reply(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif path == "/orb":  # the voice loop on screen, for the Mac it runs on (main.py --gui)
            self.reply(200, ORB_PAGE.read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/orb":  # Server-Sent Events: the orb's state, FPS times a second, until the page goes
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            orb.stream(self.wfile)
        elif path == "/api/health":
            self.json(health())
        elif path == "/api/recipes":
            self.json(recipes())
        elif path == "/api/qr.png":  # for the page: scan the laptop screen with a phone
            buf = io.BytesIO()
            qrcode.make(URL, border=2, box_size=6).save(buf)
            self.reply(200, buf.getvalue(), "image/png")
        elif path == "/api/url":
            self.json({"url": URL})
        elif path == "/api/recipe-text":
            name = parse_qs(urlparse(self.path).query)["name"][0]
            self.json({"markdown": cookbook.read_recipe(name), "photo": bool(cookbook.photo(name))})
        elif path == "/api/recipe-template":
            tpl = cookbook.ROOT / "_mal.md"
            self.json({"markdown": tpl.read_text(encoding="utf-8") if tpl.exists() else
                       "---\nporsjoner: 4\ntid: \ntags: \nkilde: \n---\n\n# \n\n## Ingredienser\n\n- \n\n## Slik gjør du\n\n1. \n"})
        elif path == "/api/shopping":
            self.json(shoplist.items())
        elif path == "/api/presets":
            self.json(presets.items())
        elif path == "/api/esp":  # the phone base: where it is, its settings and state
            self.json(esp_state())
        elif path == "/api/wishes":  # what's waiting for the admin, and whether this visitor is the admin
            self.json(wishes_json(self.admin()))
        elif path == "/api/daily":
            self.json({"settings": daily.settings(), "done": daily.done(),
                       "langs": [{"id": k, "name": v[0]} for k, v in words.LANGS.items()]})
        elif path == "/api/stops":
            self.json([{"name": n, "mode": m} for n, m in agent.STOPS])
        elif path == "/api/models":
            self.json({"model": agent.model, "spent": agent.spent, "online": self.online(),
                       "models": [{"id": k, "name": v["name"], "price": v["price"]} for k, v in agent.MODELS.items()]})
        elif path == "/api/queue":
            self.json(queue_state())
        elif path.startswith("/api/queue/") and path.endswith(".png"):  # a job's thumbnail, and what ✎ opens
            id = path[len("/api/queue/"):-len(".png")]
            png = printq.png_path(id)
            if not printq.get(id) or not png.exists():
                return self.json({"error": "not found"}, 404)
            self.reply(200, png.read_bytes(), "image/png")
        else:
            self.json({"error": "not found"}, 404)

    def receipt(self, img, q, dither=False, label="kvittering", source=None, headers=()):
        """Every receipt endpoint ends here. ?raw=1 hands the page's editor the grayscale original
        (X-Dither says whether it's a picture); otherwise it's rendered, and printed with ?print=1.
        ?queue=1 skips the printer and puts it in the queue to print next. X-Queued or X-Printed says
        which of the two happened, X-Why what stopped it - and a plain preview has neither, because
        nothing was asked of the printer. -> what happened, for the caller."""
        if q.get("raw") == "1":
            buf = io.BytesIO()
            img.convert("L").save(buf, "PNG")
            self.reply(200, buf.getvalue(), "image/png", [("X-Dither", "1" if dither else "0")])
            return {"printed": False, "id": None, "why": None}
        png, outcome = render(img, q.get("print") == "1", **edits(q), dither=dither, label=label,
                              source=source, queue=q.get("queue") == "1", copies=copies(q))
        headers = list(headers) + ([("X-Queued", outcome["id"])] if outcome["id"] else
                                   [("X-Printed", "1")] if outcome["printed"] else [])
        if outcome["why"]:  # percent-encoded: a header may only carry latin-1
            headers.append(("X-Why", quote(outcome["why"], safe="")))
        self.reply(200, png, "image/png", headers)
        return outcome

    def do_POST(self):
        if not self.home_only():
            return
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        layout.HEADER.off = q.get("header") == "0"  # ?header=0: no icon and timestamp on top, for this request
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_UPLOAD:
            return self.json({"error": "Filen er for stor (maks 25 MB)."}, 413)
        body = self.rfile.read(n)
        try:
            if url.path == "/api/ask":
                d = json.loads(body)
                m, key = self.model(d)
                self.json(answer_json(*agent.ask(d["q"], use=m, api_key=key), model=m))
            elif url.path == "/api/heard":  # the question box, dumb first: a receipt the rules can make, else 204
                text = json.loads(body)["q"]
                hit = router.receipt(text, CARDS)
                if not hit:
                    self.send_response(204)
                    self.end_headers()
                    return
                name = label_for("card", hit[0])
                self.receipt(hit[1], q, label=name, source={"text": text}, headers=[("X-Card", quote(name, safe=""))])
            elif url.path == "/api/voice":  # a transcript from any speech pipeline, routed like the handset's
                d = json.loads(body)
                r = router.route(d["text"], CARDS, call=bool(d.get("call")))
                wanted = "image" in r and d.get("print", True)
                outcome = {"printed": False, "id": None, "why": None}
                if wanted:
                    _, outcome = render(r.pop("image"), True, label=label_for(r["kind"], r.get("cmd")),
                                        source={"text": d["text"], "call": bool(d.get("call"))})
                self.json({k: v for k, v in r.items() if k != "image"}
                          | {"printed": bool(wanted) and outcome["printed"], "queued": outcome["id"]})
            elif url.path == "/api/preset":  # a wished-for button: its prompt, asked like a question
                d = json.loads(body)
                p, (m, key) = presets.get(d["id"]), self.model(d)
                self.json(answer_json(*agent.ask(p["prompt"], use=m, api_key=key), icon=p["icon"], model=m))
            elif url.path == "/api/wish":  # "ønsk deg en kvittering": Claude designs a preset, if it can
                key = self.headers.get("X-Api-Key") or None
                if self.online() and not key:  # designing one always takes a paid model
                    raise NeedsKey("Å lage en ny knapp bruker Claude, som koster penger. Utenfra kjører det på "
                                   "din egen API-nøkkel: legg den inn under modellvelgeren.")
                wish = json.loads(body)["text"]
                reply, preset, dollars = agent.design_preset(wish, api_key=key)
                if preset:  # it waits for the admin; a GitHub issue tells them, if there's a token for one
                    preset["issue"] = file_issue(preset, wish)
                self.json({"reply": reply, "preset": preset, "cost": dollars, "spent": agent.spent})
            elif url.path == "/api/esp":  # change the phone base's settings: the admin's job, they include WiFi
                if not self.admin():
                    return self.json({"error": "Bare admin kan endre telefonen."}, 403)
                self.json(esp_post(json.loads(body)))
            elif url.path == "/api/wishes":  # the admin's verdict on a wished-for button: {approve: id} or {reject: id}
                if not self.admin():
                    return self.json({"error": "Bare admin kan godkjenne ønsker."}, 403)
                d = json.loads(body)
                decide(d.get("approve") or d.get("reject"), approved="approve" in d)
                self.json(wishes_json(True))
            elif url.path == "/api/daily":  # switch the daily prints on/off, pick the language or the time
                daily.update(json.loads(body))
                self.json({"settings": daily.settings(), "done": daily.done()})
            elif url.path == "/api/queue":
                self.json(queue_post(q, body))
            elif url.path == "/api/card/word":  # ?lang=ko: today's word, cached, so previews are free
                self.receipt(word_card(q.get("lang") or daily.settings()["lang"]), q, label="dagens ord")
            elif url.path == "/api/presets":
                presets.remove(json.loads(body)["remove"])
                self.json(presets.items())
            elif url.path == "/api/model":  # the server's own: the handset's, and the page's at home
                if self.online():
                    return self.json({"error": "Utenfra velger du modell bare for denne enheten."}, 403)
                agent.set_model(json.loads(body)["model"])
                self.json({"model": agent.model})
            elif url.path == "/api/shopping":  # the shared list: {text} adds, {remove: [ids]} removes
                d = json.loads(body)
                if d.get("remove"):
                    shoplist.remove(d["remove"])
                elif d.get("from_recipe"):  # a recipe's ingredients onto the shared list
                    for t in layout.ingredients(d["from_recipe"]):
                        shoplist.add(t)
                elif d.get("text"):
                    for t in d["text"].split(","):
                        if t.strip():
                            shoplist.add(t)
                self.json(shoplist.items())
            elif url.path == "/api/text":  # everything printed from text: answers, recipes, lists
                d = json.loads(body)
                # {recipe: its name, photo: true}: a recipe from the collection, with its photo if it has one
                dish = d.get("photo") and d.get("recipe") and cookbook.photo(d["recipe"])
                if d.get("kind") == "short":
                    return self.receipt(layout.recipe_short(d["a"], photo=dish), q, label="oppskrift (kort)",
                                        source={"q": d.get("q", ""), "kind": "short"})
                if d.get("kind") == "shopping":
                    outcome = self.receipt(layout.recipe(d["a"], icon="shopping"), q, label="handleliste",
                                           source=d.get("ids"))
                    if outcome["printed"]:  # only once it's really on paper: "printet" on the page
                        shoplist.mark_printed(d.get("ids", []))
                    return
                answer = layout.recipe(d["a"], photo=dish) if layout.is_recipe(d["a"]) else layout.answer(
                    d["q"], d["a"], icon=d.get("icon", "question"))
                self.receipt(answer, q, label="oppskrift" if layout.is_recipe(d["a"]) else "svar",
                             source={"q": d.get("q", "")})
            elif url.path == "/api/recipes":  # a recipe typed on the page, or an edited one saved back
                d = json.loads(body)
                if d.get("name"):
                    cookbook.update(d["name"], d["markdown"])
                    self.json({"name": d["name"]})
                else:
                    self.json({"name": cookbook.add(d["markdown"])})
            elif url.path == "/api/photo":
                self.receipt(photo(body), q, dither=True, label="foto")
            elif url.path == "/api/image":  # the page's image editor: crops, rotations and drawings come back here
                img = ImageOps.exif_transpose(Image.open(io.BytesIO(body))).convert("L")
                if img.width != layout.DOTS:  # rotated or drawn at another size: fit the paper width
                    img = img.resize((layout.DOTS, max(1, round(img.height * layout.DOTS / img.width))), Image.LANCZOS)
                self.receipt(img, q, dither=q.get("dither") == "1", label=q.get("label", "bilde"))
            elif url.path == "/api/card/departures":  # ?stop= picks a board from agent.STOPS, by index as the page sends it
                stop = int(q.get("stop", 0))
                if not 0 <= stop < len(agent.STOPS):
                    raise ValueError(f"No stop number {stop}")
                self.receipt(CARDS["departures"](stop=agent.STOPS[stop][0], mode=agent.STOPS[stop][1]), q,
                             label="avganger")
            elif url.path.startswith("/api/card/") and url.path[10:] in CARDS:
                self.receipt(CARDS[url.path[10:]](), q, label=label_for("card", url.path[10:]))
            else:
                self.json({"error": "not found"}, 404)
        except NeedsKey as e:
            self.json({"error": str(e), "need_key": True}, 401)
        except anthropic.AuthenticationError as e:
            if not self.headers.get("X-Api-Key"):  # the flat's own key in .env is wrong: that's ours to fix
                return self.json({"error": f"{type(e).__name__}: {e}"}, 500)
            self.json({"error": "API-nøkkelen ble ikke godtatt. Sjekk den på console.anthropic.com.",
                       "need_key": True}, 401)
        except Exception as e:  # one bad request shouldn't take the server down; tell the page what broke
            self.json({"error": f"{type(e).__name__}: {e}"}, 500)

    def log_message(self, *args):  # quiet; the phone loop owns the terminal
        pass


def banner():
    """The address plus a terminal QR code, for a headless Pi where the page's own QR isn't visible."""
    code = qrcode.QRCode(border=1)
    code.add_data(URL)
    buf = io.StringIO()
    code.print_ascii(out=buf)
    art = buf.getvalue()
    try:
        art.encode(sys.stdout.encoding or "ascii")
    except UnicodeEncodeError:  # e.g. a Windows console on cp1252 has no block characters: address only
        art = ""
    return f"{art}web page: {URL}  (Windows: allow python through the firewall so phones can reach it)"


def daily_job(job, settings):
    """What daily.run prints: the day's art, or the word of the day in the chosen language.

    Goes straight to the printer and lets a refusal out on purpose: daily.run retries every minute until
    it goes out, and leaving a copy in the queue as well would only print it twice."""
    img = CARDS["art"]() if job == "art" else word_card(settings["lang"])
    if not PRINTER:
        raise ValueError("No printer configured: start with --printer <ip>")
    printer.send(printer.encode(layout.to_rows(img.convert("L"))), PRINTER)


def word_card(lang):
    return layout.word(words.today(lang), words.label(lang))


def tunnel(name):
    """Run the Cloudflare tunnel `name` (set up with cloudflared, see the README) alongside the server; it
    stops when the server does."""
    import atexit
    import shutil
    import subprocess
    exe = (shutil.which("cloudflared") or next((str(p) for p in (Path.home() / ".local" / "bin" / "cloudflared",)
                                                if p.exists()), None) or str(Path.home() / "bin" / "cloudflared.exe"))
    proc = subprocess.Popen([exe, "tunnel", "run", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    atexit.register(proc.terminate)
    return proc


def start(port=PORT, printer_ip=None, tunnel_name=None):
    """Serve in a background thread on all interfaces, so phones on the WiFi can reach it. With a printer,
    also runs the daily prints (art, word of the day), switched on and off from the page, and a worker that
    works through anything left in the queue. With a tunnel name, also the Cloudflare tunnel that makes it
    reachable from home despite the router's AP isolation."""
    global PRINTER, URL
    PRINTER, URL = printer_ip, PUBLIC_URL or f"http://{lan_ip()}:{port}"
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    if printer_ip:
        threading.Thread(target=daily.run, args=(daily_job,), daemon=True).start()
        threading.Thread(target=printq.drain, args=(PRINTER,), daemon=True).start()
    if tunnel_name:
        tunnel(tunnel_name)
    if issues.enabled():
        threading.Thread(target=keep_syncing_wishes, daemon=True).start()
    return server


if __name__ == "__main__":
    import argparse
    import time
    p = argparse.ArgumentParser()
    p.add_argument("--printer", help="printer IP; omit for previews only")
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--tunnel", help="also run this Cloudflare tunnel, e.g. weatherboy")
    a = p.parse_args()
    # never crash on a character the console can't show; flush each line, so a service's log is live
    sys.stdout.reconfigure(errors="replace", line_buffering=True)
    start(a.port, a.printer, a.tunnel)
    print(banner())
    while True:
        time.sleep(3600)
