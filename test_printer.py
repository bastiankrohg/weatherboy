"""python test_printer.py - offline checks, no printer or API key needed."""
from datetime import datetime

from PIL import Image

import layout
import printer

# encoder round-trips and the smoke test is well-formed
rows = printer.smoketest()
job = printer.encode(rows)
assert printer.decode(job) == rows
assert job.startswith(b"\x1b*rA\x1b*rP0\x00") and job.endswith(b"\x1b*rB")
assert job[len(printer.START):][:3] == b"b\x48\x00"  # 'b', n1=72, n2=0

# printer status (ASB): the real unit's reply when ready; set bits name the problem
assert printer.problems(bytes.fromhex("23 86 00 00 00 00 00 00 00 00 00")) == []
assert printer.problems(bytes.fromhex("23 86 20 00 00 08 00 00 00")) == ["cover open", "out of paper"]
assert printer.problems(b"") == []

# bit order: leftmost dot is the MSB, black is 1
img = Image.new("L", (576, 1), 255)
img.putpixel((0, 0), 0)
assert layout.to_rows(img) == [b"\x80" + bytes(71)]
assert layout.from_rows(layout.to_rows(img)).getpixel((0, 0)) == 0

# a receipt with wrapping, a heading, ascii art and norwegian letters renders 576 wide with ink
art = "```\n" + "\n".join(["  \\ | /  ", "-- (_) --", "  / | \\  "] * 2) + "\n```"
text = "# Sol på Sagene\nBlåbærsyltetøy " * 20 + "\n\n" + art + "\nhttps://example.com/" + "x" * 200
r = layout.answer("hvordan blir været i kveld?", text, datetime(2026, 9, 29, 18, 42))
assert r.width == 576 and r.height > 400
assert r.getextrema()[0] == 0
assert layout.wrap("a " * 500, layout.font(layout.SANS, 28)).__len__() > 5
assert all(layout.font(layout.SANS, 28).getlength(l) <= layout.COL for l in layout.wrap("x" * 300, layout.font(layout.SANS, 28)))
layout.to_rows(r)

# dither produces pure black/white at printer width
d = layout.dither(Image.linear_gradient("L").resize((64, 32)))
assert d.width == 576 and set(d.tobytes()) <= {0, 255}

# weather card, radar and the daily maze render; the maze is reproducible per date
from datetime import date, timedelta
t0 = datetime(2026, 9, 29, 18).astimezone()
fc = [{"time": t0 + timedelta(hours=i), "temp": 10 + (i % 7), "wind": 3.2, "symbol": "lightrain_night",
       "rain": 0.4 * (i % 3)} for i in range(24)]
assert layout.weather("Oslo", fc, t0).width == 576
planes = [{"callsign": "SAS78T", "alt": 594.0, "speed": 73.9, "heading": 55.2, "lat": 60.0, "lon": 10.8},
          {"callsign": "FAR", "alt": None, "speed": None, "heading": None, "lat": 61.5, "lon": 10.8}]
layout.to_rows(layout.radar("Oslo", 59.91, 10.75, planes, 40, t0))
deps = [{"time": t0 + timedelta(minutes=i), "realtime": i % 2 == 0, "mode": "tram", "line": str(10 + i),
         "dest": "Rikshospitalet via en veldig lang omvei gjennom hele byen", "platform": "C"} for i in range(5)]
layout.to_rows(layout.departures("Jernbanetorget", deps, t0))
m1, m2 = layout.maze(date(2026, 9, 30), t0), layout.maze(date(2026, 9, 30), t0)
assert m1.tobytes() == m2.tobytes() and m1.tobytes() != layout.maze(date(2026, 10, 1), t0).tobytes()

# keywords: short utterances only, fuzzy on long words, exact on short ones
from main import command
pw = ["pineapple", "ananas"]
cases = {"Whether.": "weather", "Været.": "weather", "Pineapple": "chat", "Ananas!": "chat", "Skriv ute!": "print",
         "Ha det bra.": "bye", "Print that": "print", "Fly": "flights", "Kunst": "art", "Neste trikken": "departures",
         "Hvordan blir været i Bergen i morgen?": None, "i byen": None, "Hei": None}
