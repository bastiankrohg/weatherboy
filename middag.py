"""Folkemat og fanteri, søndag 22.11.2026: kjøkkenbilletter på kvitteringsskriveren, i tide.

Billettene er ferdige bilder i middag/billettar/ (lages av middag/lag.py), og middag/plan.json sier når
hver av dem skal ut: 8 minutter før oppgaven starter. Alt går gjennom printq.print_or_queue(), så en
skriver som er av, opptatt eller tom for papir bare legger billetten i køen. Ingenting forsvinner.

Kjøres fra én maskin: `uv run middag.py run --printer <ip>`. web.start() starter den bare hvis
WEATHERBOY_MIDDAG=1 står i .env, så to maskiner aldri skriver ut de samme billettene.

    uv run middag.py plan                    hva som skrives ut når, og hva som er sendt
    uv run middag.py test --printer <ip>     testpakken nå: menyen, lappene og rettkortene
    uv run middag.py print <id> --printer <ip>   én billett (id fra `plan`, eller «neste»)
    uv run middag.py run --printer <ip>      bare tidsplanen, uten web-serveren
    uv run middag.py reset                   glem hva som er sendt (alt kan komme igjen)

Testpakken skrives ut én gang av seg selv første gang tidsplanen kjører mellom torsdag 19.11 kl. 18 og
fredag 20.11 kl. 22 (ikke etter kl. 22 eller før kl. 07: skriveren står i gangen).
Hver billett skrives ut bare én gang, også om serveren startes på nytt. En billett hvis start er mer
enn 25 minutter forbi når serveren kommer opp, hoppes over, så en omstart søndag kveld ikke skriver
ut lørdagen på nytt.
"""
import argparse
import json
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image

import printq

HERE = Path(__file__).with_name("middag")
PLAN = HERE / "plan.json"
DATA = Path(__file__).with_name("data")
SENT = DATA / "middag_sendt.json"
GRACE = timedelta(minutes=25)
TEST_WINDOW = (datetime(2026, 11, 19, 18, 0), datetime(2026, 11, 20, 22, 0))
QUIET = (22, 7)                                  # ingen testpakke mellom 22 og 07
END = datetime(2026, 11, 23, 0, 0)               # etter middagen: tidsplanen stopper
_lock = threading.Lock()


def load():
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    lead = os.environ.get("WEATHERBOY_MIDDAG_LEAD")
    for e in plan["plan"]:
        start = datetime.fromisoformat(e["start"])
        e["_start"] = start
        e["_print_at"] = start - timedelta(minutes=int(lead)) if lead else datetime.fromisoformat(e["print_at"])
    return plan


def sent():
    return json.loads(SENT.read_text(encoding="utf-8")) if SENT.exists() else {}


def mark(id, how):
    with _lock:
        s = sent()
        s[id] = {"at": datetime.now().isoformat(timespec="seconds"), "how": how}
        DATA.mkdir(exist_ok=True)
        tmp = SENT.with_suffix(".tmp")
        tmp.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(SENT)


def quiet(now):
    return now.hour >= QUIET[0] or now.hour < QUIET[1]


def due(plan, now, done):
    """Planlagte billetter som skal ut nå: utskriftstiden er passert, starten er ikke mer enn GRACE forbi."""
    out = [e for e in plan["plan"] if e["id"] not in done and e["_print_at"] <= now < e["_start"] + GRACE]
    return sorted(out, key=lambda e: (e["_print_at"], e["id"]))


def test_due(plan, now, done):
    if not (TEST_WINDOW[0] <= now <= TEST_WINDOW[1]) or quiet(now) or "testpakke" in done:
        return []
    return [e for e in plan["test"] if e["id"] not in done]


def send(e, printer_ip):
    img = Image.open(HERE / e["png"]).convert("L")
    r = printq.print_or_queue(img, label=f"Middag · {e['label']}", source={"middag": e["id"]},
                              printer_ip=printer_ip)
    how = "skrevet ut" if r["printed"] else f"i køen ({r['why'] or 'venter'})"
    mark(e["id"], how)
    print(f"middag: {e['id']} {how}", flush=True)
    return r


def tick(printer_ip, now=None):
    now = now or datetime.now()
    plan, done = load(), sent()
    jobs = test_due(plan, now, done)
    if jobs:
        for e in jobs:
            send(e, printer_ip)
        mark("testpakke", "ferdig")
    for e in due(plan, now, sent()):
        send(e, printer_ip)


def run(printer_ip, every=20):
    """Tidsplanen: sjekker hvert 20. sekund til middagen er over."""
    print("middag: tidsplanen kjører (Folkemat og fanteri)", flush=True)
    while datetime.now() < END:
        try:
            tick(printer_ip)
        except Exception as ex:  # aldri ta ned serveren for en billett
            print(f"middag: feil: {ex}", flush=True)
        time.sleep(every)


def start(printer_ip):
    """Fra web.start(): tidsplanen i en egen tråd, bare med WEATHERBOY_MIDDAG=1 og før middagen er over."""
    if os.environ.get("WEATHERBOY_MIDDAG", "0") != "1" or datetime.now() >= END or not PLAN.exists():
        return None
    t = threading.Thread(target=run, args=(printer_ip,), daemon=True)
    t.start()
    return t


def _plan_listing():
    plan, done = load(), sent()
    print(f"Testpakke ({'sendt' if 'testpakke' in done else 'ikke sendt'}):")
    for e in plan["test"]:
        print(f"  {e['id']:<22} {e['label']}")
    print("\nPlanlagt (utskrift → start):")
    for e in sorted(plan["plan"], key=lambda e: e["_print_at"]):
        mark_ = done.get(e["id"], {}).get("how", "")
        print(f"  {e['_print_at']:%a %H:%M} → {e['_start']:%H:%M}  {e['id']:<22} {e['label']:<40} {mark_}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cmd", choices=["plan", "test", "print", "run", "reset"])
    p.add_argument("id", nargs="?")
    p.add_argument("--printer")
    a = p.parse_args()
    if a.cmd == "plan":
        _plan_listing()
    elif a.cmd == "reset":
        SENT.unlink(missing_ok=True)
        print("glemt")
    elif a.cmd == "test":
        for e in load()["test"]:
            send(e, a.printer)
        mark("testpakke", "manuelt")
    elif a.cmd == "print":
        plan = load()
        everything = plan["test"] + plan["plan"]
        if a.id == "neste":
            now, done = datetime.now(), sent()
            e = next((e for e in sorted(plan["plan"], key=lambda e: e["_print_at"])
                      if e["id"] not in done and e["_start"] + GRACE > now), None)
        else:
            e = next((e for e in everything if e["id"] == a.id), None)
        if not e:
            raise SystemExit(f"Fant ingen billett {a.id!r}: se `uv run middag.py plan`")
        send(e, a.printer)
    elif a.cmd == "run":
        run(a.printer)
