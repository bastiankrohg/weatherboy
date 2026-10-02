"""Clicks through the web page in a real browser (your installed Edge): receipts, the image editor, the
print queue. Starts its own server without a printer, so nothing prints.

    uv run --extra ui test_web_ui.py            screenshots land in out/
"""
import json
import os

from playwright.sync_api import sync_playwright

import daily
import presets
import printq
import shoplist
import words
import tempfile
import web
from pathlib import Path

shoplist.FILE = Path(tempfile.mkdtemp()) / "handleliste.json"  # never the flat's real list
presets.FILE = Path(tempfile.mkdtemp()) / "presets.json"  # nor its real buttons
printq.DATA = Path(tempfile.mkdtemp())  # nor the flat's real print queue
printq.PEDIA, printq.FILE = printq.DATA / "printq", printq.DATA / "printq.json"
daily.DATA = Path(tempfile.mkdtemp())  # nor the daily settings; and no Claude calls for the word
daily.SETTINGS, daily.DONE = daily.DATA / "daglig.json", daily.DATA / "daglig_utskrevet.json"
words.FILE = daily.DATA / "dagens_ord.json"
words.generate = lambda lang, seen: {"word": "산책", "reading": "sanchaek", "kind": "noun", "meaning": "a walk",
                                     "example": "산책해요.", "example_reading": "sanchaekaeyo.",
                                     "example_meaning": "I take a walk.", "note": ""}
presets.create_preset("Dagens ord", "Gi meg et sjeldent norsk ord.", "idea")
web.CHECKS = {"printer": ("Skriver", lambda: {"level": "red", "state": "svarer ikke"}),  # fast fakes:
              "local": ("Lokal modell", lambda: {"level": "green", "state": "robotlab"})}   # no real services
server = web.start(0, None)  # port 0: a free port, so a stray server can't answer in its place
B = f"http://127.0.0.1:{server.server_address[1]}"
OUT = "out/"
os.makedirs(OUT, exist_ok=True)