for said, want in cases.items():
    assert command(said, pw) == want, (said, command(said, pw), want)

# F615 LCD frames: page-major, one byte per column, LSB = top row of the page
import phone
img = Image.new("L", (128, 64), 255)
for xy in ((0, 0), (0, 7), (1, 8), (127, 63)):
    img.putpixel(xy, 0)
f = phone.frame(img)
assert len(f) == 1024 and f[0] == 0b1000_0001 and f[128 + 1] == 1 and f[1023] == 0x80 and sum(f) == 0x81 + 1 + 0x80
assert layout.lcd("Løftet!", "æøå " * 40).size == (128, 64)

# recipes: list, read, stay inside the collection, drafts never overwrite and always cite their source
import tempfile
from pathlib import Path
import cookbook
cookbook.ROOT = Path(tempfile.mkdtemp()).resolve()
(cookbook.ROOT / "README.md").write_text("# not a recipe", encoding="utf-8")
(cookbook.ROOT / "pannekaker.md").write_text("# Pannekaker\ntags: søtt\n\n## Ingredienser\n- 4 egg\n", encoding="utf-8")
assert cookbook.list_recipes() == "pannekaker | Pannekaker | søtt"
assert "4 egg" in cookbook.read_recipe("pannekaker") and cookbook.last.name == "pannekaker.md"
for bad in ("../test_printer", "..\\x", "README.x", "nope"):
    try:
        cookbook.read_recipe(bad)
        raise AssertionError(bad)
    except ValueError:
        pass
assert cookbook.save_draft("Fiskesuppe fra Bergen!", "# Fiskesuppe\n- torsk", "https://x.no/a") == "Saved as drafts/fiskesuppe-fra-bergen"
assert cookbook.save_draft("Fiskesuppe fra Bergen!", "# Fiskesuppe", "u") == "Saved as drafts/fiskesuppe-fra-bergen-2"
assert "kilde: https://x.no/a" in cookbook.read_recipe("drafts/fiskesuppe-fra-bergen")
assert "drafts/fiskesuppe-fra-bergen | Fiskesuppe |  | draft" in cookbook.list_recipes().splitlines()
assert cookbook.read_recipe("drafts/fiskesuppe-fra-bergen").startswith("---\nkilde: https://x.no/a\n---\n\n# Fiskesuppe")
cookbook.save_draft("Suppe", "---\ntid: 1 t\n---\n# Suppe", "https://y.no")
assert cookbook.read_recipe("drafts/suppe").startswith("---\nkilde: https://y.no\ntid: 1 t\n---")
(cookbook.ROOT / "_mal.md").write_text("# Mal\ntags: x", encoding="utf-8")
assert "_mal" not in cookbook.list_recipes()

# recipe format: front matter, groups, steps; prints as a kitchen card
meta, title, intro, sections = layout.parse_recipe(
    "---\nporsjoner: 4\ntid: 45 min\n---\n\n# Pannekaker\n\nTynne.\n\n## Ingredienser\n\n- 4 egg\n### Til servering\n"
    "- syltetøy\n\n## Slik gjør du\n\n1. Visp.\n2) Stek.\n\n## Tips\nRøren holder.")
assert meta == {"porsjoner": "4", "tid": "45 min"} and title == "Pannekaker" and intro == ["Tynne."]
assert sections == [("Ingredienser", [("item", "4 egg"), ("sub", "Til servering"), ("item", "syltetøy")]),
                    ("Slik gjør du", [("step", "Visp."), ("step", "Stek.")]), ("Tips", [("p", "Røren holder.")])]
assert layout.parse_recipe("# Uten front matter\n## Ingredienser\n- salt")[1] == "Uten front matter"
assert layout.is_recipe("# X\n\n## Ingredienser\n- a") and not layout.is_recipe("# Været\nIngredienser: sol")
for recipe in (Path(__file__).parent / "recipes").glob("*.md"):  # the real collection, template included
    layout.to_rows(layout.recipe(recipe.read_text(encoding="utf-8"), t0))

