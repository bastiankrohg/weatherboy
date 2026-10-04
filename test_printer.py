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
from router import command
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

# a dish's photo: the same name next to it, or what `bilde:` names; full width under the title, only if asked
assert cookbook.photo("pannekaker") is None
Image.new("L", (1200, 800), 128).save(cookbook.ROOT / "pannekaker.jpg")
assert cookbook.photo("pannekaker").name == "pannekaker.jpg"
(cookbook.ROOT / "drafts" / "bilder").mkdir()
Image.new("L", (400, 900), 90).save(cookbook.ROOT / "drafts" / "bilder" / "suppe.png")
(cookbook.ROOT / "drafts" / "suppe.md").write_text("---\nbilde: bilder/suppe.png\n---\n# Suppe\n\n## Ingredienser\n- vann\n",
                                                   encoding="utf-8")
assert cookbook.photo("drafts/suppe").name == "suppe.png"
(cookbook.ROOT / "drafts" / "ute.md").write_text("---\nbilde: ../../hemmelig.png\n---\n# Ute", encoding="utf-8")
assert cookbook.photo("drafts/ute") is None  # a photo must be inside the collection
text = cookbook.read_recipe("pannekaker")
plain, pictured = layout.recipe(text), layout.recipe(text, photo=cookbook.photo("pannekaker"))
assert pictured.height - plain.height >= 576 * 800 // 1200
assert set(pictured.crop((0, 200, 576, 500)).tobytes()) <= {0, 255}  # the photo is dithered already, text isn't
assert layout.recipe(text, photo=cookbook.photo("drafts/suppe")).height - plain.height < 576 + 40  # tall: cropped square
assert layout.recipe_short(text, photo=cookbook.photo("pannekaker")).height > layout.recipe_short(text).height
(cookbook.ROOT / "ødelagt.md").write_text("# Ødelagt\n", encoding="utf-8")
(cookbook.ROOT / "ødelagt.jpg").write_bytes(b"not a jpeg")
assert layout.recipe("# Ødelagt", photo=cookbook.photo("ødelagt")).height == layout.recipe("# Ødelagt").height
for p in ("ute.md", "suppe.md"):
    (cookbook.ROOT / "drafts" / p).unlink()
for p in ("ødelagt.md", "ødelagt.jpg"):
    (cookbook.ROOT / p).unlink()
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

# web page helpers: phone photos come out upright, previews are PNGs, no printer means the job waits
import io
import printq
import web
printq.DATA = Path(tempfile.mkdtemp())  # the queue keeps its PNGs and index; never the real ones
printq.PEDIA, printq.FILE = printq.DATA / "printq", printq.DATA / "printq.json"
ph = Image.linear_gradient("L").resize((800, 600)).convert("RGB")
exif = ph.getexif()
exif[0x0112] = 6  # stored landscape, shot portrait
buf = io.BytesIO()
ph.save(buf, "JPEG", exif=exif)
pic = web.photo(buf.getvalue())
assert pic.size == (576, 72 + 768 + 8) and pic.getpixel((layout.M + 22, 24)) == 0  # upright, camera hump inked
assert Image.open(io.BytesIO(web.render(layout.maze(date(2026, 9, 30), t0), False)[0])).width == 576
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

# no printer at all: nothing is printed and nothing is lost, it waits in the queue and says so
png, outcome = web.render(layout.maze(date(2026, 9, 30), t0), True, label="labyrint")
assert not outcome["printed"] and outcome["id"] and outcome["why"] == "ingen skriver er valgt"
assert [j["label"] for j in printq.items()] == ["labyrint"] and printq.count() == (1, 0)
assert web.status()["state"] == "ingen skriver valgt · 1 i kø"  # the count rides on the printer's light

# a printer that won't take it: the job stays in the queue, in place, and nothing is raised at the caller
def offline(data, host, port=9100):
    raise ConnectionRefusedError("avviser tilkobling (lokket åpent?)")


real_send = printer.send
printer.send = offline
assert printq.print_now("192.168.0.217") == 0  # a refusal isn't an error here, the job just waits
assert printq.count() == (1, 0) and printq.items()[0]["id"] == outcome["id"]  # still first, still there

# the queue is yours to rearrange: order, hold, and the PNG is the one that gets printed
png, second = web.render(layout.word({"word": "mugga", "meaning": "kopp"}, "norsk"), True, label="ord")
assert printq.count() == (2, 0)
printq.move(second["id"], -1)
assert [j["label"] for j in printq.items()] == ["ord", "labyrint"]  # moved up, the tail doesn't wrap
printq.move(second["id"], -1)  # already first: nothing happens
assert [j["label"] for j in printq.items()] == ["ord", "labyrint"]
printq.hold(second["id"], True)
assert printq.count() == (2, 1) and printq.items()[0]["hold"]
assert printq.print_now("192.168.0.217") == 0  # the head is held, so nothing goes out
printer.send = real_send
sent = []
printer.send = lambda data, host, port=9100: sent.append(data)
assert printq.print_now("192.168.0.217") == 1 and len(sent) == 1  # the held one waits, the one behind it goes
assert printq.count() == (1, 1) and printq.items()[0]["label"] == "ord"  # only the held job is left
printer.send = real_send

