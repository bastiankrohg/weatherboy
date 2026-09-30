"""Clicks through the web page in a real browser (your installed Edge): receipts, the image editor.
Starts its own server without a printer, so nothing prints.

    uv run --with playwright test_web_ui.py            screenshots land in out/
"""
import os

from playwright.sync_api import sync_playwright

import shoplist
import tempfile
import web
from pathlib import Path

shoplist.FILE = Path(tempfile.mkdtemp()) / "handleliste.json"  # never the flat's real list
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
    page.on("response", lambda r: r.status >= 400 and errors.append(f"{r.status} {r.url}"))
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

    # text editor: a recipe from the list opens as text; editing it re-renders the card (not saved: real collection)
    page.click(".recipes li button")
    page.wait_for_function("() => current && current.recipe && document.querySelector('#paper img')")
    before = page.eval_on_selector("#paper img", "i => i.naturalHeight")
    page.click("#edit")
    page.wait_for_selector("#edittext", state="visible")
    assert page.is_visible("#saverecipe")
    page.fill("#edittext", page.input_value("#edittext") + "\n\n## Tips\n\n- Ekstra linje fra testen.\n")
    page.click("#apply")
    page.wait_for_function("h => document.querySelector('#paper img') && !document.querySelector('#paper').hidden"
                           " && document.querySelector('#paper img').naturalHeight > h", arg=before)
    text_ok = page.evaluate("() => current.a.includes('Ekstra linje fra testen')")

    # shared shopping list: add two things, preview the printout with tick boxes
    page.fill("#shopinput", "melk, egg")
    page.press("#shopinput", "Enter")
    page.wait_for_function("() => document.querySelectorAll('#shoplist li:not(.msg)').length === 2")
    page.click("#shopprint")
    page.wait_for_function("() => current && current.kind === 'shopping' && document.querySelector('#paper img')")
    shop_ok = page.evaluate("() => current.ids.length === 2 && current.a.includes('- egg')")

    # blank sheet
    page.click("#blank")
    blank = page.eval_on_selector("#canvas", "c => [c.width, c.height]")
    tool = page.evaluate("() => ed.tool")
    browser.close()
server.shutdown()

print("preview", first, "| canvas", size, "| crop", {k: round(v) for k, v in crop.items()})
print("ink pixels", inked, "-> undo ->", undone, "| rotated canvas", rotated, "| applied", final, path)
print("blank", blank, tool, "| page errors:", errors or "none")
assert size == first and crop["y"] == 0 and crop["x"] == 0 and crop["w"] == size[0] and inked > 100 and undone == 0
assert rotated == [size[1], size[0]] and final[0] == 576 and path.startswith("/api/image") and blank == [576, 800]
assert tool == "pen" and text_ok and shop_ok and not errors
print("editor ok")