errors = []
with sync_playwright() as p:
    browser = p.chromium.launch(channel="msedge")
    page = browser.new_page(viewport={"width": 1200, "height": 1100})
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    # the one 500 the queue raises on purpose ("Skriv ut alt" with no printer) is checked where it happens
    page.on("response", lambda r: r.status >= 400 and "/api/queue?print=all" not in r.url
            and errors.append(f"{r.status} {r.url}"))
    page.goto(B)

    # a receipt, then the pencil: the canvas replaces the paper
    page.click("[data-card=art]")
    page.wait_for_selector("#paper img")
    first = page.eval_on_selector("#paper img", "i => [i.naturalWidth, i.naturalHeight]")
    page.click("#edit")
    page.wait_for_selector("#canvas", state="visible")
    size = page.eval_on_selector("#canvas", "c => [c.width, c.height]")
    assert page.is_hidden("#paper"), "paper still shown while editing"

    box = page.locator("#canvas").bounding_box()
    # crop is the default tool for receipts: drag a rectangle over the top half
    page.mouse.move(box["x"] + 2, box["y"] + 2)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] - 2, box["y"] + box["height"] / 2, steps=8)
    page.mouse.up()
    crop = page.evaluate("() => ed.crop")
    # draw a stroke with the pen
    page.click("[data-tool=pen]")
    page.mouse.move(box["x"] + 40, box["y"] + 40)
    page.mouse.down()
    page.mouse.move(box["x"] + 200, box["y"] + 120, steps=10)
    page.mouse.up()
    inked = page.evaluate("() => { const d = ed.ink.getContext('2d').getImageData(0, 0, ed.ink.width, ed.ink.height).data;"
                          " let n = 0; for (let i = 3; i < d.length; i += 4) n += d[i] > 0; return n; }")
    page.click("#undo")
    undone = page.evaluate("() => { const d = ed.ink.getContext('2d').getImageData(0, 0, ed.ink.width, ed.ink.height).data;"
                           " let n = 0; for (let i = 3; i < d.length; i += 4) n += d[i] > 0; return n; }")
    page.mouse.move(box["x"] + 40, box["y"] + 40)
    page.mouse.down()
    page.mouse.move(box["x"] + 200, box["y"] + 120, steps=10)
    page.mouse.up()
    page.screenshot(path=OUT + "ui_editing.png")
    # rotate to landscape and apply
    page.click("#rotr")
    rotated = page.eval_on_selector("#canvas", "c => [c.width, c.height]")
    page.click("#done")
    page.wait_for_function("() => !document.querySelector('#paper').hidden && document.querySelector('#paper img')"
                           " && document.querySelector('#paper img').naturalHeight !== %d" % first[1])
    final = page.eval_on_selector("#paper img", "i => [i.naturalWidth, i.naturalHeight]")
    path = page.evaluate("() => current.path")
    page.screenshot(path=OUT + "ui_after.png")

    # recipe filter: the chips come from the tags; picking one shows only recipes with it, again shows all
    page.wait_for_selector("#tagchips button")
    total = page.evaluate("() => recipeList.length")
    chip = page.locator("#tagchips button").first
    tag = chip.inner_text().rsplit(" ", 1)
    chip.click()
    shown = page.evaluate("() => document.querySelectorAll('#recipes li button').length")
    page.locator("#tagchips button").first.click()
    filter_ok = int(tag[1]) == shown <= total and page.evaluate("() => document.querySelectorAll('#recipes li button').length") == total

    def new_preview(click):
        """Click, then wait for a new receipt image (not the one already showing) with printing enabled."""
        old = page.evaluate("() => document.querySelector('#paper img')?.src || ''")
        page.click(click)
        page.wait_for_function("old => { const i = document.querySelector('#paper img');"
                               " return i && i.src !== old && !document.querySelector('#paper').hidden"
                               " && !document.querySelector('#print').disabled; }", arg=old)
        return page.eval_on_selector("#paper img", "i => i.naturalHeight")

    # text editor: a recipe from the list opens as text; editing it re-renders the card (not saved: real collection)
    before = new_preview(".recipes li button")
    page.click("#edit")
    page.wait_for_selector("#edittext", state="visible")
    assert page.is_visible("#saverecipe")
    page.fill("#edittext", page.input_value("#edittext") + "\n\n## Tips\n\n- Ekstra linje fra testen.\n")
    full_h = new_preview("#apply")
    text_ok = full_h > before and page.evaluate("() => current.a.includes('Ekstra linje fra testen')")

    # short version and ingredients to the list, from the recipe opened above
    short_h = new_preview("#shortlong")
    page.click("#toshop")
    page.wait_for_function("() => document.querySelectorAll('#shoplist li:not(.msg)').length > 0")
    ingredients_added = page.evaluate("() => document.querySelectorAll('#shoplist li:not(.msg)').length")
    page.click("#shopclear")  # nothing printed: stays
    page.evaluate("() => shopPost({ remove: shop.map(it => it.id) })")
    page.wait_for_function("() => document.querySelectorAll('#shoplist li:not(.msg)').length === 0")

    # shared shopping list: add two things, preview the printout with tick boxes
    page.fill("#shopinput", "melk, egg")
    page.press("#shopinput", "Enter")
    page.wait_for_function("() => document.querySelectorAll('#shoplist li:not(.msg)').length === 2")
    page.click("#shopprint")
    page.wait_for_function("() => current && current.kind === 'shopping' && document.querySelector('#paper img')")
    shop_ok = page.evaluate("() => current.ids.length === 2 && current.a.includes('- egg')")

    # the print queue: with no printer, "Kø" is what everything does, and the list is yours to work with
    page.wait_for_selector("#queue .msg")
    empty_ok = page.inner_text("#queue .msg") == "Ingenting i køen." and page.is_disabled("#queueprint")
    for n, card in enumerate(["", "[data-card=art]", "[data-card=qr]"], start=1):
        if card:
            new_preview(card)   # the shopping list is the one already on the paper
        page.click("#ko")
        page.wait_for_function("n => document.querySelectorAll('#queue li:not(.msg)').length === n", arg=n)
    labels = page.evaluate("() => queue.map(j => j.label)")
    queued_ok = labels == ["handleliste", "kunst", "qr-kode"] and page.inner_text("#queueprint") == "Skriv ut alt"

    # ▲ and ▼ move one step in the order, and stop at the ends instead of wrapping
    page.click("#queue li:nth-child(3) .qbar button:nth-child(1)")   # ▲ on the last one
    page.wait_for_function("() => queue.map(j => j.label).join() === 'handleliste,qr-kode,kunst'")
    page.click("#queue li:nth-child(1) .qbar button:nth-child(2)")   # ▼ on the first one
    page.wait_for_function("() => queue.map(j => j.label).join() === 'qr-kode,handleliste,kunst'")
    with page.expect_response("**/api/queue?move=*") as moved:       # ▲ on the first one: already at the top
        page.click("#queue li:nth-child(1) .qbar button:nth-child(1)")
    order_kept = [j["label"] for j in json.loads(moved.value.text())["items"]] == \
                 ["qr-kode", "handleliste", "kunst"]

    # ⏸ holds one back: it's skipped by the printer, but never dropped
    page.click("#queue li:nth-child(1) .qbar button:nth-child(3)")
    page.wait_for_function("() => document.querySelectorAll('#queue li.held').length === 1")
    held_ok = page.evaluate("() => queue[0].hold") and "holdt" in page.inner_text("#queue li.held small")

    # ✎ opens the job's own stored PNG in the editor, and "Bruk endringene" writes it back to the queue
    ed_id = printq.items()[0]["id"]
    before_png = printq.png_path(ed_id).read_bytes()
    page.click("#queue li:nth-child(1) .qbar button:nth-child(4)")
    page.wait_for_selector("#canvas", state="visible")
    opened = page.evaluate("() => ed.job && ed.job.id") == ed_id
    page.click("#rotr")
    page.click("#done")
    page.wait_for_function("() => document.querySelector('#msg').textContent === 'Endret i køen.'")
    page.wait_for_function("n => document.querySelectorAll('#queue li:not(.msg)').length === n", arg=3)
    edited_ok = (opened and printq.png_path(ed_id).read_bytes() != before_png
                 and [j["id"] for j in printq.items()][0] == ed_id)  # same job, same place, new picture

    # × drops one; "Skriv ut alt" with no printer complains and loses nothing; "Tøm" empties it
    page.click("#queue li:nth-child(1) .qbar button:nth-child(5)")
    page.wait_for_function("() => document.querySelectorAll('#queue li:not(.msg)').length === 2")
    page.click("#queueprint")
    page.wait_for_function("() => document.querySelector('#msg').classList.contains('bad')")
    kept_ok = page.evaluate("() => queue.length") == 2 and printq.count()[0] == 2
    page.once("dialog", lambda dlg: dlg.accept())
    page.click("#queueclear")
    page.wait_for_selector("#queue .msg")
    cleared_ok = page.inner_text("#queue .msg") == "Ingenting i køen." and printq.count() == (0, 0)

    # a wished-for preset shows as a button, and × removes it (after a confirm)
    page.wait_for_selector(".preset button")
    preset_label = page.inner_text(".preset button")
    page.once("dialog", lambda dlg: dlg.accept())
    page.click(".preset button:last-child")
    page.wait_for_function("() => !document.querySelector('.preset')")
    preset_ok = preset_label == "Dagens ord" and presets.items() == []

    # daily prints: switches save at once, "Stopp alt" turns both off, the word previews
    page.wait_for_function("() => document.querySelector('#d-lang').options.length > 5")
    page.select_option("#d-lang", "ja")
    page.wait_for_function("() => document.querySelector('#d-status').textContent.length > 0")
    page.click("#d-off")
    page.wait_for_function("() => document.querySelector('#d-status').textContent === 'Av.'")
    daily_ok = daily.settings()["lang"] == "ja" and not daily.settings()["art"] and not daily.settings()["word"]
    page.select_option("#d-lang", "ko")
    page.click("#d-preview")
    page.wait_for_function("() => current && current.path.startsWith('/api/card/word') && document.querySelector('#paper img')")

    # health lights: the server's own plus one per component; tapping one says what's up
    page.wait_for_function("() => document.querySelectorAll('#health .pill').length === 3")
    lights = page.evaluate("() => [...document.querySelectorAll('#health .pill')].map(b => b.textContent + ':' + b.className)")
    page.click("#health .pill.red")
    health_ok = lights == ["Server:pill green", "Skriver:pill red", "Lokal modell:pill green"] \
        and page.inner_text("#msg") == "Skriver: svarer ikke"

    # blank sheet
    page.click("#blank")
    blank = page.eval_on_selector("#canvas", "c => [c.width, c.height]")
    tool = page.evaluate("() => ed.tool")
    browser.close()
