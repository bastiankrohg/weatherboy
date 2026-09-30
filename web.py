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
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import qrcode
from PIL import Image, ImageEnhance, ImageOps

import agent
import cookbook
import layout
import printer

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
    return {"top": int(q.get("top", 0)), "bottom": int(q["bottom"]) if "bottom" in q else None,
            "brightness": clamp(float(q.get("brightness", 1)), 0.2, 3.0),
            "contrast": clamp(float(q.get("contrast", 1)), 0.2, 4.0)}


def render(img, do_print, top=0, bottom=None, brightness=1.0, contrast=1.0, dither=False):
    """Every receipt goes through here: grayscale page -> brightness/contrast -> crop -> threshold (text) or
    Atkinson dither (pictures) -> printer rows. Returns the PNG of exactly what the head fires; prints it too
    when do_print. On text, brightness moves the threshold across the antialiased edges: brighter prints
    thinner strokes, darker prints bolder ones."""
    img = img.convert("L")
    if brightness != 1:
        img = ImageEnhance.Brightness(img).enhance(brightness)
    if contrast != 1:
        img = ImageEnhance.Contrast(img).enhance(contrast)
    bottom = img.height if bottom is None else min(max(bottom, 1), img.height)
    img = img.crop((0, min(max(top, 0), bottom - 1), img.width, bottom))
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
    def reply(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def json(self, obj, code=200):
        self.reply(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self):
        path = urlparse(self.path).path
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
        elif path == "/api/recipe-template":
            tpl = cookbook.ROOT / "_mal.md"
            self.json({"markdown": tpl.read_text(encoding="utf-8") if tpl.exists() else
                       "---\nporsjoner: 4\ntid: \ntags: \nkilde: \n---\n\n# \n\n## Ingredienser\n\n- \n\n## Slik gjør du\n\n1. \n"})
        elif path == "/api/stops":
            self.json([{"name": n, "mode": m} for n, m in agent.STOPS])
        elif path == "/api/models":
            self.json({"model": agent.model, "spent": agent.spent,
                       "models": [{"id": k, "name": v["name"], "price": v["price"]} for k, v in agent.MODELS.items()]})
        else:
            self.json({"error": "not found"}, 404)

    def do_POST(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_UPLOAD:
            return self.json({"error": "Filen er for stor (maks 25 MB)."}, 413)
        body, pr = self.rfile.read(n), q.get("print") == "1"
        try:
            adj = edits(q)
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
            elif url.path == "/api/answer":
                d = json.loads(body)
                img = (layout.recipe(d["a"]) if layout.is_recipe(d["a"])
                       else layout.answer(d["q"], d["a"], icon=d.get("icon", "question")))
                self.reply(200, render(img, pr, **adj), "image/png")
            elif url.path == "/api/recipes":  # a recipe typed on the page
                self.json({"name": cookbook.add(json.loads(body)["markdown"])})
            elif url.path == "/api/photo":
                self.reply(200, render(photo(body), pr, **adj, dither=True), "image/png")
            elif url.path == "/api/recipe":
                self.reply(200, render(layout.recipe(cookbook.read_recipe(q["name"])), pr, **adj), "image/png")
            elif url.path == "/api/card/departures":  # ?stop= picks a board from agent.STOPS
                stop = int(q.get("stop", 0))
                if not 0 <= stop < len(agent.STOPS):
                    raise ValueError(f"No stop number {stop}")
                self.reply(200, render(CARDS["departures"](stop), pr, **adj), "image/png")
            elif url.path.startswith("/api/card/") and url.path[10:] in CARDS:
                self.reply(200, render(CARDS[url.path[10:]](), pr, **adj), "image/png")
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


def start(port=PORT, printer_ip=None):
    """Serve in a background thread on all interfaces, so phones on the WiFi can reach it."""
    global PRINTER, URL
    PRINTER, URL = printer_ip, f"http://{lan_ip()}:{port}"
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
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