# web page helpers: phone photos come out upright, previews are PNGs, no printer means no printing
import io
import web
ph = Image.linear_gradient("L").resize((800, 600)).convert("RGB")
exif = ph.getexif()
exif[0x0112] = 6  # stored landscape, shot portrait
buf = io.BytesIO()
ph.save(buf, "JPEG", exif=exif)
pic = web.photo(buf.getvalue())
assert pic.size == (576, 72 + 768 + 8) and pic.getpixel((layout.M + 22, 24)) == 0  # upright, camera hump inked
assert Image.open(io.BytesIO(web.render(layout.maze(date(2026, 9, 30), t0), False))).width == 576
import qrcode
lbl = layout.qr("http://192.168.0.216:8615", t0)
code = qrcode.QRCode(border=0)
code.add_data("http://192.168.0.216:8615")
n = len(code.get_matrix())
x = (576 - n * 14) // 2 + 3  # through the QR's left finder pattern, left of the cloud
top = next(y for y in range(lbl.height) if lbl.getpixel((x, y)) == 0)
assert lbl.width == 576 and all(lbl.getpixel((x, top + i)) == 0 for i in range(7 * 14))  # finder: 7 black modules
assert web.status() == {"state": "ingen skriver valgt", "level": "off"} and web.lan_ip().count(".") == 3
assert web.recipes()[0]["name"] == "drafts/fiskesuppe-fra-bergen-2"  # cookbook.ROOT is the temp collection here
try:
    web.render(layout.maze(date(2026, 9, 30), t0), True)
    raise AssertionError("printed without a printer")
except ValueError as e:
    assert "No printer" in str(e)

# user-written recipes go straight into the collection; no title, no save
assert cookbook.add("# Negroni\ntags: cocktail\n\n## Ingredienser\n- gin") == "negroni"
assert cookbook.add("# Negroni\n") == "negroni-2" and "negroni | Negroni | cocktail" in cookbook.list_recipes()
try:
    cookbook.add("bare tekst")
    raise AssertionError("saved a recipe without a title")
except ValueError:
    pass

# themes: the model's "tema:" line picks the icon and is dropped from the text; recipes pick their own
assert layout.theme("tema: cocktail\n# Negroni") == ("cocktail", "# Negroni")
assert layout.theme("**Tema: Idea**\nLys er raskt.") == ("idea", "Lys er raskt.")
assert layout.theme("tema: dinosaur\nx") == ("question", "tema: dinosaur\nx") and layout.theme("Hei") == ("question", "Hei")
top = lambda img: img.crop((layout.M, 50, layout.M + 50, 95)).tobytes()  # the icon's corner of the header
assert top(layout.answer("q", "tema: cocktail\nx", t0)) == top(layout.answer("q", "x", t0, icon="cocktail"))
assert top(layout.answer("q", "x", t0, icon="cocktail")) != top(layout.answer("q", "x", t0))
assert top(layout.recipe("---\ntags: cocktail\n---\n# N\n## Ingredienser\n- gin", t0)) == top(
    layout.answer("q", "x", t0, icon="cocktail"))
for name in layout.ICONS:
    layout.to_rows(layout.answer("q", "x", t0, icon=name))

# settings: .env fills in what the real environment doesn't set; no key gives a clear message
import os
import agent
env = Path(tempfile.mkdtemp()) / ".env"
env.write_text('# comment\nWB_TEST_A = "one"\nWB_TEST_B=two=2\nnot a setting\n', encoding="utf-8")
os.environ["WB_TEST_B"] = "from the shell"
agent.load_env(env)
assert os.environ["WB_TEST_A"] == "one" and os.environ["WB_TEST_B"] == "from the shell"
if not (agent.client.api_key or agent.client.auth_token):
    try:
        agent.ask("hei")
        raise AssertionError("asked Claude without a key")
    except RuntimeError as e:
        assert "ANTHROPIC_API_KEY" in str(e)

