"""What a question is doing right now, for the page: it sends an id with the question and asks /api/progress about
it every second, so a slow answer shows its steps ("Søker: ...", "Leser yr.no") instead of looking stuck.

The steps are recorded per thread: the web server answers each request on its own thread, and the model, its tools
and this all run on that thread until the answer is in."""
import threading
import time
from urllib.parse import urlparse

KEEP = 600  # seconds a finished question's steps are kept, in case the page asks late
LIMIT_S = 90  # seconds a question from the page gets: under the 100 s Cloudflare allows a request through the tunnel
TOO_SLOW = f"Modellen brukte for lang tid (over {LIMIT_S} s). Prøv igjen, eller velg en annen modell."
_runs = {}
_lock = threading.Lock()
_here = threading.local()


def start(rid):
    """This thread works on question `rid` from now on (None: nobody's watching)."""
    _here.rid = rid
    if not rid:
        return
    with _lock:
        now = time.monotonic()
        for old in [k for k, r in _runs.items() if now - r["started"] > KEEP]:
            del _runs[old]
        _runs[rid] = {"started": now, "steps": [], "done": False}


def step(text):
    """One thing the question is doing now; a no-op when nobody's watching."""
    rid = getattr(_here, "rid", None)
    if not rid:
        return
    with _lock:
        run = _runs.get(rid)
        if run:
            run["steps"].append({"at": round(time.monotonic() - run["started"], 1), "text": text})


def finish():
    rid = getattr(_here, "rid", None)
    _here.rid = None
    with _lock:
        if rid in _runs:
            _runs[rid]["done"] = True


def get(rid):
    with _lock:
        run = _runs.get(rid)
        if not run:
            return {"steps": [], "elapsed": 0, "done": False}
        return {"steps": list(run["steps"]), "elapsed": round(time.monotonic() - run["started"], 1), "done": run["done"]}


def describe(tool, args):
    """A tool call as the page shows it, in Norwegian like the rest of the page."""
    args = args or {}
    said = {
        "weather": "Henter værmeldingen",
        "departures": f"Henter avganger: {args.get('stop', '')}".rstrip(": "),
        "flights": "Ser etter fly",
        "list_recipes": "Ser i oppskriftene",
        "read_recipe": f"Leser oppskriften: {args.get('name', '')}".rstrip(": "),
        "save_draft": f"Lagrer et utkast: {args.get('title', '')}".rstrip(": "),
        "add_to_shopping_list": "Legger i handlelisten",
        "web_search": f"Søker: {args.get('query', '')}".rstrip(": "),
        "web_fetch": f"Leser {urlparse(str(args.get('url', ''))).hostname or 'en nettside'}",
        "create_preset": "Lager knappen",
    }
    return said.get(tool, f"Bruker {tool}")