# edit a job's picture and it's the new picture that prints, same id, same place in the order
id = printq.items()[0]["id"]
edited = Image.new("L", (layout.DOTS, 40), 255)
edited.paste(0, (0, 0, 10, 10))
printq.replace_img(id, edited)
assert Image.open(io.BytesIO(printq.png_path(id).read_bytes())).size == (layout.DOTS, 40)
try:
    printq.replace_img("nope", edited)
    raise AssertionError("replaced a job that isn't there")
except KeyError:
    pass

# a PNG left behind by a crash goes when the index is next written, and a full queue refuses instead of dropping
printq.PEDIA.mkdir(parents=True, exist_ok=True)
(printq.PEDIA / "forlatt.png").write_bytes(b"")
printq.add(edited, "en til")
assert "forlatt.png" not in [p.name for p in printq.PEDIA.iterdir()] and printq.count() == (2, 1)
printq.remove([j["id"] for j in printq.items()])
assert printq.count() == (0, 0) and not list(printq.PEDIA.glob("*.png"))  # removed jobs leave no pictures
saved, printq.MAX = printq.MAX, 1
try:
    printq.add(edited, "den første")
    printq.add(edited, "den andre")  # one is the limit; taking it would silently drop the oldest
    raise AssertionError("took a job over the limit")
except ValueError as e:
    assert "full" in str(e)
finally:
    printq.MAX = saved
printq.remove([j["id"] for j in printq.items()])

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
assert layout.theme("tema: dinosaur\nx") == ("question", "x") and layout.theme("Hei") == ("question", "Hei")
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
# every module that reads a setting as it loads imports envfile first, so .env counts whatever loads first
import re as _re
for _f in Path(__file__).parent.glob("*.py"):
    _src = _f.read_text(encoding="utf-8")
    if _f.name not in ("envfile.py", "test_printer.py", "test_web_ui.py") and _re.search(r"^\S.*os\.environ", _src, _re.M):
        first = _re.search(r"^(?:import|from) (\w+)", _src, _re.M)[1]
        assert first == "envfile", f"{_f.name} reads the environment at import but imports {first} before envfile"
assert os.environ["WB_TEST_A"] == "one" and os.environ["WB_TEST_B"] == "from the shell"
if not (agent.client.api_key or agent.client.auth_token):
    try:
        agent.ask("hei", use="claude-haiku-4-5")
        raise AssertionError("asked Claude without a key")
    except RuntimeError as e:
        assert "ANTHROPIC_API_KEY" in str(e)

# models: the free local one by default, each model gets only settings it accepts; cost maths; switching starts fresh
from types import SimpleNamespace as NS
assert agent.model == os.environ.get("WEATHERBOY_MODEL", "local")
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
import json, threading, urllib.error, urllib.parse, urllib.request
from http.server import ThreadingHTTPServer
srv = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
def call(path, body=b"", ctype="application/octet-stream"):
    r = urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{srv.server_port}{path}", data=body,
                                                      method="POST", headers={"Content-Type": ctype}))
    return r.headers, Image.open(io.BytesIO(r.read()))
def post(path, body=b""):  # a JSON reply, the queue's and the shopping list's shape
    return json.loads(urllib.request.urlopen(urllib.request.Request(
        f"http://127.0.0.1:{srv.server_port}{path}", data=body, method="POST")).read())
def marked_to_png(img):
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()
hdr, raw = call("/api/photo?raw=1", buf.getvalue(), "image/jpeg")
assert hdr["X-Dither"] == "1" and raw.width == 576 and len(set(raw.tobytes())) > 2  # grayscale, not yet dithered
hdr, raw_txt = call("/api/card/art?raw=1")
assert hdr["X-Dither"] == "0"
hdr, _ = call("/api/card/art")  # a plain preview: rendered, but nothing asked of the printer and nothing queued
assert "X-Printed" not in hdr and "X-Queued" not in hdr and printq.count() == (0, 0)
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
_, lbl = call("/api/text", body, "application/json")  # preview: nothing queued, nothing marked
assert printq.count() == (0, 0) and all(it["printed"] is None for it in shoplist.items())
hdr, lbl = call("/api/text?print=1", body, "application/json")  # no printer: it's kept, not lost, not an error
assert hdr["X-Queued"] and urllib.parse.unquote(hdr["X-Why"]) == "ingen skriver er valgt" and "X-Printed" not in hdr
assert printq.items()[0]["label"] == "handleliste" and printq.count() == (1, 0)
assert all(it["printed"] is None for it in shoplist.items())  # it isn't on paper yet, so not marked
shoplist.mark_printed([1])
assert shoplist.items()[0]["printed"] and shoplist.add("melk")["id"] == 4  # printed Melk: a new one may go on
shoplist.remove([2, 3])