server.shutdown()

# Chromium logs every 500 to the console as well as the network; the queue raises one on purpose, so that
# single message is separated from the ones that would mean something is actually broken.
asked_for = [e for e in errors if "status of 500" in e]
other = [e for e in errors if "status of 500" not in e]

print("preview", first, "| canvas", size, "| crop", {k: round(v) for k, v in crop.items()})
print("ink pixels", inked, "-> undo ->", undone, "| rotated canvas", rotated, "| applied", final, path)
print("blank", blank, tool, "| page errors:", other or "none")
assert size == first and crop["y"] == 0 and crop["x"] == 0 and crop["w"] == size[0] and inked > 100 and undone == 0
assert rotated == [size[1], size[0]] and final[0] == 576 and path.startswith("/api/image") and blank == [576, 800]
print("full", full_h, "-> short card", short_h, "px high | ingredients added", ingredients_added)
assert short_h < full_h * 0.7 and ingredients_added >= 3
print("filter", tag, "->", shown, "of", total)
print("queue", labels, "->", "held, edited, kept on a failed print-all, then cleared")
assert (tool == "pen" and text_ok and shop_ok and filter_ok and preset_ok and daily_ok and health_ok
        and empty_ok and queued_ok and order_kept and held_ok and edited_ok and kept_ok and cleared_ok
        and not other and len(asked_for) == 1)   # exactly one deliberate 500: "Skriv ut alt" with no printer
print("editor ok")
