"""Claude with live-data tools. ask(question) -> printable text; ask(question, voice=True) -> speakable text."""
import math
import os
import threading
import time
from datetime import datetime
from pathlib import Path

import requests
from anthropic import Anthropic, beta_tool

import cookbook
import local
import presets
import shoplist
import websearch


def load_env(path=Path(__file__).with_name(".env")):
    """KEY=value lines from the git-ignored .env into os.environ; real environment variables win."""
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.lstrip().startswith("#"):
                os.environ.setdefault(key.strip(), value.strip().strip("'\""))


load_env()  # before anything reads the environment below

PLACE = os.environ.get("WEATHERBOY_PLACE", "Oslo")
LAT, LON = (float(v) for v in os.environ.get("WEATHERBOY_LATLON", "59.9139,10.7522").split(","))
# Departure boards, first = default: "Name" or "Name=mode" (rail, bus, tram, metro, water) separated by ";"
STOPS = [(name.strip(), mode.strip() or None) for name, _, mode in (
    s.partition("=") for s in os.environ.get("WEATHERBOY_STOPS", "Oslo S=rail;Jakob kirke;Jernbanetorget").split(";"))]
# MET Norway's terms require an identifying User-Agent; put your contact in WEATHERBOY_UA.
MET = {"User-Agent": os.environ.get("WEATHERBOY_UA", "weatherboy/0.1 personal-receipt-printer")}
ENTUR = {"ET-Client-Name": "personal-weatherboy"}

