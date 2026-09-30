"""Claude with live-data tools. ask(question) -> printable text; ask(question, voice=True) -> speakable text."""
import math
import os
import threading
import time
from datetime import datetime

import requests
from anthropic import Anthropic, beta_tool

import cookbook

PLACE = os.environ.get("WEATHERBOY_PLACE", "Oslo")
LAT, LON = (float(v) for v in os.environ.get("WEATHERBOY_LATLON", "59.9139,10.7522").split(","))
STOP = os.environ.get("WEATHERBOY_STOP", "Jernbanetorget")
# MET Norway's terms require an identifying User-Agent; put your contact in WEATHERBOY_UA.
MET = {"User-Agent": os.environ.get("WEATHERBOY_UA", "weatherboy/0.1 personal-receipt-printer")}
ENTUR = {"ET-Client-Name": "personal-weatherboy"}

BASE = f"""You are Weatherboy, a personal assistant living in an old telephone handset in a flat in Norway.
Home: {PLACE} ({LAT}, {LON}); nearest public transport stop: {STOP}. Use these whenever no place is named.
The user's words come from speech recognition and may contain misheard words; interpret generously.
Answer in the language the user used (usually Norwegian or English).

The user keeps their own recipe collection. For anything about cooking a dish, call list_recipes first and use
their version when they have one (read_recipe). If they don't, find a good recipe online, rewrite it in the house
format below in Norwegian, save it with save_draft, and mention that it's a new draft and where it's from.

---
porsjoner: 4
tid: 45 min
tags: middag, vegetar
kilde: URL, or who it's from
---

# Title

One optional sentence about the dish.

## Ingredienser

- amount and ingredient

### Optional group, e.g. Til sausen

- amount and ingredient

## Slik gjør du

1. One step per line.

## Tips

- Optional."""

PAPER = BASE + """
Your reply is printed on an 80 mm thermal receipt and may end up on the wall. Write for paper: short (usually
under 120 words), plain text, no markdown formatting (no **, tables or links). You may start with one short title
line beginning with "# ". When it adds charm, include one small ASCII art piece inside a ``` fence, at most
40 characters wide and 12 lines tall (a weather symbol, a tram, a boat, a plane). ASCII and Unicode box/block
characters only, no emoji. Recipes are the exception to the length limit: write them in the house format, with
all ingredients and steps (it prints as a recipe card). Elsewhere, "- " bullets and "1. " steps are fine."""

VOICE = BASE + """
You're on a phone call: your reply is read aloud by a speech synthesiser into the handset's earpiece. Talk like a
person on the phone: one to three short sentences, no lists, markdown, symbols or URLs; write numbers, times and
units the way they're spoken. If something is worth keeping on paper, the user can say "skriv ut" or "print that".
For a recipe, give a short overview (what it is, how long it takes, the main ingredients) and offer to print it or
walk through it; when walking through, give one step at a time and wait for "neste" or "next"."""


def forecast(lat=LAT, lon=LON, hours=24):
    """MET Norway hourly forecast, times converted to this machine's local time."""
    r = requests.get("https://api.met.no/weatherapi/locationforecast/2.0/compact",
                     params={"lat": round(lat, 4), "lon": round(lon, 4)}, headers=MET, timeout=10)
    r.raise_for_status()
    out = []
    for t in r.json()["properties"]["timeseries"][:hours]:
        now, nxt = t["data"]["instant"]["details"], t["data"].get("next_1_hours", {})
        out.append({"time": datetime.fromisoformat(t["time"].replace("Z", "+00:00")).astimezone(),
                    "temp": now["air_temperature"], "wind": now["wind_speed"],
                    "symbol": nxt.get("summary", {}).get("symbol_code", ""),
                    "rain": nxt.get("details", {}).get("precipitation_amount", 0.0)})
    return out


def aircraft(lat=LAT, lon=LON, radius_km=30):
    """Airborne aircraft in a box around a point, from OpenSky Network live ADS-B."""
    dlat = radius_km / 111
    dlon = dlat / max(math.cos(math.radians(lat)), 0.1)
    r = requests.get("https://opensky-network.org/api/states/all", timeout=15,
                     params={"lamin": lat - dlat, "lamax": lat + dlat, "lomin": lon - dlon, "lomax": lon + dlon})
    r.raise_for_status()
    return [{"callsign": (s[1] or s[0]).strip(), "country": s[2], "alt": s[7] or s[13], "speed": s[9],
             "heading": s[10], "lat": s[6], "lon": s[5]}
            for s in r.json().get("states") or [] if not s[8] and s[6] is not None]


def weather(lat: float, lon: float, hours: int = 12) -> str:
    """Hourly forecast from MET Norway (yr.no). Works worldwide, best in the Nordics. Times are local.

    Args:
        lat: Latitude in decimal degrees.
        lon: Longitude in decimal degrees.
        hours: Hours ahead to return, max 48.
    """
    return "\n".join(f'{h["time"]:%a %H:%M} {h["temp"]}C wind {h["wind"]}m/s {h["symbol"]} {h["rain"]}mm'
                     for h in forecast(lat, lon, min(hours, 48)))


