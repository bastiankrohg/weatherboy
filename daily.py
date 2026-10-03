"""The daily prints: art and a word of the day, at a set time, switched on and off from the web page.
The due-or-not rule is due(); the loop in run() retries each minute until the printer takes it."""
import envfile  # noqa: F401 - .env loaded before the settings below are read
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

DATA = Path(__file__).with_name("data")
SETTINGS = DATA / "daglig.json"
DONE = DATA / "daglig_utskrevet.json"
QUIET_FROM = (22, 0)  # no scheduled prints after this; pos_printer.md's quiet hours start 22:30
DEFAULTS = {"time": os.environ.get("WEATHERBOY_ART_AT", "12:00") or "12:00",
            "art": bool(os.environ.get("WEATHERBOY_ART_AT", "12:00")), "word": True, "lang": "ko"}
JOBS = ("art", "word")  # printed in this order, so they come out together
_lock = threading.Lock()


def _read(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _write(path, data):
    DATA.mkdir(exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def settings():
    with _lock:
        return {**DEFAULTS, **_read(SETTINGS, {})}


def update(changes):
    """From the page: {"art": false}, {"lang": "fr"}, {"time": "09:00"} … Unknown keys are refused."""
    import words
    with _lock:
        s = {**DEFAULTS, **_read(SETTINGS, {})}
        for k, v in changes.items():
            if k in ("art", "word"):
                s[k] = bool(v)
            elif k == "lang" and v in words.LANGS:
                s[k] = v
            elif k == "time" and len(v) == 5 and v[2] == ":" and 0 <= int(v[:2]) < 24 and 0 <= int(v[3:]) < 60:
                s[k] = v
            else:
                raise ValueError(f"Ugyldig innstilling: {k}={v!r}")
        _write(SETTINGS, s)
        return s


def done():
    """{"art": "2026-09-30", "word": …}: the day each job last reached paper."""
    old = DATA / "dagens_kunst.json"  # the art-only job's record, before words existed
    return _read(DONE, {"art": _read(old, {}).get("date")} if old.exists() else {})


def due(now, at, last):
    """Print now? From `at` until quiet hours, unless it already went out today."""
    if last == now.date().isoformat():
        return False
    hh, mm = map(int, at.split(":"))
    return (hh, mm) <= (now.hour, now.minute) < QUIET_FROM


def run(print_job):
    """Every minute: each switched-on job that's due gets printed by print_job(name), which raises while the
    printer is off, busy or out of paper; then it's simply tried again next minute."""
    waiting = None
    while True:
        now, s = datetime.now(), settings()
        record = done()
        for job in JOBS:
            if s[job] and due(now, s["time"], record.get(job)):
                try:
                    print_job(job, s)
                    record[job] = now.date().isoformat()
                    _write(DONE, record)
                    print(f"daily {job} printed {now:%H:%M}")
                except Exception as e:  # noqa: BLE001 - any failure means "try again next minute"
                    if waiting != (job, now.date()):  # say it once a day per job, not every minute
                        print(f"daily {job}: waiting ({e})")
                        waiting = (job, now.date())
                    break  # keep the order: the word waits for the art
        time.sleep(60)
