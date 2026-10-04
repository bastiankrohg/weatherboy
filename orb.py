"""What the voice loop is doing, for the orb on the Mac's screen (orb.html, at /orb). main.py says which mode
it's in and listen/speak report how loud it is; the page gets it all as a stream of Server-Sent Events.

    mode: idle (hung up) | listen | think | speak | error
    label: the same short line the handset's LCD shows ("Lytter...", "Skriver ut...")
    heard / said: the last thing the person said, and the last answer; both also go in the transcript (log())

Stdlib only, and nothing here knows about audio devices: the web server runs it on an old Mac without the
voice extra, where the orb simply sits idle."""
import collections
import json
import math
import sys
import threading
import time
from datetime import datetime

FPS = 30
_state = {"mode": "idle", "label": "", "heard": "", "said": "", "level": 0.0, "seq": 0}
# The transcript: what was said both ways, kept across hang-ups. The stream only carries "seq", which goes up
# when this changes, and the page fetches the lines then (/api/orb/log) instead of 30 times a second.
_log = collections.deque(maxlen=30)
_level_at = 0.0
_lock = threading.Lock()


def set(mode=None, label=None, heard=None, said=None):
    """Change whichever fields are given; the others stay. Something heard or said also goes in the transcript,
    and going idle after a conversation draws a line under it."""
    with _lock:
        for who, text in (("du", heard), ("weatherboy", said)):
            if text and not (_log and _log[-1]["who"] == who and _log[-1]["text"] == text):
                _log.append({"who": who, "text": text, "at": f"{datetime.now():%H:%M}"})
                _state["seq"] += 1
        if mode == "idle" and _log and _log[-1]["who"] != "-":
            _log.append({"who": "-", "text": "lagt på", "at": f"{datetime.now():%H:%M}"})
            _state["seq"] += 1
        for k, v in (("mode", mode), ("label", label), ("heard", heard), ("said", said)):
            if v is not None:
                _state[k] = v


def log():
    with _lock:
        return list(_log)


def level(rms):
    """An audio block's RMS (mic or voice), as 0-1 on a log scale: -50 dB is silence, -10 dB is shouting."""
    global _level_at
    db = 20 * math.log10(max(rms, 1e-6))
    with _lock:
        _state["level"] = min(1.0, max(0.0, (db + 50) / 40))
        _level_at = time.monotonic()


def snapshot():
    with _lock:
        s = dict(_state)
        if time.monotonic() - _level_at > 0.3:  # nobody is reporting (thinking, hung up): silence
            s["level"] = 0.0
    return s


def stream(wfile):
    """Feed one page until it goes away. Only what changed is worth sending, but the level changes every
    frame while there's sound, so it's simplest to send everything: it's a few dozen bytes."""
    try:
        while True:
            wfile.write(f"data: {json.dumps(snapshot(), ensure_ascii=False)}\n\n".encode())
            wfile.flush()
            time.sleep(1 / FPS)
    except (BrokenPipeError, ConnectionResetError):
        pass


def open_window(url):
    """The orb in a window of its own: Chrome's app mode where there is one (no tabs, no address bar),
    else the default browser."""
    import shutil
    import subprocess
    import webbrowser
    chrome = {"darwin": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"}.get(sys.platform) \
        or shutil.which("google-chrome") or shutil.which("chromium")
    try:
        subprocess.Popen([chrome, f"--app={url}", "--window-size=720,820"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, TypeError):
        webbrowser.open(url)