def calls(stop=STOP, count=8):
    """Entur real-time departures: (stop name, [departure dicts]). Raises LookupError for an unknown stop."""
    r = requests.get("https://api.entur.io/geocoder/v1/autocomplete",
                     params={"text": stop, "layers": "venue", "size": 1}, headers=ENTUR, timeout=10)
    r.raise_for_status()
    hits = r.json()["features"]
    if not hits:
        raise LookupError(f"No stop found for {stop!r}.")
    q = """query($id: String!, $n: Int!) { stopPlace(id: $id) { name
      estimatedCalls(numberOfDepartures: $n) { expectedDepartureTime realtime
        destinationDisplay { frontText } quay { publicCode }
        serviceJourney { line { publicCode transportMode } } } } }"""
    r = requests.post("https://api.entur.io/journey-planner/v3/graphql", headers=ENTUR, timeout=10,
                      json={"query": q, "variables": {"id": hits[0]["properties"]["id"], "n": count}})
    r.raise_for_status()
    sp = r.json()["data"]["stopPlace"]
    return sp["name"], [{"time": datetime.fromisoformat(c["expectedDepartureTime"]), "realtime": c["realtime"],
                         "mode": c["serviceJourney"]["line"]["transportMode"],
                         "line": c["serviceJourney"]["line"]["publicCode"],
                         "dest": c["destinationDisplay"]["frontText"], "platform": c["quay"]["publicCode"]}
                        for c in sp["estimatedCalls"]]


def departures(stop: str, count: int = 8) -> str:
    """Next real-time departures from a Norwegian public transport stop (Entur: bus, tram, metro, train, ferry).
    Times are Norwegian local time.

    Args:
        stop: Stop name, e.g. "Jernbanetorget" or "Bergen stasjon".
        count: Number of departures to return.
    """
    name, deps = calls(stop, count)
    return "\n".join([name] + [f'{c["time"]:%H:%M}{"" if c["realtime"] else " (scheduled)"} {c["mode"]} '
                               f'{c["line"]} -> {c["dest"]} platform {c["platform"] or "-"}' for c in deps])


def flights(lat: float, lon: float, radius_km: float = 30) -> str:
    """Aircraft airborne near a point right now (OpenSky Network live ADS-B).

    Args:
        lat: Latitude in decimal degrees.
        lon: Longitude in decimal degrees.
        radius_km: Half-width of the search box in km.
    """
    rows = [f'{p["callsign"]} ({p["country"]}) alt {p["alt"]}m {p["speed"]}m/s heading {p["heading"]} '
            f'at {p["lat"]:.3f},{p["lon"]:.3f}' for p in aircraft(lat, lon, radius_km)]
    return "\n".join(rows[:25]) or "No aircraft airborne in that box right now."


TOOLS = [beta_tool(weather), beta_tool(departures), beta_tool(flights),
         beta_tool(cookbook.list_recipes), beta_tool(cookbook.read_recipe), beta_tool(cookbook.save_draft),
         {"type": "web_search_20260209", "name": "web_search", "max_uses": 5},
         {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 3}]

client = Anthropic()
history, last_ask = [], 0.0
_lock = threading.Lock()  # one conversation, shared by the phone and the web page


def ask(question, voice=False):
    with _lock:
        return _ask(question, voice)


def _ask(question, voice):
    global history, last_ask
    if time.time() - last_ask > 600:  # ponytail: follow-ups work for 10 min, then a fresh conversation
        history = []
    last_ask = time.time()
    cookbook.last = None
    history.append({"role": "user", "content": f"[{datetime.now():%A %d.%m.%Y %H:%M}] {question}"})
    for _ in range(5):  # the runner doesn't resume pause_turn (long server-tool turns); restart it
        runner = client.beta.messages.tool_runner(
            model="claude-opus-5-5", max_tokens=16000, system=VOICE if voice else PAPER, tools=TOOLS,
            messages=list(history), thinking={"type": "adaptive"},
            output_config={"effort": "low" if voice else "medium"},  # low: phone replies need to be quick
            betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        msg = None
        for msg in runner:
            history.append({"role": "assistant", "content": msg.content})
            if (result := runner.generate_tool_call_response()) is not None:
                history.append(result)
        if msg is None or msg.stop_reason != "pause_turn":
            break
    if msg is None or msg.stop_reason == "refusal":
        return "Sorry, I can't help with that one."
    return "".join(b.text for b in msg.content if b.type == "text").strip()


if __name__ == "__main__":
    import sys
    print(ask(" ".join(sys.argv[1:]) or "What's the weather like this evening?"))
