"""Receipts waiting for a printer that can't take them yet. One PNG per job in data/printq/, and
data/printq.json as the order.

    python printq.py             what's waiting
    python printq.py 192.168.0.108   print everything unheld, in order, then list what didn't go

Everything that prints goes through print_or_queue(): the job goes out now if it can, and stays in
the queue if the printer is unconfigured, off, busy or out of paper. Nothing is lost, and the web
page can reorder, hold, edit and drop jobs while they wait. drain() is the worker that empties the
queue as soon as the printer takes a job again.

The queue stores the finished PNG rather than the encoded job: smaller, and it is the same image
the page previewed and its editor round-trips.
"""
import argparse
import json
import threading
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from PIL import Image

import layout
import printer

DATA = Path(__file__).with_name("data")
PEDIA = DATA / "printq"                      # one PNG per job
FILE = DATA / "printq.json"                  # the index, and its order IS the queue
MAX = 50                                     # refuse rather than silently drop the oldest
LABEL_MAX = 60
POOL = 10                                    # seconds between attempts; don't hammer a printer that's out of paper
DRAIN = threading.Lock()                     # one job at a time, and never next to a manual "print all"
_lock = threading.Lock()                     # the index


def _read(default):
    return json.loads(FILE.read_text(encoding="utf-8")) if FILE.exists() else default


def _write(jobs):
    DATA.mkdir(exist_ok=True)
    PEDIA.mkdir(exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(jobs, ensure_ascii=False), encoding="utf-8")
    tmp.replace(FILE)
    keep = {f"{j['id']}.png" for j in jobs}
    for png in PEDIA.glob("*.png"):           # a job that was removed leaves its picture behind
        if png.name not in keep:
            png.unlink(missing_ok=True)


def items():
    with _lock:
        return _read([])


def get(id):
    with _lock:
        return next((j for j in _read([]) if j["id"] == id), None)


def count():
    jobs = items()
    return len(jobs), sum(1 for j in jobs if j["hold"])


def add(img, label="kvittering", source=None, hold=False, dither=False):
    """The finished receipt, as a PIL image. source is whatever made it (the transcript, the card
    name, the request), kept so the page can explain the job and re-render it later. dither is how it
    was halftoned, so editing it later starts from the same look."""
    with _lock:
        jobs = _read([])
        if len(jobs) >= MAX:
            raise ValueError(f"Køen er full ({MAX} kvitteringer).")
        job = {"id": uuid4().hex[:8], "label": str(label)[:LABEL_MAX],
               "added": datetime.now().isoformat(timespec="seconds"), "hold": bool(hold),
               "dither": bool(dither), "source": source or None}
        job["png"] = f"{PEDIA.name}/{job['id']}.png"
        PEDIA.mkdir(parents=True, exist_ok=True)
        img.convert("L").save(PEDIA / f"{job['id']}.png", "PNG")
        jobs.append(job)
        _write(jobs)
    return job


def remove(ids):
    """Drop these jobs and their pictures. Returns the ones that were actually there."""
    ids = {ids} if isinstance(ids, str) else set(ids)
    with _lock:
        jobs = _read([])
        gone = [j for j in jobs if j["id"] in ids]
        _write([j for j in jobs if j["id"] not in ids])
    return gone


def move(id, delta):
    """One step up the queue (delta -1) or down (+1). At either end nothing happens."""
    with _lock:
        jobs = _read([])
        i = next((n for n, j in enumerate(jobs) if j["id"] == id), None)
        if i is None:
            raise KeyError(id)
        j = i + delta
        if 0 <= j < len(jobs):
            jobs[i], jobs[j] = jobs[j], jobs[i]
            _write(jobs)
        return next(x for x in jobs if x["id"] == id)


def hold(id, on):
    """Hold this job back, or let it print again. Held jobs are skipped, never dropped: that's the
    difference between "not now" and "I don't want it"."""
    with _lock:
        jobs = _read([])
        job = next((j for j in jobs if j["id"] == id), None)
        if job is None:
            raise KeyError(id)
        job["hold"] = bool(on)
        _write(jobs)
        return job


def replace_img(id, img):
    """A job edited on the page comes back with new pixels, same id and place in the order."""
    with _lock:
        if not any(j["id"] == id for j in _read([])):
            raise KeyError(id)
        PEDIA.mkdir(parents=True, exist_ok=True)
        img.convert("L").save(PEDIA / f"{id}.png", "PNG")


def image(job):
    """The stored PNG, ready for layout.to_rows."""
    return Image.open(PEDIA / f"{job['id']}.png").convert("L")


def png_path(id):
    return PEDIA / f"{id}.png"


def print_or_queue(img, label="kvittering", source=None, printer_ip=None, hold=False, dither=False):
    """Print this receipt now; if that can't be done, keep it instead of losing it.

    -> {"printed": bool, "id": the queued job or None, "why": what stopped it, or None}"""
    if printer_ip:
        try:
            printer.send(printer.encode(layout.to_rows(img)), printer_ip)
            return {"printed": True, "id": None, "why": None}
        except Exception as e:  # noqa: BLE001 - a receipt must not be lost, not even to our own bug
            why = str(e) or type(e).__name__
    else:
        why = "ingen skriver er valgt"
    return {"printed": False, "id": add(img, label, source, hold, dither)["id"], "why": why}


def print_one(printer_ip):
    """The next job that isn't held. Nothing comes off the queue until the printer has taken it."""
    with DRAIN:
        with _lock:
            job = next((j for j in _read([]) if not j["hold"]), None)
        if job is None:
            return None
        printer.send(printer.encode(layout.to_rows(image(job))), printer_ip)
        with _lock:
            _write([j for j in _read([]) if j["id"] != job["id"]])
    return job


def print_now(printer_ip, limit=MAX):
    """Print everything unheld, in order, stopping at the first refusal. -> how many went out."""
    n = 0
    while n < limit:
        try:
            if print_one(printer_ip) is None:
                break
        except Exception as e:  # noqa: BLE001 - out of paper or off: the rest stays for next time
            print(f"printq: stoppet ({e})")
            break
        n += 1
    return n


def drain(printer_ip, tick=POOL):
    """Empty the queue as soon as the printer takes jobs again. A daemon thread from web.start().
    Held jobs are skipped, never dropped: one you moved back stays where you put it."""
    waiting = None
    while True:
        try:
            job = print_one(printer_ip)
        except Exception as e:  # noqa: BLE001 - off, busy or out of paper: try again next tick
            job = None
            if waiting != str(e):
                print(f"printq: venter ({e})")  # say it once, not every ten seconds
                waiting = str(e)
        if job is None:
            time.sleep(tick)
            continue
        waiting = None
        print(f"printq: skrev ut {job['label']!r}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("printer", nargs="?", help="printer IP; with it, print everything unheld first")
    a = p.parse_args()
    if a.printer:
        print(f"skrev ut {print_now(a.printer)}")
    waiting = items()
    for j in waiting:
        print(f"{j['added'][11:]}  {'hold' if j['hold'] else '    '}  {j['label']}")
    print(f"{len(waiting)} i køen" if waiting else "ingen i køen")