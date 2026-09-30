"""Weatherboy's web page: every receipt, Claude, recipes and photo printing from a browser on the LAN.

    python web.py --printer 192.168.0.217      just the web page (main.py starts it too, see --web)

Every endpoint that makes a receipt returns its PNG preview, decoded from the actual job bytes. Printing is
?print=1 on the same request, so the page previews first and prints as a second, deliberate tap.
ponytail: no login. Anyone on the LAN can print and spend API credit. Add a shared password if the flat grows.
"""
import io
import json
import socket
import sys
import os
import threading
import time
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import qrcode
from PIL import Image, ImageEnhance, ImageOps

import agent
import cookbook
import layout
import printer
import shoplist

PORT = 8615
MAX_UPLOAD = 25_000_000  # bytes; a big phone photo is ~10 MB
PAGE = Path(__file__).with_name("web.html")
PRINTER = None           # printer IP, set by start(); None = previews only
URL = f"http://127.0.0.1:{PORT}"  # this page's LAN address, set by start()


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

    def do_GET(self):
        try:
            self._get(urlparse(self.path).path)
        except Exception as e:  # e.g. an unknown recipe name: tell the page instead of dropping the connection
            self.json({"error": f"{type(e).__name__}: {e}"}, 500)

    def _get(self, path):
        if path == "/":
            self.reply(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/status":
            self.json(status())
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
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_UPLOAD:
            return self.json({"error": "Filen er for stor (maks 25 MB)."}, 413)
        body = self.rfile.read(n)
        try:
            if url.path == "/api/ask":
                answer, dollars = agent.ask(json.loads(body)["q"])
                icon, answer = layout.theme(answer)  # the "tema: x" line becomes the receipt's icon
                self.json({"answer": answer, "icon": icon, "cost": dollars, "spent": agent.spent,
                           "model": agent.MODELS[agent.model]["name"],
                           # the recipe it read or drafted, so the page shows that card rather than a short note
                           "recipe": cookbook.last and cookbook.last.relative_to(cookbook.ROOT).with_suffix("").as_posix()})
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


ART_AT = os.environ.get("WEATHERBOY_ART_AT", "12:00")  # daily art print; empty turns it off
ART_DONE = Path(__file__).with_name("data") / "dagens_kunst.json"
QUIET_FROM = (22, 0)  # no scheduled prints after this; pos_printer.md's quiet hours start 22:30


def art_due(now, at, done):
    """Print today's art? From `at` until quiet hours, unless it's already on paper today."""
    if not at or done == now.date().isoformat():
        return False
    hh, mm = map(int, at.split(":"))
    return (hh, mm) <= (now.hour, now.minute) < QUIET_FROM


def daily_art(at):
    """Every minute: if the day's art is due, print it. A printer that's off, busy or out of paper just means
    trying again next minute, until quiet hours; the date it went out is kept across restarts."""
    waiting = None
    while True:
        now = datetime.now()
        done = json.loads(ART_DONE.read_text(encoding="utf-8"))["date"] if ART_DONE.exists() else None
        if PRINTER and art_due(now, at, done):
            try:
                render(CARDS["art"](), True)
                ART_DONE.parent.mkdir(exist_ok=True)
                ART_DONE.write_text(json.dumps({"date": now.date().isoformat()}), encoding="utf-8")
                print(f"dagens kunst printed {now:%H:%M}")
            except (OSError, printer.PrinterError) as e:
                if waiting != now.date():  # say it once a day, not every minute
                    print(f"dagens kunst: waiting for the printer ({e})")
                    waiting = now.date()
        time.sleep(60)


def start(port=PORT, printer_ip=None):
    """Serve in a background thread on all interfaces, so phones on the WiFi can reach it. With a printer,
    also runs the daily art print."""
    global PRINTER, URL
    PRINTER, URL = printer_ip, f"http://{lan_ip()}:{port}"
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    if printer_ip and ART_AT:
        threading.Thread(target=daily_art, args=(ART_AT,), daemon=True).start()
    return server


if __name__ == "__main__":
    import argparse
    import time
    p = argparse.ArgumentParser()
    p.add_argument("--printer", help="printer IP; omit for previews only")
    p.add_argument("--port", type=int, default=PORT)
    a = p.parse_args()
    sys.stdout.reconfigure(errors="replace")  # never crash on a character the console can't show
    start(a.port, a.printer)
    print(banner())
    while True:
        time.sleep(3600)
