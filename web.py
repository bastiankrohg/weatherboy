"""Weatherboy's web page: every receipt, Claude, recipes and photo printing from a browser on the LAN.

    python web.py --printer 192.168.0.217      just the web page (main.py starts it too, see --web)

Every endpoint that makes a receipt returns its PNG preview, decoded from the actual job bytes. Printing is
?print=1 on the same request, so the page previews first and prints as a second, deliberate tap.
ponytail: no login. Anyone on the LAN can print and spend API credit. Add a shared password if the flat grows.
"""
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
from urllib.parse import parse_qs, urlparse

import anthropic
import qrcode
import requests
from PIL import Image, ImageEnhance, ImageOps

import agent
import cookbook
import daily
import home
import layout
import local
import presets
import printer
import router
import shoplist
import websearch
import words

PORT = 8615
MAX_UPLOAD = 25_000_000  # bytes; a big phone photo is ~10 MB
PAGE = Path(__file__).with_name("web.html")
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

CARDS = {  # the keyword receipts, shared with main.py
    "weather": lambda: layout.weather(agent.PLACE, agent.forecast()),
    "departures": lambda stop=0: layout.departures(*agent.calls(agent.STOPS[stop][0], 10, agent.STOPS[stop][1])),
    "flights": lambda: layout.radar(agent.PLACE, agent.LAT, agent.LON, agent.aircraft(radius_km=40), 40),
    "art": lambda: layout.maze(date.today()),
    "qr": lambda: layout.qr(URL),
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


def render(img, do_print, brightness=1.0, contrast=1.0, dither=False):
    """Every receipt goes through here: grayscale page -> brightness/contrast -> threshold (text) or Atkinson
    dither (pictures) -> printer rows. Returns the PNG of exactly what the head fires; prints it too when
    do_print. On text, brightness moves the threshold across the antialiased edges: brighter prints thinner
    strokes, darker prints bolder ones. Crops, rotations and drawings happen in the page's editor (/api/image)."""
    img = img.convert("L")
    if brightness != 1:
        img = ImageEnhance.Brightness(img).enhance(brightness)
    if contrast != 1:
        img = ImageEnhance.Contrast(img).enhance(contrast)
    if dither:
        img = layout.dither(img)
    job = printer.encode(layout.to_rows(img))
    if do_print:
        if not PRINTER:
            raise ValueError("No printer configured: start with --printer <ip>")
        printer.send(job, PRINTER)
    buf = io.BytesIO()
    layout.from_rows(printer.decode(job)).save(buf, "PNG")
    return buf.getvalue()


def answer_json(answer, dollars, icon=None):
    """What the page needs from one of Claude's answers."""
    theme, answer = layout.theme(answer)  # the "tema: x" line becomes the receipt's icon
    return {"answer": answer, "icon": icon or theme, "cost": dollars, "spent": agent.spent,
            "model": agent.MODELS[agent.model]["name"],
            # the recipe it read or drafted, so the page shows that card rather than a short note
            "recipe": cookbook.last and cookbook.last.relative_to(cookbook.ROOT).with_suffix("").as_posix()}


def printer_here():
    """Is this machine on the printer's network, i.e. at home? A quick knock on the printer's port."""
    if not PRINTER:
        return True  # no printer configured: the network we're on is all we have to go by
    try:
        socket.create_connection((PRINTER, 9100), timeout=2).close()
        return True
    except OSError:
        return False


def status():
    """green: ready. yellow: on the network but won't print. red: can't reach it. off: no printer configured."""
    if not PRINTER:
        return {"state": "ingen skriver valgt", "level": "off"}
    try:
        asb = printer.status(PRINTER)
        if not asb:
            return {"state": "opptatt, svarer ikke på status", "level": "yellow"}
        bad = printer.problems(asb)
        return {"state": ", ".join(bad) or "klar", "level": "yellow" if bad else "green"}
    except ConnectionRefusedError:  # what this printer does with its cover open
        return {"state": "avviser tilkobling (lokket åpent?)", "level": "yellow"}
    except OSError:
        return {"state": f"svarer ikke på {PRINTER}", "level": "red"}


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


class Handler(BaseHTTPRequestHandler):
    def reply(self, code, body, ctype, headers=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
            self.json({"markdown": cookbook.read_recipe(parse_qs(urlparse(self.path).query)["name"][0])})
        elif path == "/api/recipe-template":
            tpl = cookbook.ROOT / "_mal.md"
            self.json({"markdown": tpl.read_text(encoding="utf-8") if tpl.exists() else
                       "---\nporsjoner: 4\ntid: \ntags: \nkilde: \n---\n\n# \n\n## Ingredienser\n\n- \n\n## Slik gjør du\n\n1. \n"})
        elif path == "/api/shopping":
            self.json(shoplist.items())
        elif path == "/api/presets":
            self.json(presets.items())
        elif path == "/api/daily":
            self.json({"settings": daily.settings(), "done": daily.done(),
                       "langs": [{"id": k, "name": v[0]} for k, v in words.LANGS.items()]})
        elif path == "/api/stops":
            self.json([{"name": n, "mode": m} for n, m in agent.STOPS])
        elif path == "/api/models":
            self.json({"model": agent.model, "spent": agent.spent,
                       "models": [{"id": k, "name": v["name"], "price": v["price"]} for k, v in agent.MODELS.items()]})
        else:
            self.json({"error": "not found"}, 404)

    def receipt(self, img, q, dither=False):
        """Every receipt endpoint ends here. ?raw=1 hands the page's image editor the grayscale original
        (X-Dither says whether it's a picture); otherwise it's rendered, and printed with ?print=1."""
        if q.get("raw") == "1":
            buf = io.BytesIO()
            img.convert("L").save(buf, "PNG")
            return self.reply(200, buf.getvalue(), "image/png", [("X-Dither", "1" if dither else "0")])
        self.reply(200, render(img, q.get("print") == "1", **edits(q), dither=dither), "image/png")

    def do_POST(self):
        if not self.home_only():
            return
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_UPLOAD:
            return self.json({"error": "Filen er for stor (maks 25 MB)."}, 413)
        body = self.rfile.read(n)
        try:
            if url.path == "/api/ask":
                self.json(answer_json(*agent.ask(json.loads(body)["q"])))
            elif url.path == "/api/voice":  # a transcript from any speech pipeline, routed like the handset's
                d = json.loads(body)
                r = router.route(d["text"], CARDS, call=bool(d.get("call")))
                printed = "image" in r and d.get("print", True)
                if printed:
                    render(r.pop("image"), True)
                self.json({k: v for k, v in r.items() if k != "image"} | {"printed": bool(printed)})
            elif url.path == "/api/preset":  # a wished-for button: its prompt, asked like a question
                p = presets.get(json.loads(body)["id"])
                self.json(answer_json(*agent.ask(p["prompt"]), icon=p["icon"]))
            elif url.path == "/api/wish":  # "ønsk deg en kvittering": Claude designs a preset, if it can
                reply, preset, dollars = agent.design_preset(json.loads(body)["text"])
                self.json({"reply": reply, "preset": preset, "cost": dollars, "spent": agent.spent})
            elif url.path == "/api/daily":  # switch the daily prints on/off, pick the language or the time
                daily.update(json.loads(body))
                self.json({"settings": daily.settings(), "done": daily.done()})
            elif url.path == "/api/card/word":  # ?lang=ko: today's word, cached, so previews are free
                self.receipt(word_card(q.get("lang") or daily.settings()["lang"]), q)
            elif url.path == "/api/presets":
                presets.remove(json.loads(body)["remove"])
                self.json(presets.items())
            elif url.path == "/api/model":
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
                if d.get("kind") == "short":
                    return self.receipt(layout.recipe_short(d["a"]), q)
                if d.get("kind") == "shopping":
                    self.receipt(layout.recipe(d["a"], icon="shopping"), q)
                    if q.get("print") == "1":  # only once it's really on paper: "printet" on the page
                        shoplist.mark_printed(d.get("ids", []))
                    return
                self.receipt(layout.recipe(d["a"]) if layout.is_recipe(d["a"])
                             else layout.answer(d["q"], d["a"], icon=d.get("icon", "question")), q)
            elif url.path == "/api/recipes":  # a recipe typed on the page, or an edited one saved back
                d = json.loads(body)
                if d.get("name"):
                    cookbook.update(d["name"], d["markdown"])
                    self.json({"name": d["name"]})
                else:
                    self.json({"name": cookbook.add(d["markdown"])})
            elif url.path == "/api/photo":
                self.receipt(photo(body), q, dither=True)
            elif url.path == "/api/image":  # the page's image editor: crops, rotations and drawings come back here
                img = ImageOps.exif_transpose(Image.open(io.BytesIO(body))).convert("L")
                if img.width != layout.DOTS:  # rotated or drawn at another size: fit the paper width
                    img = img.resize((layout.DOTS, max(1, round(img.height * layout.DOTS / img.width))), Image.LANCZOS)
                self.receipt(img, q, dither=q.get("dither") == "1")
            elif url.path == "/api/card/departures":  # ?stop= picks a board from agent.STOPS
                stop = int(q.get("stop", 0))
                if not 0 <= stop < len(agent.STOPS):
                    raise ValueError(f"No stop number {stop}")
                self.receipt(CARDS["departures"](stop), q)
            elif url.path.startswith("/api/card/") and url.path[10:] in CARDS:
                self.receipt(CARDS[url.path[10:]](), q)
            else:
                self.json({"error": "not found"}, 404)
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
    """What daily.run prints: the day's art, or the word of the day in the chosen language."""
    render(CARDS["art"]() if job == "art" else word_card(settings["lang"]), True)


def word_card(lang):
    return layout.word(words.today(lang), words.label(lang))


def tunnel(name):
    """Run the Cloudflare tunnel `name` (set up with cloudflared, see the README) alongside the server; it
    stops when the server does."""
    import atexit
    import shutil
    import subprocess
    exe = shutil.which("cloudflared") or str(Path.home() / "bin" / "cloudflared.exe")
    proc = subprocess.Popen([exe, "tunnel", "run", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    atexit.register(proc.terminate)
    return proc


def start(port=PORT, printer_ip=None, tunnel_name=None):
    """Serve in a background thread on all interfaces, so phones on the WiFi can reach it. With a printer,
    also runs the daily prints (art, word of the day), switched on and off from the page. With a tunnel name,
    also the Cloudflare tunnel that makes it reachable from home despite the router's AP isolation."""
    global PRINTER, URL
    PRINTER, URL = printer_ip, PUBLIC_URL or f"http://{lan_ip()}:{port}"
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    if printer_ip:
        threading.Thread(target=daily.run, args=(daily_job,), daemon=True).start()
    if tunnel_name:
        tunnel(tunnel_name)
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
