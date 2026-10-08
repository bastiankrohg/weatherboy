"""Pillow side: compose receipts at 576 dots and convert to/from printer rows."""
import math
import random
import re
import threading
from datetime import datetime
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from printer import DOTS, ROW

M = 32                # 4 mm side margins
COL = DOTS - 2 * M    # 512-dot text column
MAX_H = 4800          # ~60 cm. ponytail: hard paper guard; long answers get cut, not printed forever

SANS = ("DejaVuSans.ttf", "arial.ttf", "Arial.ttf", "Helvetica.ttc")
BOLD = ("DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf", "Helvetica.ttc")
MONO = ("DejaVuSansMono.ttf", "consola.ttf", "Menlo.ttc", "Courier New.ttf")
# Faces with Hangul / Chinese / Japanese: Noto CJK on a Pi (fonts-noto-cjk), else what Windows or macOS ship
KOREAN = ("NotoSansCJK-Regular.ttc", "malgun.ttf", "AppleSDGothicNeo.ttc")
CHINESE = ("NotoSansCJK-Regular.ttc", "msyh.ttc", "PingFang.ttc", "Hiragino Sans GB.ttc")
JAPANESE = ("NotoSansCJK-Regular.ttc", "YuGothM.ttc", "meiryo.ttc", "Hiragino Sans GB.ttc", "msyh.ttc")
KOREAN_BOLD = ("NotoSansCJK-Bold.ttc", "malgunbd.ttf", "AppleSDGothicNeo.ttc")
CHINESE_BOLD = ("NotoSansCJK-Bold.ttc", "msyhbd.ttc", "PingFang.ttc", "Hiragino Sans GB.ttc")
JAPANESE_BOLD = ("NotoSansCJK-Bold.ttc", "YuGothB.ttc", "meiryob.ttc", "Hiragino Sans GB.ttc", "msyhbd.ttc")
HANGUL = re.compile(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]")
KANA = re.compile(r"[\u3040-\u30ff]")
HAN = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")


@lru_cache(maxsize=None)
def font(names, size):
    for n in names:  # PIL searches the OS font dirs for bare names on Windows, macOS and Linux
        try:
            return ImageFont.truetype(n, size)
        except OSError:
            pass
    return ImageFont.load_default(size)


def pick(f, text):
    """PIL has no font fallback: text with Hangul, kana or Chinese characters gets a face that has them,
    in the same weight. One face per call, so a line mixing scripts takes the first that matches."""
    bold = f is font(BOLD, int(f.size))  # fonts are cached, so the bold face is always the same object
    for script, faces, bold_faces in ((HANGUL, KOREAN, KOREAN_BOLD), (KANA, JAPANESE, JAPANESE_BOLD),
                                      (HAN, CHINESE, CHINESE_BOLD)):
        if script.search(text):
            return font(bold_faces if bold else faces, int(f.size))
    return f


def wrap(text, f, width=COL):
    lines, cur = [], ""
    for word in text.split():
        while f.getlength(word) > width:  # URLs and the like: hard-break
            cut = next(i for i in range(len(word), 0, -1) if f.getlength(word[:i]) <= width)
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(word[:cut])
            word = word[cut:]
        if cur and f.getlength(cur + " " + word) > width:
            lines.append(cur)
            cur = word
        else:
            cur = (cur + " " + word).strip()
    return lines + [cur] if cur else lines


def mono_for(lines):
    """Largest monospace size (12-24px) where the widest art line fits the column."""
    for size in range(24, 11, -1):
        f = font(MONO, size)
        if max(f.getlength(l) for l in lines) <= COL:
            return f
    return f


def _canvas():
    img = Image.new("L", (DOTS, MAX_H), 255)  # drawn tall, cropped at the end; anything past MAX_H is clipped
    return img, ImageDraw.Draw(img)


HEADER = threading.local()  # this thread's receipts (the web page's choices): .off skips the top line, .icon picks its icon


def header_on():
    return not getattr(HEADER, "off", False)


def _header(d, when=None, label="WEATHERBOY", icon=None):
    """An icon (see ICONS) or a tracked-out small-caps label on the left, a timestamp on the right. -> next y.
    With the header switched off, nothing: just the margin the first line would have had."""
    small, x, y = font(SANS, 20), M, 64  # 8 mm above the first mark
    if not header_on():
        return y - 24
    icon = getattr(HEADER, "icon", None) or icon  # an icon picked on the page wins, even over a text label
    if icon:
        ICONS.get(icon, ICONS["question"])(d, M, y - 8)
    for ch in "" if icon else label:
        d.text((x, y), ch, font=small, fill=0)
        x += small.getlength(ch) + 4
    stamp = f"{when or datetime.now():%d.%m %H:%M}"
    d.text((DOTS - M - small.getlength(stamp), y), stamp, font=small, fill=0)
    return y + 34