assert [it["id"] for it in shoplist.items()] == [1, 4]

# the queue over HTTP: hold it to print later, look at it, reorder, edit its picture, drop it
id = printq.items()[0]["id"]
q = get("/api/queue")
assert q["count"] == 1 and q["held"] == 0 and q["items"][0]["id"] == id
assert set(q["items"][0]) == {"id", "label", "added", "hold", "dither", "source", "png"}  # what the page draws from
thumb = Image.open(io.BytesIO(urllib.request.urlopen(f"http://127.0.0.1:{srv.server_port}/api/queue/{id}.png").read()))
assert thumb.width == layout.DOTS and thumb.mode == "L"
try:
    urllib.request.urlopen(f"http://127.0.0.1:{srv.server_port}/api/queue/ukjent.png")
    raise AssertionError("served a picture for a job that isn't there")
except urllib.error.HTTPError as e:
    assert e.code == 404
q = post("/api/queue?hold=" + id + "&on=1")
assert q["held"] == 1 and q["items"][0]["hold"]
marked = Image.new("L", (layout.DOTS, 30), 255)
marked.paste(0, (0, 0, 20, 20))
q = post(f"/api/queue?replace={id}", marked_to_png(marked))
assert Image.open(io.BytesIO(printq.png_path(id).read_bytes())).size == (layout.DOTS, 30) and q["count"] == 1
assert q["items"][0]["hold"] and q["items"][0]["label"]  # editing pixels left the job itself alone
q = post("/api/queue?move=" + id + "&delta=1")  # already the only one: nothing happens, no error
assert [j["id"] for j in q["items"]] == [id]
try:
    post("/api/queue?print=all")  # nothing to print to
    raise AssertionError("printed the queue with no printer")
except urllib.error.HTTPError as e:
    assert e.code == 500 and "Ingen skriver" in json.loads(e.read())["error"]
assert get("/api/queue")["count"] == 1  # and it's all still there
q = post("/api/queue?clear=1")
assert q["count"] == 0 and not list(printq.PEDIA.glob("*.png"))

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
_saved, home.FILE = home.FILE, home.FILE.with_name("ingen.json")
home._home.update(v4=None, at=0.0)
assert home.addresses(at_home=lambda: False)[0] is None  # the printer missed a knock, nothing saved yet...
assert _time.time() - home._home["at"] > home.REFRESH - home.RETRY - 1  # ...so it asks again soon, not in 5 min
home.FILE = _saved
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
ink = lambda out: Image.open(io.BytesIO(out[0])).convert("L").histogram()[0]
txt = layout.answer("q", "Brødskive med brunost og syltetøy " * 6, t0)
assert ink(web.render(txt, False, brightness=0.5)) > ink(web.render(txt, False)) > ink(web.render(txt, False, brightness=1.6))
photo_png = web.render(pic, False, dither=True, contrast=1.8)[0]  # photos stay grayscale until the final dither

# the page's "Topplinje" switch (?header=0): no icon and timestamp, and the paper that was theirs is saved
with_top, with_photo = layout.answer("hva?", "Et svar."), layout.photo(pic)
layout.HEADER.off = True
assert layout.answer("hva?", "Et svar.").height < with_top.height and layout.photo(pic).height == with_photo.height - 72
layout.HEADER.off = False

# copies (?copies=3): each its own job, a preview prints none, and a typo can't empty the roll
sent = []
printer.send = lambda data, host, port=9100: sent.append(data)
assert web.render(with_top, True, printer_ip="192.0.2.1", copies=3)[1]["printed"] and len(sent) == 3
web.render(with_top, False, copies=3)
assert len(sent) == 3 and web.copies({"copies": "999"}) == 20 and web.copies({}) == 1
printer.send = real_send
assert set(Image.open(io.BytesIO(photo_png)).convert("L").tobytes()) <= {0, 255}
assert web.edits({"brightness": "99", "contrast": "0"}) == {"brightness": 3.0, "contrast": 0.2}
assert agent.STOPS[0] == ("Oslo S", "rail") if "WEATHERBOY_STOPS" not in os.environ else True

# stray **bold** from the model prints as plain words, not as asterisks and not dropped
assert layout.answer("q", "**Ordet:** mugga", t0).tobytes() == layout.answer("q", "Ordet: mugga", t0).tobytes()