BASE = f"""You are Weatherboy, a personal assistant living in an old telephone handset in a flat in Norway.
Home: {PLACE} ({LAT}, {LON}). Their stops: {", ".join(n + (f" ({m} only)" if m else "") for n, m in STOPS)}.
Use these whenever no place is named.
The user's words come from speech recognition and may contain misheard words; interpret generously.
Answer in the language the user used (usually Norwegian or English).

The user keeps their own recipe collection. For anything about cooking a dish, call list_recipes first and use
their version when they have one (read_recipe). If they don't, find a good recipe online, rewrite it in the house
format below in Norwegian, save it with save_draft, and mention that it's a new draft and where it's from.
When translating: metric Norwegian units (g, dl, ss, ts, °C; never cups or °F) and the everyday Norwegian word
for each ingredient as it's sold in Norwegian shops (cream cheese = kremost, heavy cream = kremfløte,
baking paper = bakepapir). Check each ingredient means the same thing after translation, not a similar-sounding word.

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
Start every reply with a line "tema: X", X being whichever fits best: question, cooking, cocktail, weather,
transport, flight, music or idea (facts, science, history). It becomes the icon on the receipt and isn't printed.
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


def calls(stop=STOPS[0][0], count=8, mode=STOPS[0][1]):
    """Entur real-time departures: (stop name, [departure dicts]), only `mode` (e.g. "rail") if given.
    Raises LookupError for an unknown stop."""
    r = requests.get("https://api.entur.io/geocoder/v1/autocomplete",
                     params={"text": stop, "layers": "venue", "size": 1}, headers=ENTUR, timeout=10)
    r.raise_for_status()
    hits = r.json()["features"]
    if not hits:
        raise LookupError(f"No stop found for {stop!r}.")
    q = """query($id: String!, $n: Int!, $modes: [TransportMode]) { stopPlace(id: $id) { name
      estimatedCalls(numberOfDepartures: $n, whiteListedModes: $modes) { expectedDepartureTime realtime
        destinationDisplay { frontText } quay { publicCode }
        serviceJourney { line { publicCode transportMode } } } } }"""
    r = requests.post("https://api.entur.io/journey-planner/v3/graphql", headers=ENTUR, timeout=10,
                      json={"query": q, "variables": {"id": hits[0]["properties"]["id"], "n": count,
                                                      "modes": [mode] if mode else None}})
    r.raise_for_status()
    sp = r.json()["data"]["stopPlace"]
    return sp["name"], [{"time": datetime.fromisoformat(c["expectedDepartureTime"]), "realtime": c["realtime"],
                         "mode": c["serviceJourney"]["line"]["transportMode"],
                         "line": c["serviceJourney"]["line"]["publicCode"],
                         "dest": c["destinationDisplay"]["frontText"], "platform": c["quay"]["publicCode"]}
                        for c in sp["estimatedCalls"]]


def departures(stop: str, count: int = 8, mode: str = "") -> str:
    """Next real-time departures from a Norwegian public transport stop (Entur: bus, tram, metro, train, ferry).
    Times are Norwegian local time.

    Args:
        stop: Stop name, e.g. "Jernbanetorget" or "Bergen stasjon".
        count: Number of departures to return.
        mode: Only this kind of transport: "rail", "bus", "tram", "metro" or "water". Empty for all.
    """
    name, deps = calls(stop, count, mode or None)
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


# Claude searches the web on Anthropic's side; a local model gets these instead (websearch.py, free)
LOCAL_WEB = [beta_tool(websearch.web_search), beta_tool(websearch.web_fetch)]
TOOLS = [beta_tool(weather), beta_tool(departures), beta_tool(flights),
         beta_tool(cookbook.list_recipes), beta_tool(cookbook.read_recipe), beta_tool(cookbook.save_draft),
         beta_tool(shoplist.add_to_shopping_list)]
# Web tools are the easiest way to burn credit: $0.01 per search plus the result tokens, and a fetched page can
# be tens of thousands of tokens. So: few uses, pages capped.
WEB_LIMITS = {"search": {"max_uses": 2}, "fetch": {"max_uses": 2, "max_content_tokens": 4000}}

# $ per million tokens (input, output), from the Claude API price list. Cheapest first: the page lists them in order.
MODELS = {
    "local": {"name": f"Lokal {local.MODEL} (gratis)", "price": (0, 0)},  # see local.py
    "claude-haiku-4-5": {"name": "Haiku 4.5 (billigst)", "price": (1, 5)},
    "claude-sonnet-5-5": {"name": "Sonnet 5.5", "price": (2, 10)},
    "claude-opus-5-5": {"name": "Opus 5.5 (smartest, dyrest)", "price": (4, 20)},
}
model = os.environ.get("WEATHERBOY_MODEL", "claude-haiku-4-5")
spent = 0.0  # estimated $ since start; the real balance is on console.anthropic.com


def params(m, effort="low"):
    """Per-model request settings: each is as cheap as that model allows."""
    if m == "claude-haiku-4-5":  # no thinking at all; Haiku takes no effort setting and the pre-2026 web tools
        return {"max_tokens": 4000, "tools": TOOLS + [
            {"type": "web_search_20250305", "name": "web_search", **WEB_LIMITS["search"]},
            {"type": "web_fetch_20250910", "name": "web_fetch", **WEB_LIMITS["fetch"]}]}
    return {"max_tokens": 8000, "output_config": {"effort": effort},  # thinking can't be off on these; low = least
            "betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default", "tools": TOOLS + [
                {"type": "web_search_20260209", "name": "web_search", **WEB_LIMITS["search"]},
                {"type": "web_fetch_20260209", "name": "web_fetch", **WEB_LIMITS["fetch"]}]}


def cost(m, usage):
    """Estimated $ for one API response. Cache writes bill at 1.25x input, reads at 0.1x."""
    pin, pout = MODELS.get(m, MODELS[model])["price"]
    tokens = (usage.input_tokens * pin + (usage.cache_creation_input_tokens or 0) * pin * 1.25
              + (usage.cache_read_input_tokens or 0) * pin * 0.1 + usage.output_tokens * pout)
    searches = (usage.server_tool_use.web_search_requests or 0) if usage.server_tool_use else 0
    return tokens / 1e6 + searches * 0.01


def set_model(m):
    global model, history
    if m not in MODELS:
        raise ValueError(f"Unknown model {m!r}")
    if m != model:  # tool blocks from one model's web-tool version can confuse another's: start fresh
        model, history = m, []


client = Anthropic()
history, last_ask = [], 0.0
_lock = threading.Lock()  # one conversation, shared by the phone and the web page


def ask(question, voice=False):
    """-> (answer text, estimated $ for it)."""
    if model != "local" and not (client.api_key or client.auth_token):
        raise RuntimeError("Mangler API-nøkkel: legg ANTHROPIC_API_KEY=... i .env ved siden av agent.py, og start på nytt.")
    with _lock:
        return _ask(question, voice)


def _ask(question, voice):
    global history, last_ask
    if time.time() - last_ask > 600:  # ponytail: follow-ups work for 10 min, then a fresh conversation
        history = []
    last_ask = time.time()
    cookbook.last = None
    history.append({"role": "user", "content": f"[{datetime.now():%A %d.%m.%Y %H:%M}] {question}"})
    if model == "local":  # free: Ollama on the work desktop or this machine, with our own tools (no web)
        return local.chat(VOICE if voice else PAPER, history, TOOLS + LOCAL_WEB), 0.0
    return _run(VOICE if voice else PAPER, history)


def _run(system, messages, extra_tools=(), use=None, effort="low"):
    """The tool loop. Appends the conversation to `messages`; -> (final text, estimated $).
    use: a model other than the one picked on the page (preset design uses a stronger one)."""
    global spent
    m = use or model
    p = params(m, effort)
    p["tools"] = p["tools"] + list(extra_tools)
    dollars = 0.0
    for _ in range(5):  # the runner doesn't resume pause_turn (long server-tool turns); restart it
        runner = client.beta.messages.tool_runner(
            model=m, system=system, messages=list(messages),
            cache_control={"type": "ephemeral"},  # tool-loop turns re-send everything; cached reads cost 10%
            **p)
        msg = None
        for msg in runner:
            dollars += cost(msg.model, msg.usage)
            messages.append({"role": "assistant", "content": msg.content})
            if (result := runner.generate_tool_call_response()) is not None:
                messages.append(result)
        if msg is None or msg.stop_reason != "pause_turn":
            break
    spent += dollars
    if msg is None or msg.stop_reason == "refusal":
        return "Sorry, I can't help with that one.", dollars
    return "".join(b.text for b in msg.content if b.type == "text").strip(), dollars


PRESET = BASE + """
Someone in the flat wants a new button on Weatherboy's web page that prints a receipt when pressed. Design it:
a short Norwegian name, and the exact prompt you'll be given each time the button is pressed (you'll have all
your usual tools then, including web search and web fetch). If a free public API that needs no key fits, put
its full URL in the prompt, and check it answers with web_fetch first. Then call create_preset once.
Keep the prompt short and general, asking for one thing per press; no example answers or word lists in it,
or every press comes out alike. If it can't be done with those tools, create nothing and say briefly, in
Norwegian, why."""


PRESET_MODEL = "claude-sonnet-5-5"  # a preset is used many times, so it's worth a better designer than chat


def design_preset(request):
    """A wish from the page -> maybe a new preset button. -> (reply text, the preset or None, estimated $)."""
    if not (client.api_key or client.auth_token):
        raise RuntimeError("Mangler API-nøkkel: legg ANTHROPIC_API_KEY=... i .env ved siden av agent.py, og start på nytt.")
    with _lock:
        presets.last = None
        reply, dollars = _run(PRESET, [{"role": "user", "content": request}], [beta_tool(presets.create_preset)],
                              use=PRESET_MODEL, effort="medium")
        return reply, presets.last, dollars


if __name__ == "__main__":
    import sys
    answer, dollars = ask(" ".join(sys.argv[1:]) or "What's the weather like this evening?")
    print(f"{answer}\n\n[{MODELS[model]['name']}, ~${dollars:.4f}]")
