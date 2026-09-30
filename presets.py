"""Receipt buttons wished for on the web page: a name, the prompt Claude runs when pressed, an icon.
Data, not code: a wish can't change the server, only what Claude is asked."""
import json
import threading
from pathlib import Path

FILE = Path(__file__).with_name("data") / "presets.json"
_lock = threading.Lock()
last = None  # the preset created during the current request, for the page to show


def items():
    with _lock:
        return json.loads(FILE.read_text(encoding="utf-8")) if FILE.exists() else []


def _save(entries):
    FILE.parent.mkdir(exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(FILE)


def get(pid):
    return next(p for p in items() if p["id"] == pid)


def remove(pid):
    with _lock:
        entries = json.loads(FILE.read_text(encoding="utf-8")) if FILE.exists() else []
        _save([p for p in entries if p["id"] != pid])


def create_preset(name: str, prompt: str, icon: str = "question") -> str:
    """Save a new button on Weatherboy's web page. Pressing it later sends `prompt` to you, with all your tools.

    Args:
        name: Short Norwegian button label, e.g. "Dagens ord" or "Månefase".
        prompt: The full instruction you'll get when the button is pressed. Include any API URL to fetch.
        icon: One of: question, cooking, cocktail, weather, transport, flight, music, idea, shopping.
    """
    global last
    import layout
    name, prompt = " ".join(name.split())[:40], prompt.strip()[:2000]
    if not name or not prompt:
        raise ValueError("A preset needs a name and a prompt.")
    with _lock:
        entries = json.loads(FILE.read_text(encoding="utf-8")) if FILE.exists() else []
        last = {"id": max((p["id"] for p in entries), default=0) + 1, "name": name, "prompt": prompt,
                "icon": icon if icon in layout.ICONS else "question"}
        _save(entries + [last])
    return f"Created the button {name!r}."