def theme(text):
    """Split the model's "tema: cooking" first line off an answer -> (theme, rest). No tag: a question."""
    first, _, rest = text.strip().partition("\n")
    m = re.fullmatch(r"\W*tema\W*:\s*(\w+)\W*", first.strip(), re.I)
    if not m:
        return "question", text.strip()
    return (m[1].lower() if m[1].lower() in ICONS else "question"), rest.strip()  # an unknown theme: still no tag


def answer(question, text, when=None, icon=None):
    """Question small (provenance), answer big, theme icon on top. ``` fences render as monospace art."""
    if icon is None:
        icon, text = theme(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)  # receipts are plain type; models still reach for **bold**
    small, body, sub, head = font(SANS, 20), font(SANS, 28), font(BOLD, 28), font(BOLD, 40)
    img, d = _canvas()
    y = _header(d, when, icon=icon)
    qf = pick(small, question)
    for line in wrap(question, qf):
        d.text((M, y), line, font=qf, fill=0)
        y += 26
    y += 34

    art = False
    for block in text.strip().split("```"):
        if art:
            lines = [l.rstrip() for l in block.split("\n")[1:]]  # [1:] drops the ```lang tag line
            while lines and not lines[-1]:
                lines.pop()
            if lines:
                f, y = mono_for(lines), y + 17
                for line in lines:
                    d.text((M, y), line, font=f, fill=0)
                    y += f.size + 2
                y += 17
        else:
            for para in block.strip("\n").split("\n"):
                item = re.match(r"(\d+\.|[-*])\s+(.*)", para)
                fb, fs, fh = pick(body, para), pick(sub, para), pick(head, para)  # Korean, Chinese, Japanese
                if para.startswith("# "):
                    for line in wrap(para[2:], fh):
                        d.text((M, y), line, font=fh, fill=0)
                        y += 48
                    y += 10
                elif para.startswith("## "):
                    y += 17
                    for line in wrap(para[3:], fs):
                        d.text((M, y), line, font=fs, fill=0)
                        y += 34
                elif item:  # bullets and numbered steps with a hanging indent
                    d.text((M, y), "•" if item[1] in "-*" else item[1], font=body, fill=0)
                    for line in wrap(item[2], fb, COL - 40):
                        d.text((M + 40, y), line, font=fb, fill=0)
                        y += 34
                elif not para.strip():
                    y += 17
                else:
                    for line in wrap(para, fb):
                        d.text((M, y), line, font=fb, fill=0)
                        y += 34
        art = not art
    return img.crop((0, 0, DOTS, min(y + 8, MAX_H)))


SYMBOLS = {"clearsky": "klarvær", "fair": "lettskyet", "partlycloudy": "delvis skyet", "cloudy": "skyet",
           "fog": "tåke", "lightrain": "lett regn", "rain": "regn", "heavyrain": "kraftig regn",
           "lightrainshowers": "lette regnbyger", "rainshowers": "regnbyger", "heavyrainshowers": "kraftige byger",
           "lightsleet": "lett sludd", "sleet": "sludd", "lightsnow": "lett snø", "snow": "snø",
           "heavysnow": "mye snø", "rainandthunder": "regn og torden"}


def weather(place, fc, when=None):
    """Now in big type, then the next hours as a curve (only high and low labelled) over a rain lane."""
    small, body = font(SANS, 20), font(SANS, 28)
    img, d = _canvas()
    y = _header(d, when, icon="weather")
    d.text((M, y), place, font=font(BOLD, 40), fill=0)
    y += 56
    now, big = fc[0], font(BOLD, 110)
    temp = f"{round(now['temp'])}°"
    d.text((M, y), temp, font=big, fill=0)
    x = M + big.getlength(temp) + 24
    d.text((x, y + 22), SYMBOLS.get(now["symbol"].split("_")[0], now["symbol"].replace("_", " ")), font=body, fill=0)
    d.text((x, y + 60), f"vind {now['wind']:.0f} m/s", font=body, fill=0)
    y += 170

    temps = [h["temp"] for h in fc]
    lo, hi, ch, step = min(temps), max(temps), 150, COL / (len(fc) - 1)
    pts = [(M + i * step, y + ch - (t - lo) / ((hi - lo) or 1) * ch) for i, t in enumerate(temps)]
    d.line(pts, fill=0, width=4, joint="curve")
    for i, above in ((temps.index(hi), True), (temps.index(lo), False)):
        px, py = pts[i]
        label = f"{round(temps[i])}°"
        lx = min(max(px - small.getlength(label) / 2, M), DOTS - M - small.getlength(label))
        d.ellipse((px - 6, py - 6, px + 6, py + 6), fill=0)
        d.text((lx, py - 32 if above else py + 10), label, font=small, fill=0)
    y += ch + 44

    rain, lane = [h["rain"] or 0 for h in fc], 56
    top = max(max(rain), 2.0)  # 2 mm/h fills the lane unless something heavier is coming
    for i, r in enumerate(rain):
        if r:
            d.rectangle((M + (i - 0.35) * step, y + lane - r / top * lane, M + (i + 0.35) * step, y + lane), fill=0)
    d.line((M, y + lane, DOTS - M, y + lane), fill=0, width=2)
    y += lane + 10
    for i, h in enumerate(fc):
        if h["time"].hour % 6 == 0:
            label = f"{h['time']:%H}"
            d.line((M + i * step, y - 10, M + i * step, y - 4), fill=0, width=3)
            d.text((M + i * step - small.getlength(label) / 2, y), label, font=small, fill=0)
    y += 34
    d.text((M, y), f"{sum(rain):.1f} mm neste {len(fc)} timer", font=small, fill=0)
    return img.crop((0, 0, DOTS, y + 34))