# presets wished for on the page: data only; unknown icons fall back, removal works
import presets
presets.FILE = Path(tempfile.mkdtemp()) / "presets.json"
assert "Dagens ord" in presets.create_preset("  Dagens   ord ", "Gi meg et sjeldent norsk ord.", icon="dinosaur")
assert presets.last == {"id": 1, "name": "Dagens ord", "prompt": "Gi meg et sjeldent norsk ord.", "icon": "question",
                        "pending": True}  # a wish waits for the admin
presets.create_preset("Månefase", "Hvilken månefase er det i kveld?", "idea")
assert presets.items() == [] and [p["name"] for p in presets.items(pending=True)] == ["Dagens ord", "Månefase"]
presets.approve(1)
presets.approve(2)
assert [p["name"] for p in presets.items()] == ["Dagens ord", "Månefase"] and presets.get(2)["icon"] == "idea"
assert "pending" not in presets.get(1)
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

# the local (free) model: an OpenAI-style server that first asks for a tool, then answers with its result
import local
from http.server import BaseHTTPRequestHandler
class FakeOllama(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def do_GET(self):  # /v1/models: "I'm here"
        self.send_response(200); self.end_headers(); self.wfile.write(b'{"data": []}')
    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert req["messages"][0]["role"] == "system" and any(t["function"]["name"] == "list_recipes" for t in req["tools"])
        last = req["messages"][-1]
        msg = ({"role": "assistant", "content": "tema: cooking\nDere har: " + last["content"]} if last["role"] == "tool"
               else {"role": "assistant", "content": "", "tool_calls": [
                   {"id": "c1", "type": "function", "function": {"name": "list_recipes", "arguments": "{}"}}]})
        body = json.dumps({"choices": [{"message": msg}]}).encode()
        self.send_response(200); self.end_headers(); self.wfile.write(body)
fake = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
threading.Thread(target=fake.serve_forever, daemon=True).start()
local.SERVERS = ["http://127.0.0.1:1/v1", f"http://127.0.0.1:{fake.server_port}/v1"]  # first one is down
agent.set_model("local")
answer, dollars = agent.ask("Hvilke oppskrifter har vi?")
assert dollars == 0 and "negroni" in answer and answer.startswith("tema: cooking")
local.SERVERS = ["http://127.0.0.1:1/v1"]
try:
    agent.ask("hei")
    raise AssertionError("answered without a local model")
except RuntimeError as e:
    assert "svarer ikke" in str(e)
agent.set_model("claude-haiku-4-5")
# the gate in front of a published Ollama: the same conversation gets through with the key, nothing else does
import gate
gate.KEY, gate.MODELS, gate.OLLAMA = "nøkkel-123", {local.MODEL}, f"http://127.0.0.1:{fake.server_port}"
gated = ThreadingHTTPServer(("127.0.0.1", 0), gate.Gate)
threading.Thread(target=gated.serve_forever, daemon=True).start()
local.SERVERS, local.KEY = [f"http://127.0.0.1:{gated.server_port}/v1"], "nøkkel-123"
agent.set_model("local")
answer, _ = agent.ask("Hvilke oppskrifter har vi?")
assert "negroni" in answer  # through the gate, tools and all
def gate_call(method, path, body=None, key="nøkkel-123"):
    h = {"Content-Type": "application/json"} | ({"Authorization": f"Bearer {key}"} if key else {})
    req = urllib.request.Request(f"http://127.0.0.1:{gated.server_port}{path}",
                                 json.dumps(body).encode() if body is not None else None, h, method=method)
    try:
        return urllib.request.urlopen(req).status
    except urllib.error.HTTPError as e:
        return e.code
assert gate_call("GET", "/v1/models") == 200
assert gate_call("GET", "/v1/models", key=None) == 401 and gate_call("GET", "/v1/models", key="gjett") == 401
assert gate_call("POST", "/api/pull", {"model": "llama3"}) == 404  # Ollama's own API: not through here
assert gate_call("GET", "/api/tags") == 404 and gate_call("DELETE", "/api/delete") == 405
assert gate_call("POST", "/v1/chat/completions", {"model": "llama3:70b", "messages": []}) == 403  # only ours
gate.KEY = ""
assert gate_call("GET", "/v1/models") == 401  # no key set up on the gate: nobody gets in
gated.shutdown()
local.KEY = ""
agent.set_model("claude-haiku-4-5")
fake.shutdown()

# the router: one transcript in, one decision out, the same for the handset and /api/voice
import router
asked = []
real_ask = agent.ask
agent.ask = lambda q, voice=False: asked.append((q, voice)) or ("tema: idea\nLys går fort.", 0.0)
fake_cards = {"weather": lambda **kw: layout.lcd("vær", w=576)}  # takes the arguments nlp works out
r = router.route("Været", fake_cards)
assert r["kind"] == "card" and r["cmd"] == "weather" and r["image"].width == 576 and not asked
assert router.route("ananas", fake_cards) == {"kind": "call", "cmd": "chat", "say": router.GREETING}
r = router.route("hvor fort går lyset", fake_cards)
assert r["kind"] == "answer" and r["text"] == "Lys går fort." and asked[-1] == ("hvor fort går lyset", False)
cookbook.last = None
assert router.route("skriv ut", fake_cards)["image"].tobytes() == r["image"].tobytes()  # reprints the last one
r = router.route("hvor fort går lyset", fake_cards, call=True)
assert r["kind"] == "answer" and "image" not in r and r["say"] == "Lys går fort." and asked[-1][1] is True
assert router.route("ha det", fake_cards, call=True)["kind"] == "hangup"
assert router.route("hm", fake_cards)["kind"] == "ignored"

# nlp reads a sentence into a card without a model, and what it works out reaches the receipt.
# The gazetteers and the fetches are stubbed: no network, and the exact hours are ours to assert.
import nlp
seen = {}
real = {(m, k): getattr(m, k) for m, k in ((agent, "forecast"), (agent, "calls"), (agent, "aircraft"),
                                           (layout, "weather"), (layout, "departures"), (layout, "radar"),
                                           (web, "word_card"), (nlp, "match"), (nlp, "geocode"), (nlp, "geocode_stop"))}
nlp.geocode = lambda name, capitalized=False: nlp.Place("Røros", 62.19, 10.44)
nlp.geocode_stop = lambda name: "Røros stasjon" if name.startswith("røros") else None
agent.forecast = lambda lat, lon, hours: seen.update(lat=lat, lon=lon, hours=hours) or []
agent.calls = lambda stop, count, mode: seen.update(stop=stop, count=count, mode=mode) or (stop, [])
agent.aircraft = lambda lat, lon, radius_km=30: []
layout.weather = lambda place, fc, when=None: seen.update(place=place, stamp=when) or layout.lcd(place, w=576)
layout.departures = lambda stop, deps, when=None: seen.update(place=stop, stamp=when) or layout.lcd(stop, w=576)
layout.radar = lambda place, lat, lon, planes, radius, when=None: seen.update(place=place, lat=lat) \
    or layout.lcd(place, w=576)
web.word_card = lambda lang: seen.update(lang=lang) or layout.lcd("ord", w=576)

n = len(asked)  # "hva blir været i Røros i morgen" is a weather card for Røros tomorrow: no model at all
r = router.route("hva blir været i Røros i morgen", web.CARDS)
assert r["kind"] == "card" and r["cmd"] == "weather" and r["image"].width == 576 and len(asked) == n
assert (seen["lat"], seen["lon"], seen["hours"]) == (62.19, 10.44, 18)  # Røros, tomorrow 06:00-24:00
assert seen["place"] == "Røros" and seen["stamp"].day != datetime.now().day  # ...and it's stamped

n = len(asked)
r = router.route("tog fra Røros", web.CARDS)  # a stop and a mode out of four words
assert r["kind"] == "card" and r["cmd"] == "departures" and len(asked) == n
assert seen["stop"] == "Røros stasjon" and seen["mode"] == "rail" and seen["count"] == 10

router.route("fly over Røros nå", web.CARDS)
assert seen["place"] == "Røros" and seen["lat"] == 62.19  # aircraft around the place it worked out

r = router.route("dagens ord på koreansk", web.CARDS)
assert r["kind"] == "card" and r["cmd"] == "word" and seen["lang"] == "ko" and len(asked) == n

# the two things nlp refuses to guess: it escalates, and the model gets its turn
r = router.route("hvorfor er himmelen blå", web.CARDS)
assert r["kind"] == "answer" and asked[-1] == ("hvorfor er himmelen blå", False) and len(asked) == n + 1
assert router.route("hva er 2+2", web.CARDS)["kind"] == "answer"  # no rule, so the model too

def down(*a, **k):
    raise ConnectionError("nede")


agent.forecast = down  # the fetch failing must not swallow the question
assert router.route("hva blir været i Røros i morgen", web.CARDS)["kind"] == "answer" and len(asked) == n + 3

nlp.match = lambda *a, **k: 1 / 0  # a parser that trips must not take the handset down with it
assert router.route("hva blir været i Røros", web.CARDS)["kind"] == "answer" and len(asked) == n + 4
nlp.match = real[(nlp, "match")]  # the rest of this file wants the real one

assert web.CARDS["departures"](stop=agent.STOPS[1][0], mode=agent.STOPS[1][1]).width == 576  # a name, not an index

srv2 = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
threading.Thread(target=srv2.serve_forever, daemon=True).start()
v = json.loads(urllib.request.urlopen(urllib.request.Request(
    f"http://127.0.0.1:{srv2.server_port}/api/voice", method="POST",
    data=json.dumps({"text": "hvor fort går lyset", "print": False}).encode())).read())
assert v["kind"] == "answer" and v["text"] == "Lys går fort." and v["printed"] is False and "image" not in v
# ?stop= is an index, the way the page sends it, and the card takes the name behind it
def call2(path, body=b"", ctype="application/octet-stream"):
    r = urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{srv2.server_port}{path}", data=body,
                                                      method="POST", headers={"Content-Type": ctype}))
    return r.headers, Image.open(io.BytesIO(r.read()))