# models: Haiku by default, each model gets only settings it accepts; cost maths; switching starts fresh
from types import SimpleNamespace as NS
assert agent.model == os.environ.get("WEATHERBOY_MODEL", "claude-haiku-4-5")
hk, op = agent.params("claude-haiku-4-5"), agent.params("claude-opus-5-5")
assert "output_config" not in hk and "thinking" not in hk and "fallbacks" not in hk
assert {t["type"] for t in hk["tools"] if isinstance(t, dict)} == {"web_search_20250305", "web_fetch_20250910"}
assert agent.params("claude-sonnet-5-5", "medium")["output_config"] == {"effort": "medium"}
assert op["output_config"] == {"effort": "low"} and {t["type"] for t in op["tools"] if isinstance(t, dict)} == {
    "web_search_20260209", "web_fetch_20260209"}
u = NS(input_tokens=1_000_000, output_tokens=100_000, cache_creation_input_tokens=0, cache_read_input_tokens=1_000_000,
       server_tool_use=NS(web_search_requests=2))
assert abs(agent.cost("claude-haiku-4-5", u) - (1 + 0.5 + 0.1 + 0.02)) < 1e-9
agent.history = [{"role": "user", "content": "x"}]
agent.set_model("claude-sonnet-5-5")
assert agent.model == "claude-sonnet-5-5" and agent.history == []
agent.set_model("claude-haiku-4-5")
try:
    agent.set_model("gpt-9")
    raise AssertionError("accepted an unknown model")
except ValueError:
    pass

# the editor's round trip, through a real server: raw hands out the grayscale original with its halftone mode;
# an edited image (here rotated to landscape, so wider than the paper) comes back fitted to 576 dots
import json, threading, urllib.error, urllib.request
from http.server import ThreadingHTTPServer
srv = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
def call(path, body=b"", ctype="application/octet-stream"):
    r = urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{srv.server_port}{path}", data=body,
                                                      method="POST", headers={"Content-Type": ctype}))
    return r.headers, Image.open(io.BytesIO(r.read()))
hdr, raw = call("/api/photo?raw=1", buf.getvalue(), "image/jpeg")
assert hdr["X-Dither"] == "1" and raw.width == 576 and len(set(raw.tobytes())) > 2  # grayscale, not yet dithered
hdr, raw_txt = call("/api/card/art?raw=1")
assert hdr["X-Dither"] == "0"
# the text editor: recipes come out as text, go back in edited, and save over their file; bad names get an error
get = lambda path: json.loads(urllib.request.urlopen(f"http://127.0.0.1:{srv.server_port}{path}").read())
md = get("/api/recipe-text?name=negroni")["markdown"]
assert md.startswith("# Negroni")
_, card = call("/api/text", json.dumps({"q": "", "a": md + "\n- Campari"}).encode(), "application/json")
assert card.width == 576
cookbook.update("negroni", md + "\n- Campari")
assert "Campari" in get("/api/recipe-text?name=negroni")["markdown"]
try:
    get("/api/recipe-text?name=../agent")
    raise AssertionError("read outside the collection")
except urllib.error.HTTPError as e:
    assert e.code == 500 and "No recipe" in json.loads(e.read())["error"]
# the shared shopping list: no duplicates while unprinted, removal, "printet" only after a real print
import shoplist
shoplist.FILE = Path(tempfile.mkdtemp()) / "data" / "handleliste.json"
assert get("/api/shopping") == []
lst = json.loads(urllib.request.urlopen(urllib.request.Request(
    f"http://127.0.0.1:{srv.server_port}/api/shopping", data=json.dumps({"text": "Melk, brød,  melk "}).encode(),
    method="POST")).read())
assert [it["text"] for it in lst] == ["Melk", "brød"]
assert "løk" in shoplist.add_to_shopping_list("2 løk, ")
body = json.dumps({"kind": "shopping", "a": "# Handleliste\n\n## Varer\n\n- Melk\n", "ids": [1]}).encode()
_, lbl = call("/api/text", body, "application/json")  # preview: nothing marked
try:
    call("/api/text?print=1", body, "application/json")  # no printer configured: the print fails
