"""Clicks through the web page in a real browser (your installed Edge): receipts, the image editor.
Starts its own server without a printer, so nothing prints.

    uv run --with playwright test_web_ui.py            screenshots land in out/
"""
import os

from playwright.sync_api import sync_playwright

import presets
import shoplist
import tempfile
import web
from pathlib import Path

shoplist.FILE = Path(tempfile.mkdtemp()) / "handleliste.json"  # never the flat's real list
presets.FILE = Path(tempfile.mkdtemp()) / "presets.json"  # nor its real buttons
presets.create_preset("Dagens ord", "Gi meg et sjeldent norsk ord.", "idea")
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

    # recipe filter: the chips come from the tags; picking one shows only recipes with it, again shows all
    page.wait_for_selector("#tagchips button")
    total = page.evaluate("() => recipeList.length")
    chip = page.locator("#tagchips button").first
    tag = chip.inner_text().rsplit(" ", 1)
    chip.click()
    shown = page.evaluate("() => document.querySelectorAll('#recipes li button').length")
    page.locator("#tagchips button").first.click()
    filter_ok = int(tag[1]) == shown <= total and page.evaluate("() => document.querySelectorAll('#recipes li button').length") == total

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

    # short version and ingredients to the list, from the recipe opened above
    full_h = page.eval_on_selector("#paper img", "i => i.naturalHeight")
    page.click("#shortlong")
    page.wait_for_function("h => current.kind === 'short' && document.querySelector('#paper img').naturalHeight !== h", arg=full_h)
    short_h = page.eval_on_selector("#paper img", "i => i.naturalHeight")
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

    # a wished-for preset shows as a button, and × removes it (after a confirm)
    page.wait_for_selector(".preset button")
    preset_label = page.inner_text(".preset button")
    page.once("dialog", lambda dlg: dlg.accept())
    page.click(".preset button:last-child")
    page.wait_for_function("() => !document.querySelector('.preset')")
    preset_ok = preset_label == "Dagens ord" and presets.items() == []

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
print("short card", short_h, "px high | ingredients added", ingredients_added)
assert short_h < full_h * 0.7 and ingredients_added >= 3
print("filter", tag, "->", shown, "of", total)
assert tool == "pen" and text_ok and shop_ok and filter_ok and preset_ok and not errors
print("editor ok")