seen.clear()
_, dep = call2("/api/card/departures?stop=1")
assert seen["stop"] == agent.STOPS[1][0] and seen["mode"] == agent.STOPS[1][1] and dep.width == layout.DOTS
try:
    call2("/api/card/departures?stop=99")
    raise AssertionError("served a board that isn't configured")
except urllib.error.HTTPError as e:
    assert e.code == 500 and "No stop number" in json.loads(e.read())["error"]
srv2.shutdown()
for (m, k), v in real.items():  # the gazetteers and the fetches back to themselves
    setattr(m, k, v)
agent.ask = real_ask

# the question box: rules first (a receipt, no model), then the model the device picked. Through the tunnel,
# Claude only runs on the visitor's own key, and the server's model is the flat's to change, not theirs
calls_made = []
agent.ask = lambda q, voice=False, use=None, api_key=None: calls_made.append((q, use, api_key)) or ("tema: idea\nJa.", 0.0)
srv3 = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
threading.Thread(target=srv3.serve_forever, daemon=True).start()
home._home.update(v4=ipaddress.ip_address("84.214.212.9"), v6=None, at=_time.time())
def ask3(path, body, tunnel=False, key=None):
    h = {"Content-Type": "application/json"} | ({"CF-Connecting-IP": "84.214.212.9"} if tunnel else {}) \
        | ({"X-Api-Key": key} if key else {})
    try:
        r = urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{srv3.server_port}{path}",
                                                          json.dumps(body).encode(), h, method="POST"))
        return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()