except urllib.error.HTTPError as e:
    assert e.code == 500
assert all(it["printed"] is None for it in shoplist.items())
shoplist.mark_printed([1])
assert shoplist.items()[0]["printed"] and shoplist.add("melk")["id"] == 4  # printed Melk: a new one may go on
shoplist.remove([2, 3])

assert [it["id"] for it in shoplist.items()] == [1, 4]

# a recipe's ingredients onto the list (groups flattened); the short card is well under the full one
pk = ("---\nporsjoner: 4\ntid: 45 min\n---\n# Pannekaker\n## Ingredienser\n- 4 egg\n### Til servering\n- syltetøy\n"
      "## Slik gjør du\n1. Visp.\n2. Stek.\n## Tips\n- Rør godt.\n- Hvil røren.")
assert layout.ingredients(pk) == ["4 egg", "syltetøy"]
urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{srv.server_port}/api/shopping",
                                              data=json.dumps({"from_recipe": pk}).encode(), method="POST"))
assert {"4 egg", "syltetøy"} <= {it["text"] for it in shoplist.items()}
_, short = call("/api/text", json.dumps({"kind": "short", "a": pk}).encode(), "application/json")
assert short.width == 576 and short.height < layout.recipe(pk).height * 0.7
# the tunnel: visitors reported by Cloudflare get in only from the flat's own address; the LAN as before
import home, ipaddress, time as _time
home.FILE = Path(tempfile.mkdtemp()) / "hjemme.json"
home.public_ipv4 = lambda: ipaddress.ip_address("84.214.212.9")
home.ipv6_home_net = lambda: None
assert home.addresses(at_home=lambda: True)[0] == ipaddress.ip_address("84.214.212.9")  # at home: learned, saved
home._home.update(v4=None, at=0.0)
home.public_ipv4 = lambda: ipaddress.ip_address("193.157.162.12")  # the laptop is at the university now
assert home.addresses(at_home=lambda: False)[0] == ipaddress.ip_address("84.214.212.9")  # away: the saved home
assert not home.allowed("193.157.162.12", at_home=lambda: False)  # the university doesn't count as home
home._home.update(v4=ipaddress.ip_address("84.214.212.9"), v6=ipaddress.ip_network("2a02:fe0:c43e:4600::/56"),
                  at=_time.time())
assert home.allowed("84.214.212.9") and home.allowed("2a02:fe0:c43e:4601::abcd")
assert not home.allowed("8.8.8.8") and not home.allowed("2a02:fe0:c43e:4700::1") and not home.allowed("junk")
def visit(ip):
    try:
        return urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{srv.server_port}/api/url",
                                                             headers={"CF-Connecting-IP": ip})).status
    except urllib.error.HTTPError as e:
        return e.code
assert visit("84.214.212.9") == 200 and visit("203.0.113.7") == 403
home._home.update(v4=None, v6=None)  # home address unknown: tunnel visitors are refused, not waved through
assert visit("84.214.212.9") == 403 and get("/api/url")  # ...while the local network still works
home._home.update(at=0.0)
landscape = io.BytesIO()
raw.rotate(90, expand=True).save(landscape, "PNG")
_, back = call("/api/image?dither=1", landscape.getvalue(), "image/png")
assert back.width == 576 and back.height == round(576 * 576 / raw.height) and set(back.convert("L").tobytes()) <= {0, 255}
srv.shutdown()

# brightness/contrast reach every print: on text, darker = bolder strokes (more black dots), brighter = thinner
ink = lambda png: Image.open(io.BytesIO(png)).convert("L").histogram()[0]
txt = layout.answer("q", "Brødskive med brunost og syltetøy " * 6, t0)
assert ink(web.render(txt, False, brightness=0.5)) > ink(web.render(txt, False)) > ink(web.render(txt, False, brightness=1.6))
photo_png = web.render(pic, False, dither=True, contrast=1.8)  # photos stay grayscale until the final dither
assert set(Image.open(io.BytesIO(photo_png)).convert("L").tobytes()) <= {0, 255}
assert web.edits({"brightness": "99", "contrast": "0"}) == {"brightness": 3.0, "contrast": 0.2}
assert agent.STOPS[0] == ("Oslo S", "rail") if "WEATHERBOY_STOPS" not in os.environ else True

