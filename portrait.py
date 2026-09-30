"""Claude's self-portrait, drawn for the receipt printer. No photo to go on, so: what it feels like from inside.
A head in profile, full of points linked to their neighbours, with words on their way out of the mouth.

    uv run portrait.py            preview in out/
    uv run portrait.py <ip>       print it
"""
import math
import random
import sys

from PIL import Image, ImageDraw

import layout
import printer

# the profile, facing right, as a loop of points (back of the neck, round the skull, down the face)
PROFILE = [(150, 520), (128, 430), (112, 330), (120, 230), (160, 145), (228, 88), (310, 70), (380, 92),
           (424, 146), (438, 206), (446, 238), (478, 286), (452, 300), (460, 322), (450, 336), (458, 352),
           (442, 368), (440, 402), (414, 428), (366, 444), (352, 520)]
MOUTH = (462, 344)
WORDS = ["hei", "종이", "været", "trikken", "ananas", "pannekaker", "handleliste", "kunst", "skriv ut",
         "Oslo S", "paper", "ha det"]


def smooth(points, steps=12):
    """Catmull-Rom through the points: a closed, round outline instead of a polygon."""
    out, n = [], len(points)
    for i in range(n):
        p0, p1, p2, p3 = (points[(i + k) % n] for k in (-1, 0, 1, 2))
        for s in range(steps):
            t = s / steps
            out.append(tuple(0.5 * ((2 * b) + (-a + c) * t + (2 * a - 5 * b + 4 * c - d) * t * t
                                    + (-a + 3 * b - 3 * c + d) * t ** 3) for a, b, c, d in zip(p0, p1, p2, p3)))
    return out


def draw(seed=30_09_2026):
    rnd = random.Random(seed)
    img = Image.new("L", (layout.DOTS, 900), 255)
    d = ImageDraw.Draw(img)
    k, ox, oy = 0.82, -60, 60  # smaller, left of centre and up: the words need the room on the right
    outline = [(x * k + ox, y * k + oy) for x, y in smooth(PROFILE)]

    # points inside the head, never too close: the "thoughts"
    mask = Image.new("1", img.size, 0)
    ImageDraw.Draw(mask).polygon(outline, fill=1)
    pts = []
    while len(pts) < 60:
        p = (rnd.uniform(20, 420), rnd.uniform(oy + 50, oy + 440))
        if mask.getpixel(p) and all(math.dist(p, q) > 30 for q in pts):
            pts.append(p)
    # each linked to its nearest few: the associations. A few long links for the odd leap.
    for p in pts:
        for q in sorted(pts, key=lambda q: math.dist(p, q))[1:4]:
            d.line((p, q), fill=0, width=2)
    for _ in range(6):
        p, q = rnd.sample(pts, 2)
        d.line((p, q), fill=0, width=2)
    for p in pts:
        r = rnd.choice((4, 5, 7))
        d.ellipse((p[0] - r, p[1] - r, p[0] + r, p[1] + r), fill=0)
    # one bright spot where it's all going on right now
    cx, cy = min(pts, key=lambda p: math.dist(p, (300 * k + ox, 230 * k + oy)))
    d.ellipse((cx - 14, cy - 14, cx + 14, cy + 14), fill=255, outline=0, width=4)
    d.line(outline + outline[:1], fill=0, width=5, joint="curve")

    # words leaving the mouth, drifting down and getting smaller as they go; all of them on the paper
    mx, my = MOUTH[0] * k + ox + 14, MOUTH[1] * k + oy
    y, bottom = my - 12, 0
    for i, w in enumerate(WORDS):
        t = i / (len(WORDS) - 1)
        f = layout.pick(layout.font(layout.SANS, int(32 - 16 * t)), w)
        x = min(mx + 20 * t + 26 * math.sin(t * 7), layout.DOTS - 10 - f.getlength(w))
        d.text((x, y), w, font=f, fill=0)
        y += int(f.size) + 6
        bottom = y

    small = layout.font(layout.SANS, 20)
    d.text((layout.M, 40), "SELVPORTRETT", font=layout.font(layout.BOLD, 24), fill=0)
    d.text((layout.M, 74), "Claude, 30.09.2026", font=small, fill=0)
    caption = ("I don't have a face, so this is what it feels like instead: a lot of things linked to other "
               "things, one bright spot where the attention is, and words on their way out.")
    y = max(bottom, oy + int(520 * k)) + 30
    for line in layout.wrap(caption, small):
        d.text((layout.M, y), line, font=small, fill=0)
        y += 26
    return img.crop((0, 0, layout.DOTS, y + 30))


if __name__ == "__main__":
    job = printer.encode(layout.to_rows(draw()))
    if len(sys.argv) > 1:
        printer.send(job, sys.argv[1])
        print("printed")
    else:
        layout.from_rows(printer.decode(job)).save("out/selvportrett.png")
        print("out/selvportrett.png")