code, hdr, png = ask3("/api/heard", {"q": "qr"})  # a keyword: the receipt itself
assert code == 200 and urllib.parse.unquote(hdr["X-Card"]) == "qr-kode" and png[:4] == b"\x89PNG" and not calls_made
assert ask3("/api/heard", {"q": "hvorfor er himmelen blå"})[0] == 204  # nothing for the rules: the page asks the model
code, _, out = ask3("/api/ask", {"q": "hei", "model": "claude-opus-5-5"}, tunnel=True)
assert code == 401 and json.loads(out)["need_key"] and not calls_made  # paid, from outside, no key: never ours
assert ask3("/api/ask", {"q": "hei", "model": "local"}, tunnel=True)[0] == 200  # the free one is anyone's
assert ask3("/api/ask", {"q": "hei", "model": "claude-opus-5-5"}, tunnel=True, key="sk-ant-theirs")[0] == 200
assert ask3("/api/ask", {"q": "hei", "model": "claude-opus-5-5"})[0] == 200  # at home, the flat's key is fine
assert calls_made == [("hei", "local", None), ("hei", "claude-opus-5-5", "sk-ant-theirs"), ("hei", "claude-opus-5-5", None)]
assert ask3("/api/wish", {"text": "en vits"}, tunnel=True)[0] == 401
assert ask3("/api/model", {"model": "claude-opus-5-5"}, tunnel=True)[0] == 403 and agent.model == "claude-haiku-4-5"
# wishes: Claude's design waits; a GitHub issue says so; only the admin (or GitHub) turns it into a button
import issues
filed, closed = [], []
issues.enabled = lambda: True
issues.open_issue = lambda preset, wish: filed.append((preset["name"], wish)) or (7, "https://github.com/x/y/issues/7")
issues.close = lambda n, approved: closed.append((n, approved))
real_design = agent.design_preset
def fake_design(text, api_key=None):
    presets.last = None
    presets.create_preset("Vits", "Fortell en kort vits.", "idea")
    return "Laget.", presets.last, 0.0