# stray **bold** from the model prints as plain words, not as asterisks and not dropped
assert layout.answer("q", "**Ordet:** mugga", t0).tobytes() == layout.answer("q", "Ordet: mugga", t0).tobytes()

# presets wished for on the page: data only; unknown icons fall back, removal works
import presets
presets.FILE = Path(tempfile.mkdtemp()) / "presets.json"
assert "Dagens ord" in presets.create_preset("  Dagens   ord ", "Gi meg et sjeldent norsk ord.", icon="dinosaur")
assert presets.last == {"id": 1, "name": "Dagens ord", "prompt": "Gi meg et sjeldent norsk ord.", "icon": "question"}
presets.create_preset("Månefase", "Hvilken månefase er det i kveld?", "idea")
assert [p["name"] for p in presets.items()] == ["Dagens ord", "Månefase"] and presets.get(2)["icon"] == "idea"
presets.remove(1)
assert [p["id"] for p in presets.items()] == [2]
try:
    presets.create_preset("", "x")
    raise AssertionError("created a preset without a name")
except ValueError:
    pass

# daily prints: due from their time until quiet hours, once a day; settings validated
import daily
from datetime import datetime as dt
assert not daily.due(dt(2026, 9, 30, 11, 59), "12:00", None)
assert daily.due(dt(2026, 9, 30, 12, 0), "12:00", None) and daily.due(dt(2026, 9, 30, 17, 30), "12:00", "2026-09-29")
assert not daily.due(dt(2026, 9, 30, 17, 30), "12:00", "2026-09-30")  # already printed today
assert not daily.due(dt(2026, 9, 30, 22, 0), "12:00", None)
daily.DATA = Path(tempfile.mkdtemp())
daily.SETTINGS, daily.DONE = daily.DATA / "daglig.json", daily.DATA / "daglig_utskrevet.json"
assert daily.settings()["lang"] == "ko" and daily.update({"lang": "fr", "word": False})["lang"] == "fr"
assert daily.settings() == {**daily.DEFAULTS, "lang": "fr", "word": False}
for bad in ({"lang": "xx"}, {"time": "25:00"}, {"rm": "-rf"}):
    try:
        daily.update(bad)
        raise AssertionError(bad)
    except ValueError:
        pass
(daily.DATA / "dagens_kunst.json").write_text('{"date": "2026-09-30"}')  # the old art-only record carries over
assert daily.done() == {"art": "2026-09-30"}

# word of the day: cached per language and day, remembers what it gave
import words
from datetime import date as _date
words.FILE = Path(tempfile.mkdtemp()) / "dagens_ord.json"
calls = []
words.generate = lambda lang, seen: calls.append(seen) or {
    "word": f"ord{len(calls)}", "reading": "", "kind": "noun", "meaning": "word", "example": "x",
    "example_reading": "", "example_meaning": "x", "note": ""}
assert words.today("ko")["word"] == "ord1" and words.today("ko")["word"] == "ord1" and len(calls) == 1
words.today("fr")
data = json.loads(words.FILE.read_text(encoding="utf-8"))
data["today"]["ko"]["date"] = "2000-01-01"  # yesterday's word: a new one, and the old one is remembered
words.FILE.write_text(json.dumps(data), encoding="utf-8")
assert words.today("ko")["word"] == "ord3" and calls[-1] == ["ord1"]
assert layout.word(words.today("ko"), words.label("ko")).width == 576
assert "Dagens tegn" in words.label("ja") and "Dagens ord" in words.label("ko")

import speak
assert speak.lang_of("Det blir tolv grader og lett regn.") == "no"
assert speak.lang_of("It will be twelve degrees and light rain.") == "en"

print("ok")
