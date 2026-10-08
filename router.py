"""One transcript in, one decision out: the routing shared by the handset (main.py) and any other speech
pipeline posting transcripts to the web server (/api/voice). Keywords make a card without a model, the password
starts a call, nlp reads the time, place and stop out of a longer sentence, and only what's left goes to the
model. route() only decides and renders; the caller prints, plays tones or speaks."""
import envfile  # noqa: F401 - .env loaded before the settings below are read
import os
import re
from difflib import get_close_matches

import agent
import cookbook
import layout
import nlp

COMMANDS = {  # short utterances (1-3 words) that skip the model. NB-Whisper writes English words in Norwegian.
    "weather": ["weather", "vær", "været", "værmelding", "yr"],
    "departures": ["departures", "avganger", "avgang", "tog", "toget", "train", "trikken", "bussen", "tram", "bus"],
    "flights": ["flights", "planes", "fly", "flyene", "radar"],
    "art": ["art", "kunst", "labyrint", "maze"],
    "nrk": ["nyheter", "nyhetene", "nrk", "news"],
    "world": ["verdensnyheter", "world news", "new york times", "utenriks"],
    "print": ["skriv ut", "print"],
    "bye": ["ha det", "hade", "bye", "goodbye", "legg på", "hang up"],
}
PASSWORD = [w.strip().lower() for w in os.environ.get("WEATHERBOY_PASSWORD", "pineapple,ananas").split(",") if w.strip()]
GREETING = "Hallo, det er Weatherboy. Hva lurer du på?"
last = None  # makes the last receipt again, for "skriv ut"


def command(text, password=()):
    words = re.findall(r"\w+", text.lower())
    if not 1 <= len(words) <= 3:  # ponytail: keywords only fire on short utterances; sentences go to the model
        return None
    for name, keys in [("chat", password), *COMMANDS.items()]:
        for k in keys:
            # fuzzy for longer words ("Whether." -> weather), exact for short ones ("by" is not "bye")
            if " " in k and k in " ".join(words) or get_close_matches(k, words, 1, 0.8 if len(k) > 3 else 1.0):
                return name
    return None


def receipt(text, cards, cmd=None):
    """The part of route() that needs no model: a keyword receipt, or a sentence nlp is sure of.
    -> (card name, image) or None. The web page's question box asks this first too, so typing "tog fra Røros"
    gets the board straight away, free. cmd: command(text) if the caller has it already."""
    try:
        hit = nlp.match(text)
        if isinstance(hit, dict) and hit["intent"] in cards:
            return hit["intent"], cards[hit["intent"]](**hit["args"])
    except Exception as e:  # noqa: BLE001 - no network, an API down, a stop that has gone: never lose the call
        print(f"router: nlp could not use {text!r} ({e})")
    cmd = cmd or command(text)
    if cmd in cards:  # one or two words the keywords know, the way they always did
        return cmd, cards[cmd]()
    return None


def route(text, cards, call=False, password=PASSWORD):
    """-> {"kind": card | print | call | hangup | answer | ignored, "cmd": the keyword or None,
    "image": a receipt to print (if any), "say": for the earpiece (if any), "text", "cost"}.
    cards: the keyword receipts (web.CARDS). call: inside a phone call, answers are spoken, not printed."""
    global last
    cmd = command(text, password)
    if cmd == "print":  # the recipe itself if the last answer used one, else the last receipt
        if cookbook.last:
            path = cookbook.last
            return {"kind": "print", "cmd": cmd,
                    "image": layout.recipe(path.read_text(encoding="utf-8"), photo=cookbook.photo(path))}
        return {"kind": "print", "cmd": cmd, "image": last()} if last else {"kind": "ignored", "cmd": cmd}
    if call:
        if cmd == "bye":
            return {"kind": "hangup", "cmd": cmd, "say": "Bye!" if re.search("bye|hang", text, re.I) else "Ha det!"}
        answer, dollars = agent.ask(text, voice=True)
        icon, answer = layout.theme(answer)
        last = lambda: layout.answer(text, answer, icon=icon)
        return {"kind": "answer", "cmd": cmd, "say": answer, "text": answer, "cost": dollars}
    if cmd == "chat":
        return {"kind": "call", "cmd": cmd, "say": GREETING}
    # A sentence can still be a receipt: "tog fra Røros" is a departures board for Røros, not the fuzzy
    # keyword match on "tog", and "hva blir været i Bergen i morgen" is a forecast for Bergen tomorrow -
    # neither needs a model. nlp only answers when it's sure (None is "no rule", ESCALATE is "the model's")
    # and if it then can't fetch, we fall through rather than leaving the person with nothing.
    if hit := receipt(text, cards, cmd):
        return {"kind": "card", "cmd": hit[0], "image": hit[1]}
    if len(text.split()) < 2:  # ponytail: drops coughs and whisper's one-word hallucinations
        return {"kind": "ignored", "cmd": cmd}
    answer, dollars = agent.ask(text)
    icon, answer = layout.theme(answer)  # the "tema: x" line becomes the receipt's icon
    if layout.is_recipe(answer):
        make = lambda: layout.recipe(answer)
    elif cookbook.last:  # it read or drafted a recipe but only talked about it: the recipe itself
        path = cookbook.last
        make = lambda: layout.recipe(path.read_text(encoding="utf-8"), photo=cookbook.photo(path))  # by voice: with its photo
    else:
        make = lambda: layout.answer(text, answer, icon=icon)
    last = make
    return {"kind": "answer", "cmd": None, "text": answer, "image": make(), "cost": dollars}