agent.design_preset = fake_design
os.environ["WEATHERBOY_ADMIN"] = "hemmelig"
code, _, out = ask3("/api/wish", {"text": "en vits"})
wid = json.loads(out)["preset"]["id"]
assert code == 200 and json.loads(out)["preset"]["issue"].endswith("/7") and filed == [("Vits", "en vits")]
assert presets.get(wid, pending=True)["issue"] == 7 and "Vits" not in [p["name"] for p in presets.items()]
def wishes3(body, tunnel=True, admin=None):
    h = {"Content-Type": "application/json"} | ({"CF-Connecting-IP": "84.214.212.9"} if tunnel else {}) \
        | ({"X-Admin": admin} if admin else {})
    try:
        r = urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{srv3.server_port}/api/wishes",
                                                          json.dumps(body).encode(), h, method="POST"))
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, None
assert wishes3({"approve": wid})[0] == 403 and wishes3({"approve": wid}, admin="gjett")[0] == 403
code, d = wishes3({"approve": wid}, admin="hemmelig")
assert code == 200 and d["pending"] == [] and "Vits" in [p["name"] for p in presets.items()] and closed == [(7, True)]
assert json.loads(urllib.request.urlopen(f"http://127.0.0.1:{srv3.server_port}/api/wishes").read())["admin"]  # its own keyboard
fake_design("x")  # closed as "not planned" on GitHub: turned down here too
presets.set_issue(presets.last["id"], 8)
issues.verdict = lambda n: {8: "rejected"}.get(n)
web.sync_wishes()
assert presets.items(pending=True) == []
agent.design_preset = real_design
del os.environ["WEATHERBOY_ADMIN"]
# the phone base's settings card: the server talks to the board over the cable (here a fake one that speaks
# esp8266-phone-hook's protocol, LCD frames and all), and only the admin may change what it keeps in flash
import phone, queue as _queue
class FakeBoard(phone.SerialLink):
    def __init__(self):
        self.where, self.inbox, self.out = "serial /dev/fake", _queue.Queue(), bytearray()
        self.cfg = {"host": "172.20.10.5", "port": 5000, "ssid": "bastiphone", "pass": "set", "wifi": "connected",
                    "ip": "192.168.0.55", "rssi": -61, "hook": 0}
        self._write = threading.Lock()
        self.inbox.put(b"HOOK 1")
    def lines(self):
        while True:
            yield self.inbox.get()
    def send_frame(self, data):
        self.out += b"L" + len(data).to_bytes(2, "little") + data
    def send_line(self, text):
        self.out += text.encode() + b"\n"
        while self.out:  # the firmware's parser: skip frames, answer whole lines
            if self.out[:1] == b"L":
                n = int.from_bytes(self.out[1:3], "little")
                del self.out[:3 + n]
                continue
            line, _, rest = bytes(self.out).partition(b"\n")
            self.out = bytearray(rest)
            if (reply := self.answer(line.decode())) is not None:  # SCREEN gets no answer
                self.inbox.put(reply)
    def answer(self, cmd):
        if cmd.startswith("SCREEN "):
            self.screen = cmd[7:]
            return None
        if cmd == "GET":
            return b"CFG " + json.dumps(self.cfg).encode()
        if cmd.startswith("SET "):
            k, _, v = cmd[4:].partition("=")
            if k == "port" and not 1 <= int(v) <= 65535:
                return b"ERR port must be 1-65535"
            self.cfg[k] = "set" if k == "pass" else int(v) if k == "port" else v
            return b"OK " + k.encode()
        if cmd == "TEST":
            return b"OK reached %s:%d" % (self.cfg["host"].encode(), self.cfg["port"])
        return b"OK restarting"
board = FakeBoard()
phone._shared = phone.Phone(board)
board.send_frame(bytes(1024))  # an LCD frame from the voice loop in between: commands still parse
_time.sleep(0.1)
os.environ["WEATHERBOY_ADMIN"] = "hemmelig"
def esp3(body=None, admin=None):
    h = {"Content-Type": "application/json", "CF-Connecting-IP": "84.214.212.9"} | ({"X-Admin": admin} if admin else {})
    req = urllib.request.Request(f"http://127.0.0.1:{srv3.server_port}/api/esp", json.dumps(body).encode() if body else None,
                                 h, method="POST" if body else "GET")
    try:
        r = urllib.request.urlopen(req)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())
code, d = esp3()
assert code == 200 and d["found"] and d["lifted"] and d["config"]["host"] == "172.20.10.5" and d["where"] == "serial /dev/fake"
assert esp3({"set": {"host": "192.168.0.42"}})[0] == 403  # anyone may look, only the admin may change it
code, d = esp3({"set": {"host": "192.168.0.42", "port": "5001", "ssid": "Telia_x", "pass": ""}}, admin="hemmelig")
assert code == 200 and d["config"]["host"] == "192.168.0.42" and d["config"]["port"] == 5001 and d["config"]["ssid"] == "Telia_x"
code, d = esp3({"set": {"port": "99999"}}, admin="hemmelig")
assert code == 500 and "1-65535" in d["error"]  # the board's own reason comes back
code, d = esp3({"test": True}, admin="hemmelig")
assert d["ok"] and d["said"] == "reached 192.168.0.42:5001"
try:
    phone._shared.set(host="a\nRESTART")
    raise AssertionError("smuggled a second command in")
