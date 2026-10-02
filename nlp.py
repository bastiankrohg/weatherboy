"""Understanding a transcript without a model: which tool is meant, and with what arguments.

match(text) -> {"intent", "args"}   a tool we can run (args: place, stop, mode, when, items, name, lang ...)
            -> ESCALATE             it's ambiguous, compound or out of scope: ask a model
            -> None                 no rule applies (the caller may still try its short-command fuzzy match)

The rules aim for precision, not recall: a wrong tool is worse than one extra model call, so anything doubtful
escalates. Dates and times become arguments; places and stops are only accepted when a gazetteer knows them."""
import functools
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher

import requests

ESCALATE = "escalate"
UA = {"User-Agent": "weatherboy/1.1 personal assistant (baskrohg@gmail.com)", "ET-Client-Name": "personal-weatherboy"}

# ---------------------------------------------------------------------------------------------- normalising
SPELLED = {"en": 1, "ett": 1, "ei": 1, "to": 2, "tre": 3, "fire": 4, "fem": 5, "seks": 6, "sju": 7, "syv": 7,
           "åtte": 8, "ni": 9, "ti": 10, "elleve": 11, "tolv": 12, "one": 1, "two": 2, "three": 3, "four": 4,
           "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
SPELLED_RX = "|".join(SPELLED)
WEEKDAYS = {"mandag": 0, "tirsdag": 1, "onsdag": 2, "torsdag": 3, "fredag": 4, "lørdag": 5, "søndag": 6,
            "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}


def norm(text):
    """Lowercase, no punctuation (but commas stay: they split clauses), common one-word spellings fixed."""
    t = " " + text.lower() + " "
    for a, b in (("imorgen", "i morgen"), ("idag", "i dag"), ("ikveld", "i kveld"), ("inatt", "i natt"),
                 ("klokka", "kl"), ("klokken", "kl"), ("kl.", "kl"), ("t-bane", "tbane"), ("handle liste", "handleliste"),
                 ("i morra", "i morgen"), ("i kvelden", "i kveld"), ("i dag tidlig", "i morges")):
        t = t.replace(a, b)
    t = re.sub(r"[,;]", " , ", t)
    t = re.sub(r"(?<!\d)[.:](?!\d)|[?!\"“”'()]|(?<=\d)\.(?!\d)", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# ---------------------------------------------------------------------------------------------- date and time
@dataclass
class When:
    start: datetime
    end: datetime
    label: str
    exact: bool = False  # an exact clock time or "in N minutes", not a part of a day


PARTS = [  # (pattern, days from today, from hour, to hour (past 24 = the next day), label)
    (r" i morgen tidlig ", 1, 6, 10, "i morgen tidlig"), (r" i morgen formiddag ", 1, 8, 12, "i morgen formiddag"),
    (r" i morgen ettermiddag ", 1, 12, 18, "i morgen ettermiddag"), (r" i morgen kveld ", 1, 17, 24, "i morgen kveld"),
    (r" i morgen natt ", 1, 22, 30, "i morgen natt"), (r" i morgen | tomorrow ", 1, 6, 24, "i morgen"),
    (r" i overmorgen ", 2, 6, 24, "i overmorgen"), (r" i kveld | tonight | this evening ", 0, 17, 24, "i kveld"),
    (r" i natt ", 0, 22, 30, "i natt"), (r" i ettermiddag | this afternoon ", 0, 12, 18, "i ettermiddag"),
    (r" i formiddag ", 0, 8, 12, "i formiddag"), (r" i morges ", 0, 6, 10, "i morges"),
    (r" i dag | today ", 0, 0, 24, "i dag"),
]
PAST = re.compile(r" (i går|igår|i fjor|forgårs|yesterday|last (week|year|month)|forrige \w+|i (19|20)\d\d|fra (19|20)\d\d) ")


def _cut(t, m):
    return t[:m.start()] + " " + t[m.end():]


def when(text, now):
    """-> (When or None, the text without the time words). None = "now". Raises ValueError for the past."""
    t = " " + norm(text) + " "
    if PAST.search(t):
        raise ValueError("past")
    day = None
    for rx, offset, h0, h1, label in PARTS:
        if m := re.search(rx, t):
            t = _cut(t, m)
            base = (now + timedelta(days=offset)).replace(hour=0, minute=0, second=0, microsecond=0)
            start = base + timedelta(hours=h0)
            day = When(max(start, now) if offset == 0 else start, base + timedelta(hours=h1), label)
            break
    if not day and (m := re.search(r" (i helgen|til helgen|this weekend|på lørdag og søndag) ", t)):
        t = _cut(t, m)
        base = now.replace(hour=0, minute=0, second=0, microsecond=0)
        sat = base + timedelta(days=(5 - now.weekday()) % 7) if now.weekday() != 6 else base
        day = When(max(sat + timedelta(hours=6), now), (sat if now.weekday() != 6 else base - timedelta(days=1))
                   + timedelta(days=2), "i helgen")
    if not day and (m := re.search(rf" (?:på |om |til |neste |on |next |this )?({'|'.join(WEEKDAYS)}) ", t)):
        t = _cut(t, m)
        base = now.replace(hour=0, minute=0, second=0, microsecond=0)
        base += timedelta(days=(WEEKDAYS[m[1]] - now.weekday()) % 7 or 7)  # said on a Monday, "mandag" is next week's
        day = When(base + timedelta(hours=6), base + timedelta(hours=24), f"på {m[1]}")
    # an exact clock time: "kl 17", "kl 17:30", "kl fem", "at 5 pm"
    m = re.search(rf" (?:kl|at) (\d{{1,2}})(?:[:.](\d\d))?(?: (am|pm))? | kl ({SPELLED_RX}) ", t)
    if m:
        t = _cut(t, m)
        hh = int(m[1]) if m[1] else SPELLED[m[4]]
        mm = int(m[2] or 0)
        if m[3] == "pm" and hh < 12:
            hh += 12
        if not 0 <= hh < 24 or mm > 59:
            raise ValueError("clock")
        base = (day.start if day else now).replace(hour=0, minute=0, second=0, microsecond=0)
        cand = base + timedelta(hours=hh, minutes=mm)
        if not day and cand < now:  # already passed today: the next pm if there is one, else tomorrow
            cand = cand + timedelta(hours=12) if hh < 12 and cand + timedelta(hours=12) > now else cand + timedelta(days=1)
        label = f"{day.label} kl. {hh:02d}:{mm:02d}" if day else f"kl. {hh:02d}:{mm:02d}"
        return When(cand, cand + timedelta(hours=3), label, True), t.strip()
    # relative: "om 20 minutter", "om en time", "in 2 hours"
    m = re.search(rf" (?:om|in) (\d+|{SPELLED_RX}|en halv|a half) (minutt\w*|min|minutes?|timer|time|timen|hours?|halvtime\w*) ", t)
    if m:
        t = _cut(t, m)
        n = 0.5 if "halv" in m[1] or m[2].startswith("halvtime") else int(m[1]) if m[1].isdigit() else SPELLED.get(m[1], 1)
        delta = timedelta(minutes=n) if m[2].startswith(("min", "minutt")) else timedelta(hours=n)
        start = now + delta
        return When(start, start + timedelta(hours=3), f"om {m[1]} {m[2]}", True), t.strip()
    t = re.sub(r" (akkurat nå|for øyeblikket|just now|right now|nå|now) ", " ", t)
    return day, t.strip()


# ---------------------------------------------------------------------------------------------- places and stops
@dataclass
class Place:
    name: str
    lat: float
    lon: float


GEO_ORDER = ["By", "Tettsted", "Tettbebyggelse", "Kommune", "Administrativ bydel", "Bydel", "Flyplass", "Fylke",
             "Bygd", "Øy", "Tettsted"]  # Kartverket's feature types worth forecasting, best first


@functools.lru_cache(maxsize=512)
def geocode(name, capitalized=False):
    """A place name -> Place or None. Norway's official place names first (exact match, a town or an airport,
    not a farm or a field); elsewhere in the world only for capitalized names, from OpenStreetMap."""
    try:
        r = requests.get("https://ws.geonorge.no/stedsnavn/v1/navn", timeout=6, headers=UA,
                         params={"sok": name, "treffPerSide": 10, "fuzzy": "false"})
        r.raise_for_status()
        hits = [n for n in r.json().get("navn", []) if n["skrivemåte"].lower() == name.lower()
                and n["navneobjekttype"] in GEO_ORDER]
        if hits:
            n = min(hits, key=lambda n: GEO_ORDER.index(n["navneobjekttype"]))
            p = n["representasjonspunkt"]
            return Place(n["skrivemåte"], p["nord"], p["øst"])
        if not capitalized:
            return None
        r = requests.get("https://nominatim.openstreetmap.org/search", timeout=6, headers=UA,
                         params={"q": name, "format": "json", "limit": 1, "accept-language": "no"})
        r.raise_for_status()
        for x in r.json():
            if x.get("type") in ("administrative", "city", "town", "capital") and x.get("addresstype") != "state":
                return Place(x["display_name"].split(",")[0], float(x["lat"]), float(x["lon"]))
    except (requests.RequestException, KeyError, ValueError):
        pass
    return None


@functools.lru_cache(maxsize=512)
def geocode_stop(name):
    """A stop name -> the stop's official name, or None. "bergen" -> "Bergen stasjon"."""
    try:
        r = requests.get("https://api.entur.io/geocoder/v1/autocomplete", timeout=6, headers=UA,
                         params={"text": name, "layers": "venue", "size": 1})
        r.raise_for_status()
        found = r.json()["features"][0]["properties"]["name"]
    except (requests.RequestException, KeyError, IndexError, ValueError):
        return None
    a, b = name.lower(), found.lower()
    return found if a == b or (len(a) >= 4 and b.startswith(a + " ")) or SequenceMatcher(None, a, b).ratio() > .88 else None


FILLER = {"i", "på", "ved", "over", "fra", "rundt", "nær", "til", "mot", "in", "at", "near", "from", "around", "og",
          "nå", "kl", "med", "for", "da", "som", "eller", "and", "the", "is", "it", "det", "er", "blir", "skal", "kan",
          "så", "hva", "hvordan", "hvor", "når", "har", "vi", "jeg", "du", "meg", "oss", "her", "der", "ute", "inne",
          "en", "et", "ei", "den", "de", "dem", "neste", "next", "av", "to", "noen", "noe", "ikke", "bare", "også"}
PREPOSITIONS = {"i", "på", "ved", "over", "fra", "rundt", "nær", "in", "at", "near", "from", "around"}


def candidates(rest, cased):
    """Phrases that might be a place or stop: up to three words after a preposition, longest first.
    `cased`: the original text, so capitalization can count for something."""
    caps = {w.lower() for w in re.findall(r"[\wæøåéèüö-]+", cased) if w[:1].isupper()}
    toks = rest.split()
    out = []
    for i, tok in enumerate(toks):
        if tok in PREPOSITIONS:
            for n in (3, 2, 1):
                seg = toks[i + 1:i + 1 + n]
                if len(seg) == n and seg[0] not in FILLER and not any(w in FILLER for w in seg[1:]):
                    out.append((" ".join(seg), seg[0] in caps))
    return out


# ---------------------------------------------------------------------------------------------- the rules
GUARD = re.compile(r" (hvorfor|why|forklar\w*|explain|hva betyr|what does|forskjell\w*|difference|hvem|who|historie|"
                   r"history|vits\w*|joke|dikt|poem|oversett\w*|translate|tegn en|tegn et|draw|hvor mange|how many|"
                   r"hvor lenge|how long|skriv en|skriv et|fortell\w*|tell me|anbefal\w*|recommend\w*|kalorier|"
                   r"klima\w*|nordlys|kosthold|oppskrift på hvordan) ")
COMPOUND = re.compile(r" (,|og|men|samt|også|samtidig|and|but) (kan|kunne|skal|må|vil|hva|hvordan|hvor|når|er det|"
                      r"finnes|gi meg|fortell|vis|skriv|legg|fjern|er|har|blir|can|what|how|when|where) ")
COOKING = re.compile(r" (ovn\w*|steke|stek|kok|koke|bake|baking|pizza|kake\w*|brød|oven|cooking|retten) ")
MODES = {"tog": "rail", "toget": "rail", "togene": "rail", "train": "rail", "buss": "bus", "bussen": "bus",
         "bussene": "bus", "bus": "bus", "trikk": "tram", "trikken": "tram", "tram": "tram", "tbane": "metro",
         "tbanen": "metro", "metro": "metro", "ferge": "water", "fergen": "water", "båt": "water", "båten": "water",
         "ferry": "water"}
LANGS = {"koreansk": "ko", "korean": "ko", "hangul": "ko", "hangeul": "ko", "fransk": "fr", "french": "fr",
         "italiensk": "it", "italian": "it", "spansk": "es", "spanish": "es", "portugisisk": "pt", "portuguese": "pt",
         "kinesisk": "zh", "chinese": "zh", "japansk": "ja", "japanese": "ja", "kanji": "ja"}
LIST = r"(?:felles |vår |den |vårt )*(?:handlelist\w*|innkjøpslist\w*|shopping list)"  # the stem, not "handleliste": the definite drops the e, so handleliste\w* misses handlelista
WEATHER = re.compile(r" (vær|været|værmelding\w*|værvarsel\w*|regn|regner|regnet|regnskur\w*|regnvær|sol|sola|solen|"
                     r"solskinn\w*|vind|vinden|blåser|temperatur\w*|kaldt|varmt|snø\w*|snør|skyet|tåke|paraply\w*|"
                     r"weather|forecast|rain|raining|rainy|sunny|wind|windy|temperature|snow\w*|umbrella|yr) ")
FLIGHT = re.compile(r" (fly|flyene|flight|flights|plane|planes|aircraft|radar) ")
DEPART = re.compile(r" (avgang\w*|departures?) ")
CUE_DEPART = re.compile(r" (når|neste|next|går|kommer|avgang\w*|departure\w*|tider|rutetid\w*|forsinket|fra) ")
CUE_FLIGHT = re.compile(r" (over|nær|nå|oppe|rundt|above|overhead|near|i luften|er det|er der|now) ")
ART = re.compile(r" (kunst|labyrint\w*|maze) ")
WORD = re.compile(r" (dagens (ord|tegn|glose|kanji)|ord for dagen|word of the day|dagens ord) ")
ADD = re.compile(rf"^ (?:(?:kan|kunne|vil) (?:du|dere) )?(?:legg|sett|putt|skriv|add)(?: til| inn)? (.+?) "
                 rf"(?:på|i|til|onto|to|on) {LIST}")
ADD_WE = re.compile(r"^ (?:vi (?:trenger|mangler|må ha|er tom for|har ikke mer)|husk å kjøpe|kjøp|we need|buy) (.+)")
REMOVE = re.compile(rf"^ (?:(?:kan|kunne|vil) (?:du|dere) )?(?:fjern|stryk|slett|ta bort|remove|delete)(?: av| ut)? "
                    rf"(.+?) (?:fra|av|på|from|off|ut av) {LIST}")
RECIPE = re.compile(r" (oppskrift\w*|recipe\w*) ")
HOW_TO = re.compile(r"^ hvordan (?:lager|lage|lagar|koker|koke|baker|bake|steker|steke) (?:jeg|man|vi)? ?(.+)")


def _items(s):
    parts = [re.sub(r"^(en|et|ei|litt|noen|some|a|an) ", "", p.strip()) for p in re.split(r"\s,\s|,| og | and ", s)]
    return [p for p in parts if p and len(p.split()) <= 4]


def match(text, now=None):
    now = now or datetime.now().astimezone()
    t = " " + norm(text) + " "
    if len(t.split()) < 1:
        return None
    if GUARD.search(t) or COMPOUND.search(t):
        return ESCALATE
    try:
        w, rest = when(text, now)
    except ValueError:  # something about the past, or a clock time that can't be: no tool knows
        return ESCALATE
    rest = " " + rest + " "
    short = len(t.split()) <= 3
    # --- the shared shopping list
    if re.search(LIST, t):
        if m := REMOVE.match(t):
            return {"intent": "shopping_remove", "args": {"items": _items(m[1])}}
        if m := ADD.match(t):
            items = _items(m[1])
            return {"intent": "shopping_add", "args": {"items": items}} if items else ESCALATE
        if re.search(r" (legg|sett|putt|fjern|stryk|slett|add|remove) ", t):
            return ESCALATE  # a list command we couldn't parse
        return {"intent": "shopping_show", "args": {}}
    if m := ADD_WE.match(t):  # "vi trenger melk": only short, plain item phrases
        items = _items(m[1])
        if items and len(m[1].split()) <= 6 and not re.match(r"(å|at|en ny|hvor|hva|hvordan) ", m[1]):
            return {"intent": "shopping_add", "args": {"items": items}}
        return ESCALATE
    # --- a recipe from the collection (the caller escalates when it isn't there)
    if RECIPE.search(t) or HOW_TO.match(t):
        m = re.search(r" oppskrift\w*(?: på| til| for)? (.+)", t) or re.search(r" recipe\w*(?: for| of)? (.+)", t) or HOW_TO.match(t)
        name = re.sub(r"( da| takk| plis| please| for meg|vær så snill)+$", "", m[1].strip()) if m else ""
        if not name:
            return ESCALATE
        return {"intent": "recipe", "args": {"name": name, "short": " kort" in t, "to_list": "handleliste" in t}}
    # --- the day's word
    if WORD.search(t):
        langs = {LANGS[x] for x in t.split() if x in LANGS}
        return {"intent": "word", "args": {"lang": langs.pop() if len(langs) == 1 else None}} if len(langs) <= 1 else ESCALATE
    hits = []
    if WEATHER.search(t) and not (re.search(r" grader ", t) and COOKING.search(t)):
        hits.append("weather")
    if DEPART.search(t) or (any(x in MODES for x in t.split()) and (CUE_DEPART.search(t) or short)):
        hits.append("departures")
    if FLIGHT.search(t) and (CUE_FLIGHT.search(t) or short):
        hits.append("flights")
    if ART.search(t) and (short or " dagens " in t or re.search(r" (vis|skriv ut|print|lag) ", t)) \
            and not re.match(r" (hva|hvem|hvorfor|hvordan|når|hvor) ", t):
        hits.append("art")
    if re.search(r" qr ", t):
        hits.append("qr")
    if len(hits) != 1:
        return ESCALATE if hits else None  # two things at once, or nothing we know
    intent = hits[0]
    args = {"when": w}
    # --- the arguments, each only if we can vouch for it
    cands = candidates(rest, text)
    if intent == "departures":
        if re.search(r" (til|mot|to|towards|retning|reise\w*|rute|kommer jeg meg|how do i get|ta meg) ", rest):
            return ESCALATE  # a destination: that's journey planning, not a departure board
        words = rest.split()
        args["mode"] = next((MODES[x] for x in words if x in MODES), None)
        stop = None
        for c, _ in cands:
            if stop := geocode_stop(c):
                break
        if cands and not stop:
            return ESCALATE  # named something we can't find
        args["stop"] = stop
    elif intent in ("weather", "flights"):
        if intent == "flights" and (w and w.exact or re.search(r" [a-z]{2,3} ?\d{2,4} |billett|bestill|book|flyplass|flytid|forsink\w*|flyr ", t)):
            return ESCALATE  # a named flight, or a time: live radar knows neither
        place = None
        for c, capitalized in cands:
            if place := geocode(c, capitalized):
                break
        if cands and not place and not (len(cands) == 1 and cands[0][0] in {"dag", "morgen", "kveld", "natt", "helgen"}):
            return ESCALATE  # a place we don't know ("i Narnia") or a non-place ("i hagen")
        args["place"] = place
    return {"intent": intent, "args": args}
