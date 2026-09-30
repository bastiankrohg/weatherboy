"""The flat's shared shopping list: one small JSON file, edited from every phone on the WiFi and by Claude."""
import json
import threading
from datetime import datetime
from pathlib import Path

FILE = Path(__file__).with_name("data") / "handleliste.json"
_lock = threading.Lock()  # the web server answers phones in parallel


def _load():
    return json.loads(FILE.read_text(encoding="utf-8")) if FILE.exists() else []


def _save(items):
    FILE.parent.mkdir(exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(FILE)  # all or nothing: a crash mid-write can't leave half a list


def items():
    with _lock:
        return _load()


def add(text):
    """Add one thing; the same thing still on the list (not yet printed) isn't added twice."""
    text = " ".join(text.split())[:200]
    if not text:
        raise ValueError("Tom vare.")
    with _lock:
        items = _load()
        for it in items:
            if it["text"].lower() == text.lower() and not it["printed"]:
                return it
        it = {"id": max((i["id"] for i in items), default=0) + 1, "text": text,
              "added": datetime.now().isoformat(timespec="minutes"), "printed": None}
        _save(items + [it])
        return it


def remove(ids):
    with _lock:
        _save([it for it in _load() if it["id"] not in set(ids)])


def mark_printed(ids):
    with _lock:
        items, now = _load(), datetime.now().isoformat(timespec="minutes")
        for it in items:
            if it["id"] in set(ids):
                it["printed"] = now
        _save(items)


def add_to_shopping_list(items: str) -> str:
    """Add things to the flat's shared shopping list (everyone in the flat sees it on the web page).

    Args:
        items: Comma-separated things to buy, e.g. "melk, 2 løk, oppvasktabs".
    """
    added = [add(t)["text"] for t in items.split(",") if t.strip()]
    return f"On the shopping list now: {', '.join(added)}" if added else "Nothing to add."