except ValueError:
    pass
silent = phone.Phone(FakeBoard())
silent.link.answer = lambda cmd: None  # the old sketch: hook lines, no answers
silent.link.send_line = lambda text: None
try:
    silent.ask("GET", timeout=0.2)
    raise AssertionError("an old firmware answered")
except TimeoutError as e:
    assert "fastvaren" in str(e)
class FakeSerial:  # the real SerialLink reading a long settings reply between hook lines
    def __init__(self, data):
        self.data = data
    def read_until(self, end, size):
        i = self.data.find(end)
        n = min(size, len(self.data) if i < 0 else i + 1)
        chunk, self.data = self.data[:n], self.data[n:]
        return chunk
cfg_line = b"CFG " + json.dumps(board.cfg | {"ssid": "x" * 32}).encode() + b"\n"
link = phone.SerialLink.__new__(phone.SerialLink)
link.ser = FakeSerial(b"HOOK 1\n" + cfg_line + b"HOOK 1\n")
got = [next(link.lines()) for _ in range(3)]
assert len(cfg_line) > 150 and got == [b"HOOK 1", cfg_line.strip(), b"HOOK 1"]  # whole lines, never "OK 1"
phone._shared.screen("think")  # the board's own "Tenker" screen, until done
assert board.screen == "think"
phone._shared.screen("done")
assert board.screen == "done" and phone._shared.config()["host"] == "192.168.0.42"  # no stray answer in between
try:
    phone._shared.screen("dance")
    raise AssertionError("sent an unknown screen state")
except ValueError:
    pass
phone.Phone(phone.UdpLink.__new__(phone.UdpLink)).screen("think")  # an ESP32 on WiFi: nothing to draw, no error
phone._shared = None
del os.environ["WEATHERBOY_ADMIN"]
srv3.shutdown()
agent.ask = real_ask
# a visitor's key bills them: their client, and none of it in the flat's spending
assert agent.client_for(None) is agent.client and agent.client_for("sk-ant-x").api_key == "sk-ant-x"
before, agent.history = agent.spent, [{"role": "user", "content": "x"}]
agent.last_model, agent.last_ask = "claude-haiku-4-5", _time.time()
local.SERVERS = ["http://127.0.0.1:1/v1"]
try:
    agent.ask("hei", use="local")
except RuntimeError:
    pass
assert agent.history[:1] != [{"role": "user", "content": "x"}] and agent.spent == before  # another model: fresh

# web tools for the local model: DuckDuckGo's results parsed; fetching only reaches public addresses
import websearch
p = websearch._DDG()
p.feed('<a class="result__a" href="https://ruter.no/trikk">Trikk - Ruter</a>'
       '<a class="result__snippet" href="x">Linje <b>17</b> og 18</a>'
       '<a class="result__a" href="//duckduckgo.com/y.js?ad">Annonse</a>')
assert p.results[0] == {"title": "Trikk - Ruter", "url": "https://ruter.no/trikk", "snippet": "Linje 17 og 18"}
for bad in ("http://192.168.0.217:9100/", "http://localhost:8615/api/url", "http://127.0.0.1/", "http://10.0.0.1/",
            "http://169.254.169.254/latest/meta-data", "http://[::1]/", "file:///etc/passwd", "ftp://example.com/"):
    assert not websearch.public(bad), bad
    assert websearch.web_fetch(bad).startswith("Refused"), bad
assert [t.name for t in agent.LOCAL_WEB] == ["web_search", "web_fetch"]

# health: each component on its own; a crashing check is a red light, not a broken page; checks run in parallel
real_checks = web.CHECKS
slow = lambda: (_time.sleep(1), {"level": "green", "state": "ok"})[1]
web.CHECKS = {"a": ("A", slow), "b": ("B", slow), "c": ("C", lambda: 1 / 0)}
web._health["result"] = None
t0_ = _time.time()
h = web.health()
assert _time.time() - t0_ < 1.8  # two 1-second checks side by side, not one after the other
assert [(c["id"], c["level"]) for c in h] == [("a", "green"), ("b", "green"), ("c", "red")]
assert "ZeroDivisionError" in h[2]["state"]
web.CHECKS, web._health["result"] = real_checks, None
local.SERVERS = ["http://127.0.0.1:1/v1"]
assert web.check_local()["level"] == "red"
web.PUBLIC_URL, saved_url = "", web.PUBLIC_URL
assert web.check_tunnel() == {"level": "off", "state": "ikke satt opp"}
web.PUBLIC_URL = saved_url
daily.update({"art": False, "word": False})
assert web.check_daily() == {"level": "off", "state": "av"}

import speak
assert speak.lang_of("Det blir tolv grader og lett regn.") == "no"
assert speak.lang_of("It will be twelve degrees and light rain.") == "en"

print("ok")