def departures(stop, deps, when=None):
    """Time, line number in a black sign, destination, minutes to go right-aligned."""
    body, sign, small = font(SANS, 28), font(BOLD, 24), font(SANS, 20)
    img, d = _canvas()
    y = _header(d, when, icon="transport")
    d.text((M, y), stop, font=font(BOLD, 40), fill=0)
    y += 64
    now = datetime.now().astimezone()
    for c in deps:
        mins = max(0, int((c["time"] - now).total_seconds() // 60))
        togo = "nå" if mins == 0 else f"{mins} min"
        d.text((M, y), f"{c['time']:%H:%M}" + ("" if c["realtime"] else "*"), font=body, fill=0)
        w = max(48, sign.getlength(c["line"]) + 16)
        d.rounded_rectangle((M + 88, y + 2, M + 88 + w, y + 32), radius=6, fill=0)
        d.text((M + 88 + (w - sign.getlength(c["line"])) / 2, y + 4), c["line"], font=sign, fill=255)
        x, room = M + 100 + w, DOTS - M - body.getlength(togo) - 16 - (M + 100 + w)
        dest = c["dest"]
        while body.getlength(dest) > room:
            dest = dest[:-2] + "…"
        d.text((x, y), dest, font=body, fill=0)
        d.text((DOTS - M - body.getlength(togo), y), togo, font=body, fill=0)
        y += 44
    d.text((M, y + 6), "* = rutetid, ikke sanntid" if not all(c["realtime"] for c in deps) else "sanntid fra Entur",
           font=small, fill=0)
    return img.crop((0, 0, DOTS, y + 40))


def radar(place, lat, lon, planes, radius_km, when=None):
    """Aircraft as dots with heading ticks on range rings, north up, then a list."""
    small, row = font(SANS, 20), font(SANS, 24)
    img, d = _canvas()
    y = _header(d, when, icon="flight")
    d.text((M, y), f"Over {place}", font=font(BOLD, 40), fill=0)
    y += 72
    r, cx = COL // 2, DOTS // 2
    cy = y + r
    for f in (1, 2 / 3, 1 / 3):
        d.ellipse((cx - r * f, cy - r * f, cx + r * f, cy + r * f), outline=0, width=2)
    d.line((cx - 8, cy, cx + 8, cy), fill=0, width=3)
    d.line((cx, cy - 8, cx, cy + 8), fill=0, width=3)
    d.text((cx - small.getlength("N") / 2, cy - r - 26), "N", font=small, fill=0)
    k, shown = r / radius_km, []
    for p in planes:
        px = cx + (p["lon"] - lon) * 111 * math.cos(math.radians(lat)) * k
        py = cy - (p["lat"] - lat) * 111 * k
        if (px - cx) ** 2 + (py - cy) ** 2 > r * r:  # the API's box corners fall outside the circle
            continue
        h = math.radians(p["heading"] or 0)
        d.ellipse((px - 6, py - 6, px + 6, py + 6), fill=0)
        d.line((px, py, px + 24 * math.sin(h), py - 24 * math.cos(h)), fill=0, width=3)
        d.text((px + 10, py + 4), p["callsign"], font=small, fill=0)
        shown.append(p)
    y = cy + r + 24
    d.text((M, y), f"{len(shown)} fly · ringer hver {radius_km / 3:.0f} km", font=small, fill=0)
    y += 40
    for p in sorted(shown, key=lambda p: p["alt"] or 0)[:12]:
        info = f"{p['alt'] or 0:,.0f} m  {(p['speed'] or 0) * 3.6:.0f} km/t".replace(",", " ")
        d.text((M, y), p["callsign"], font=row, fill=0)
        d.text((DOTS - M - row.getlength(info), y), info, font=row, fill=0)
        y += 32
    return img.crop((0, 0, DOTS, y + 8))


def maze(day, when=None):
    """Daily generative print, seeded by the date so a good one can be reprinted. Full bleed."""
    rnd = random.Random(day.isoformat())
    img, d = _canvas()
    y = _header(d, when, "DAGENS KUNST") + 10
    cell, rows, truchet = 32, 22, rnd.random() < 0.5
    for row in range(rows):
        for col in range(DOTS // cell):
            x0, y0, flip = col * cell, y + row * cell, rnd.random() < 0.5
            if not truchet:  # 10 PRINT: one diagonal per cell
                d.line((x0, y0, x0 + cell, y0 + cell) if flip else (x0 + cell, y0, x0, y0 + cell), fill=0, width=4)
                continue
            corners = [((x0, y0), 0), ((x0 + cell, y0 + cell), 180)] if flip else \
                      [((x0 + cell, y0), 90), ((x0, y0 + cell), 270)]
            for (ax, ay), start in corners:  # Truchet: quarter circles around two opposite corners
                d.arc((ax - cell / 2, ay - cell / 2, ax + cell / 2, ay + cell / 2), start, start + 90, fill=0, width=4)
    y += rows * cell + 24
    d.text((M, y), f"{day:%d.%m.%Y}  ·  {'Truchet' if truchet else '10 PRINT'}", font=font(SANS, 20), fill=0)
    y += 30
    d.text((M, y), "10 PRINT CHR$(205.5+RND(1)); : GOTO 10", font=font(MONO, 18), fill=0)
    return img.crop((0, 0, DOTS, y + 30))


def parse_recipe(text):
    """Recipe markdown (recipes/_mal.md) -> (meta, title, intro lines, [(section, [(kind, text)])]),
    kind in sub | item | step | p. Same parser as recipes/print.py; the two repos don't share code."""
    lines = text.splitlines()
    meta = {}
    end = next((i for i, l in enumerate(lines[1:], 1) if l.strip() == "---"), 0) if lines[:1] == ["---"] else 0
    for l in lines[1:end]:
        if ":" in l:
            k, v = l.split(":", 1)
            meta[k.strip().lower()] = v.strip()
    title, intro, sections = "", [], []
    for l in (l.strip() for l in lines[end + 1 if end else 0:]):
        if not l:
            continue
        if l.startswith("# ") and not title:
            title = l[2:]
        elif l.startswith("## "):
            sections.append((l[3:], []))
        elif not sections:
            intro.append(l)
        elif l.startswith("### "):
            sections[-1][1].append(("sub", l[4:]))
        elif m := re.match(r"[-*]\s+(.*)", l):
            sections[-1][1].append(("item", m[1]))
        elif m := re.match(r"\d+[.)]\s+(.*)", l):
            sections[-1][1].append(("step", m[1]))
        else:
            sections[-1][1].append(("p", l))
    return meta, title, intro, sections


def is_recipe(text):
    return re.search(r"^## (Ingredienser|Ingredients)\s*$", text, re.M | re.I) is not None


@lru_cache(maxsize=8)
def _dish(path, mtime):
    """A recipe's photo across the whole paper, at most square (the middle of a tall one), dithered on its own
    so the text around it stays crisp. Cached per file version: the dither is slow, and previews repeat it."""
    from PIL import ImageOps
    pic = ImageOps.exif_transpose(Image.open(path)).convert("L")
    pic = pic.resize((DOTS, round(pic.height * DOTS / pic.width)), Image.LANCZOS)
    if pic.height > DOTS:
        top = (pic.height - DOTS) // 2
        pic = pic.crop((0, top, DOTS, top + DOTS))
    return dither(pic)


def _photo_under_title(img, y, photo):
    """-> next y. photo: a Path, or None for no photo."""
    if not photo:
        return y
    try:
        pic = _dish(str(photo), photo.stat().st_mtime)
    except OSError as e:  # not a picture PIL can read (a HEIC, a broken file): the recipe still prints
        print(f"layout: no photo from {photo.name} ({e})")
        return y
    img.paste(pic, (0, y + 6))
    return y + 6 + pic.height + 14


def recipe(text, when=None, icon=None, photo=None):
    """Kitchen card: tick boxes to check ingredients off with a pen, numbered circles for the steps.
    Shopping lists use it too (section "Varer", the bag icon). photo: the dish's picture (a Path), full width
    under the title."""
    meta, title, intro, sections = parse_recipe(re.sub(r"\*\*(.+?)\*\*", r"\1", text))
    small, body, sub, head, num = font(SANS, 20), font(SANS, 28), font(BOLD, 26), font(BOLD, 44), font(BOLD, 20)
    img, d = _canvas()
    drink = re.search(r"cocktail|drink|drikke|mocktail", meta.get("tags", ""), re.I)
    y = _header(d, when, icon=icon or ("cocktail" if drink else "cooking"))
    for line in wrap(title, head):
        d.text((M, y), line, font=head, fill=0)
        y += 52
    y = _photo_under_title(img, y, photo)
    y += 4
    for line in wrap(" ".join(intro), font(SANS, 24)):
        d.text((M, y), line, font=font(SANS, 24), fill=0)
        y += 30
    facts = " · ".join(x for x in (meta.get("porsjoner") and f"{meta['porsjoner']} porsjoner", meta.get("tid"),
                                   meta.get("tags")) if x)
    for line in wrap(facts, small):
        d.text((M, y + 6), line, font=small, fill=0)
        y += 26
    for name, items in sections:
        y += 30
        d.text((M, y), name.upper(), font=small, fill=0)
        y += 30
        boxes, n = name.lower() in ("ingredienser", "ingredients", "varer"), 0  # things to tick off with a pen
        for kind, s in items:
            if kind == "sub":
                y += 8
                d.text((M, y), s, font=sub, fill=0)
                y += 34
            elif kind == "step":
                n += 1
                d.ellipse((M, y + 1, M + 32, y + 33), fill=0)
                d.text((M + 16, y + 17), str(n), font=num, fill=255, anchor="mm")
                for line in wrap(s, body, COL - 46):
                    d.text((M + 46, y), line, font=body, fill=0)
                    y += 34
                y += 8
            elif kind == "item":
                if boxes:
                    d.rectangle((M + 1, y + 6, M + 23, y + 28), outline=0, width=3)
                else:
                    d.text((M + 4, y), "–", font=body, fill=0)
                for line in wrap(s, body, COL - 40):
                    d.text((M + 40, y), line, font=body, fill=0)
                    y += 34
                y += 4
            else:
                for line in wrap(s, body):
                    d.text((M, y), line, font=body, fill=0)
                    y += 34
    src = meta.get("kilde", "")
    if src:
        src = re.sub(r"^https?://(www\.)?([^/]+).*", r"\2", src)  # the domain is enough on paper
        y += 24
        d.text((M, y), f"kilde: {src}", font=small, fill=0)
        y += 26
    return img.crop((0, 0, DOTS, min(y + 8, MAX_H)))


INGREDIENT_SECTIONS = ("ingredienser", "ingredients")


def ingredients(text):
    """The ingredient lines of a recipe, groups flattened: what goes on the shopping list."""
    return [s for name, items in parse_recipe(text)[3] if name.lower() in INGREDIENT_SECTIONS
            for kind, s in items if kind == "item"]


def recipe_short(text, when=None, photo=None):
    """The fridge version: title and facts on one line, ingredients in two columns, steps as plain numbered
    lines in smaller type. No intro, no tips: about half the paper of recipe(). photo: as in recipe()."""
    meta, title, _, sections = parse_recipe(re.sub(r"\*\*(.+?)\*\*", r"\1", text))
    small, body, bold, head = font(SANS, 18), font(SANS, 22), font(BOLD, 22), font(BOLD, 34)
    img, d = _canvas()
    drink = re.search(r"cocktail|drink|drikke|mocktail", meta.get("tags", ""), re.I)
    y = _header(d, when, icon="cocktail" if drink else "cooking")
    for line in wrap(title, head):
        d.text((M, y), line, font=head, fill=0)
        y += 40
    y = _photo_under_title(img, y, photo)
    facts = " · ".join(x for x in (meta.get("porsjoner") and f"{meta['porsjoner']} pors.", meta.get("tid")) if x)
    if facts:
        d.text((M, y + 2), facts, font=small, fill=0)
        y += 26
    y += 12
    ingr = [e for name, items in sections if name.lower() in INGREDIENT_SECTIONS for e in items if e[0] in ("item", "sub")]
    steps = [s for name, items in sections if name.lower() not in INGREDIENT_SECTIONS for k, s in items if k == "step"]
    half, colw = (len(ingr) + 1) // 2, (COL - 16) // 2
    bottom = y
    for col, entries in enumerate((ingr[:half], ingr[half:])):  # two columns, left filled first
        x, cy = M + col * (colw + 16), y
        for kind, s in entries:
            if kind == "sub":
                d.text((x, cy + 4), s, font=bold, fill=0)
                cy += 30
                continue
            d.rectangle((x + 1, cy + 6, x + 15, cy + 20), outline=0, width=2)
            for line in wrap(s, body, colw - 24):
                d.text((x + 24, cy), line, font=body, fill=0)
                cy += 26
        bottom = max(bottom, cy)
    y = bottom + 14
    for n, s in enumerate(steps, 1):
        d.text((M, y), f"{n}.", font=bold, fill=0)
        for line in wrap(s, body, COL - 32):
            d.text((M + 32, y), line, font=body, fill=0)
            y += 26
        y += 4
    return img.crop((0, 0, DOTS, min(y + 16, MAX_H)))


def cloud(d, cx, top, width=200):
    """The web page's cloud icon (web.html), drawn for thermal paper: fill the union of three circles and the
    base black, then an inset copy white, leaving an outline of even weight with no seams. Returns the bottom y."""
    k = width / 64  # the SVG's 64-unit viewBox; the cloud spans x 5.5..59, y 2..36 in it
    ox, oy, stroke = cx - 32 * k, top - 2 * k, 3.5  # stroke in viewBox units, same as the page
    for inset, fill in ((0, 0), (stroke, 255)):
        for x, y, r in ((18, 23.5, 12.5), (35, 17, 15), (48, 25, 11)):
            d.ellipse((ox + (x - r + inset) * k, oy + (y - r + inset) * k,
                       ox + (x + r - inset) * k, oy + (y + r - inset) * k), fill=fill)
        d.rectangle((ox + 18 * k, oy + 23.5 * k, ox + 48 * k, oy + (36 - inset) * k), fill=fill)
    return oy + 36 * k


def camera(d, x, y):
    """A 44 x 32 dot camera, 3-dot strokes (thinner prints grey and patchy)."""
    d.polygon([(x + 13, y + 7), (x + 16, y), (x + 28, y), (x + 31, y + 7)], fill=0)  # viewfinder hump
    d.rounded_rectangle((x, y + 6, x + 44, y + 32), radius=5, outline=0, width=3)
    d.ellipse((x + 14, y + 11, x + 30, y + 27), outline=0, width=3)  # lens
    d.ellipse((x + 35, y + 10, x + 39, y + 14), fill=0)  # flash


def question(d, x, y):
    d.ellipse((x + 6, y, x + 38, y + 32), outline=0, width=3)
    d.text((x + 22, y + 17), "?", font=font(BOLD, 24), fill=0, anchor="mm")


def cooking(d, x, y):  # a pot with a lid
    d.rounded_rectangle((x + 6, y + 13, x + 38, y + 32), radius=4, outline=0, width=3)
    d.line((x + 2, y + 10, x + 42, y + 10), fill=0, width=3)
    d.rectangle((x + 18, y + 4, x + 26, y + 8), fill=0)
    d.line((x, y + 17, x + 6, y + 17), fill=0, width=3)
    d.line((x + 38, y + 17, x + 44, y + 17), fill=0, width=3)


def cocktail(d, x, y):  # a martini glass with an olive
    d.polygon([(x + 7, y + 1), (x + 37, y + 1), (x + 22, y + 17)], outline=0, width=3)
    d.line((x + 22, y + 17, x + 22, y + 29), fill=0, width=3)
    d.line((x + 13, y + 30, x + 31, y + 30), fill=0, width=3)
    d.line((x + 26, y - 2, x + 18, y + 9), fill=0, width=2)
    d.ellipse((x + 15, y + 6, x + 22, y + 13), fill=0)


def weather_icon(d, x, y):
    cloud(d, x + 22, y + 2, width=44)


def transport(d, x, y):  # a train, head on
    d.rounded_rectangle((x + 10, y, x + 34, y + 26), radius=5, outline=0, width=3)
    d.rectangle((x + 15, y + 5, x + 29, y + 13), outline=0, width=3)
    d.ellipse((x + 14, y + 17, x + 18, y + 21), fill=0)
    d.ellipse((x + 26, y + 17, x + 30, y + 21), fill=0)
    d.line((x + 14, y + 26, x + 9, y + 32), fill=0, width=3)
    d.line((x + 30, y + 26, x + 35, y + 32), fill=0, width=3)


def flight(d, x, y):  # a plane from above, nose right
    d.rounded_rectangle((x + 3, y + 14, x + 42, y + 18), radius=2, fill=0)
    d.polygon([(x + 22, y + 15), (x + 14, y + 1), (x + 19, y + 1), (x + 32, y + 15)], fill=0)  # swept back
    d.polygon([(x + 22, y + 17), (x + 14, y + 31), (x + 19, y + 31), (x + 32, y + 17)], fill=0)
    d.polygon([(x + 3, y + 15), (x + 6, y + 7), (x + 10, y + 7), (x + 10, y + 15)], fill=0)
    d.polygon([(x + 3, y + 17), (x + 6, y + 25), (x + 10, y + 25), (x + 10, y + 17)], fill=0)


def music(d, x, y):  # two beamed eighth notes
    d.ellipse((x + 6, y + 23, x + 16, y + 31), fill=0)
    d.ellipse((x + 26, y + 20, x + 36, y + 28), fill=0)
    d.line((x + 15, y + 27, x + 15, y + 5), fill=0, width=3)
    d.line((x + 35, y + 24, x + 35, y + 2), fill=0, width=3)
    d.polygon([(x + 14, y + 4), (x + 36, y + 1), (x + 36, y + 6), (x + 14, y + 9)], fill=0)


def idea(d, x, y):  # a light bulb
    d.ellipse((x + 11, y, x + 33, y + 22), outline=0, width=3)
    d.line((x + 17, y + 24, x + 27, y + 24), fill=0, width=3)
    d.line((x + 18, y + 29, x + 26, y + 29), fill=0, width=3)
    d.line((x + 20, y + 8, x + 22, y + 16, x + 24, y + 8), fill=0, width=2)


def shopping(d, x, y):  # a carrier bag
    d.rounded_rectangle((x + 8, y + 10, x + 36, y + 32), radius=3, outline=0, width=3)
    d.arc((x + 14, y + 1, x + 30, y + 19), 180, 360, fill=0, width=3)


def language(d, x, y):  # a speech bubble with two lines of text
    d.rounded_rectangle((x + 2, y + 1, x + 42, y + 24), radius=6, outline=0, width=3)
    d.polygon([(x + 10, y + 23), (x + 10, y + 32), (x + 19, y + 23)], fill=0)
    d.line((x + 10, y + 9, x + 34, y + 9), fill=0, width=3)
    d.line((x + 10, y + 16, x + 26, y + 16), fill=0, width=3)


def newspaper(d, x, y):  # a folded paper: a headline, a picture box and lines of text
    d.rectangle((x + 2, y + 2, x + 40, y + 31), outline=0, width=3)
    d.line((x + 8, y + 9, x + 34, y + 9), fill=0, width=3)
    d.rectangle((x + 8, y + 15, x + 18, y + 24), fill=0)
    d.line((x + 23, y + 16, x + 34, y + 16), fill=0, width=2)
    d.line((x + 23, y + 23, x + 34, y + 23), fill=0, width=2)


ICONS = {"question": question, "cooking": cooking, "cocktail": cocktail, "weather": weather_icon,
         "transport": transport, "flight": flight, "music": music, "idea": idea, "photo": camera,
         "shopping": shopping, "language": language, "news": newspaper}


def photo(picture, when=None):
    """A photo, full bleed at 576 dots, under a camera icon (left) and when it was printed (right).
    Stays grayscale: the whole page gets dithered at the end, so brightness/contrast edits still reach it."""
    picture = picture.convert("L").resize((DOTS, round(picture.height * DOTS / picture.width)), Image.LANCZOS)
    small = font(SANS, 20)
    img, d = _canvas()
    top = 72 if header_on() else 0
    if top:
        ICONS.get(getattr(HEADER, "icon", None) or "photo", camera)(d, M, 24)
        stamp = f"{when or datetime.now():%d.%m.%Y %H:%M}"
        d.text((DOTS - M - small.getlength(stamp), 30), stamp, font=small, fill=0)
    img.paste(picture, (0, top))
    return img.crop((0, 0, DOTS, min(top + picture.height + 8, MAX_H)))


def news(n, when=None):
    """The news on paper: n is news.nrk_night() or news.world(), {"title", "subtitle", "source", "items":
    [{"headline", "summary"}]}. A title, where it's from, then each story: its headline in bold and a line or
    three from it, with a rule between them."""
    small, title, head, body = font(SANS, 20), font(BOLD, 40), font(BOLD, 27), font(SANS, 23)
    img, d = _canvas()
    y = _header(d, when, icon="news")
    for line in wrap(n["title"], pick(title, n["title"])):
        d.text((M, y), line, font=pick(title, line), fill=0)
        y += 48
    d.text((M, y + 2), f"{n['subtitle']}  ·  {n['source']}", font=small, fill=0)
    y += 36
    for i, it in enumerate(n["items"]):
        d.line((M, y, DOTS - M, y), fill=0, width=2 if i == 0 else 1)
        y += 14
        for line in wrap(it["headline"], pick(head, it["headline"])):
            d.text((M, y), line, font=pick(head, line), fill=0)
            y += 34
        if it.get("summary"):
            y += 2
            for line in wrap(it["summary"], pick(body, it["summary"])):
                d.text((M, y), line, font=pick(body, line), fill=0)
                y += 29
        y += 14
        if y > MAX_H - 200:  # the paper guard: rather fewer stories than a cut-off one
            break
    return img.crop((0, 0, DOTS, min(y + 8, MAX_H)))


def word(e, label, when=None):
    """Word or character of the day. e: word, reading, kind, meaning, example, example_reading,
    example_meaning, note (see words.py). The word as large as fits, in a face for its script."""
    small, mid = font(SANS, 20), font(SANS, 26)
    img, d = _canvas()
    y = _header(d, when, icon="language")
    d.text((M, y), label, font=small, fill=0)
    y += 40
    big = next(f for size in (150, 130, 110, 96, 84, 72, 60, 48, 40)  # a single character gets the most room
               if (f := pick(font(BOLD, size), e["word"])).getlength(e["word"]) <= COL)
    top = big.getbbox(e["word"])[1]
    d.text((DOTS / 2, y - top), e["word"], font=big, fill=0, anchor="ma")  # ink top exactly at y
    y += big.getbbox(e["word"])[3] - top + 18
    for text, f in ((e.get("reading", ""), mid), (e.get("kind", ""), small)):
        if text:
            f = pick(f, text)
            d.text((DOTS / 2, y), text, font=f, fill=0, anchor="mt")
            y += int(f.size) + 10
    y += 14
    f = pick(font(BOLD, 30), e["meaning"])
    for line in wrap(e["meaning"], f):
        d.text((DOTS / 2, y), line, font=f, fill=0, anchor="mt")
        y += 38
    y += 26
    for text, size, bold in ((e.get("example", ""), 28, False), (e.get("example_reading", ""), 20, False),
                             (e.get("example_meaning", ""), 24, False), (e.get("note", ""), 20, False)):
        if not text:
            continue
        f = pick(font(BOLD if bold else SANS, size), text)
        if text is e.get("note"):
            y += 16
        for line in wrap(text, f):
            d.text((M, y), line, font=f, fill=0)
            y += size + 8
        y += 4
    return img.crop((0, 0, DOTS, min(y + 16, MAX_H)))


def qr(url, when=None):
    """A wall label: the cloud, the web page's address as a QR code, and the address in text."""
    import qrcode
    code = qrcode.QRCode(border=0, error_correction=qrcode.constants.ERROR_CORRECT_M)
    code.add_data(url)
    code.make()
    matrix = code.get_matrix()
    n = len(matrix)
    s = max(1, min(14, COL // n))  # whole dots per module: fractional scaling gives ragged, unscannable edges
    img, d = _canvas()
    y = round(cloud(d, DOTS / 2, 48)) + 4 * s  # a four-module quiet zone above the code
    x0 = (DOTS - n * s) // 2
    for r, row in enumerate(matrix):
        for c, dark in enumerate(row):
            if dark:
                d.rectangle((x0 + c * s, y + r * s, x0 + (c + 1) * s - 1, y + (r + 1) * s - 1), fill=0)
    y += n * s + 4 * s
    f = font(SANS, 26)
    d.text(((DOTS - f.getlength(url)) / 2, y), url, font=f, fill=0)
    return img.crop((0, 0, DOTS, y + 44))


def lcd(title, text="", size=16, w=128, h=64):
    """Status screen for the F615's own LCD: one bold line, then small lines as far as they fit."""
    img = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(img)
    d.fontmode = "1"  # no antialiasing: grey edges threshold into ragged glyphs on 1-bit glass
    d.text((2, 1), title, font=font(BOLD, size), fill=0)
    small, top = font(SANS, 11), size + 6
    for i, line in enumerate(wrap(text, small, w - 4)[:(h - top) // 13]):
        d.text((2, top + i * 13), line, font=small, fill=0)
    return img


def dither(img, width=DOTS):
    """Atkinson dither for pictures/art (not text). Returns pure 0/255 at `width` dots wide."""
    img = img.convert("L")
    img = img.resize((width, round(img.height * width / img.width)))
    w, h = img.size
    px = [float(v) for v in img.tobytes()]
    for i in range(w * h):
        new = 0.0 if px[i] < 128 else 255.0
        err, px[i] = (px[i] - new) / 8, new
        x = i % w
        for dx, dy in ((1, 0), (2, 0), (-1, 1), (0, 1), (1, 1), (0, 2)):
            if 0 <= x + dx < w and i + dx + dy * w < w * h:
                px[i + dx + dy * w] += err
    out = Image.new("L", (w, h))
    out.putdata([int(v) for v in px])
    return out


def to_rows(img):
    """Hard threshold, one printer row per pixel row. Dither pictures first (dither()), never text."""
    assert img.width == DOTS, f"image is {img.width} wide, render at {DOTS}"
    bits = img.convert("L").point(lambda v: 255 if v < 128 else 0, mode="1").tobytes()
    return [bits[i:i + ROW] for i in range(0, len(bits), ROW)]


def from_rows(rows):
    img = Image.frombytes("1", (DOTS, len(rows)), b"".join(rows))
    return img.point(lambda v: 0 if v else 255, mode="L")
