"""Weatherboy: speak into the handset, get a receipt - or say the password and have a phone call.

    python main.py --text                          type instead of talking; PNG previews land in out/
    python main.py --mic USB --speaker USB --printer 192.168.1.50 --phone
    python phone.py                                ESP32 hook switch + LCD bring-up
    http://<this machine>:8615                     web page: all receipts, Claude, recipes, photos
    python listen.py USB                           live mic level, to pick --threshold
    python speak.py hei, tester en to              Piper voice through the default output
    python printer.py 192.168.1.50                 stdlib smoke-test page
"""
import argparse
import os
import re
import sys
import time
from datetime import datetime
from difflib import get_close_matches

import agent
import cookbook
import layout
import printer
import web

COMMANDS = {  # short utterances (1-3 words) that skip Claude. NB-Whisper writes English words in Norwegian.
    "weather": ["weather", "vær", "været", "værmelding", "yr"],
    "departures": ["departures", "avganger", "avgang", "tog", "toget", "train", "trikken", "bussen", "tram", "bus"],
    "flights": ["flights", "planes", "fly", "flyene", "radar"],
    "art": ["art", "kunst", "labyrint", "maze"],
    "print": ["skriv ut", "print"],
    "bye": ["ha det", "hade", "bye", "goodbye", "legg på", "hang up"],
}
CHAT_IDLE = 45  # seconds of silence before the phone call hangs up
DAYS = "mandag tirsdag onsdag torsdag fredag lørdag søndag".split()


def command(text, password=()):
    words = re.findall(r"\w+", text.lower())
    if not 1 <= len(words) <= 3:  # ponytail: keywords only fire on short utterances; sentences go to Claude
        return None
    for name, keys in [("chat", password), *COMMANDS.items()]:
        for k in keys:
            # fuzzy for longer words ("Whether." -> weather), exact for short ones ("by" is not "bye")
            if " " in k and k in " ".join(words) or get_close_matches(k, words, 1, 0.8 if len(k) > 3 else 1.0):
                return name
    return None


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--printer", help="printer IP; omit to write PNG previews to out/ instead")
    p.add_argument("--text", action="store_true", help="type instead of talking (replies still play as audio)")
    p.add_argument("--mic", help="input device index or name substring (see python listen.py)")
    p.add_argument("--speaker", help="output device index or name substring; the handset earpiece")
    p.add_argument("--volume", type=float, default=0.5, help="earpiece gain, 0-1")
    p.add_argument("--threshold", type=float, default=0.02, help="RMS level that counts as speech")
    p.add_argument("--model", default="NbAiLab/nb-whisper-small", help="Norwegian model (nb-whisper-base on a Pi)")
    p.add_argument("--en-model", default="small", help="English model: stock tiny/base/small/medium")
    p.add_argument("--lang", default="auto", choices=["auto", "no", "en"], help="auto picks per utterance")
    p.add_argument("--password", default="pineapple,ananas", help="comma-separated words that start a call")
    p.add_argument("--phone", action="store_true", help="ESP32 in the F615: listen only while off hook, drive its LCD")
    p.add_argument("--web", type=int, default=web.PORT, help="web page port, 0 to turn it off")
    a = p.parse_args()
    # an emoji in an answer must not crash the loop on a cp1252 console; flush each line for service logs
    sys.stdout.reconfigure(errors="replace", line_buffering=True)
    password = [w.strip().lower() for w in a.password.split(",")]

    if a.web:
        web.start(a.web, a.printer)
        print(web.banner())
    import speak
    phone = None
    if a.phone:
        import phone as esp32
        phone = esp32.Phone()

    def hung_up():
        return bool(phone) and not a.text and not phone.lifted()

    def show(title, text="", size=16):
        if phone:
            phone.show(layout.lcd(title, text, size))

    def wait_for_lift():
        shown = None
        while hung_up():
            now = datetime.now()
            if now.minute != shown:  # idle screen: a clock
                shown = now.minute
                show(f"{now:%H:%M}", f"{DAYS[now.weekday()]} {now:%d.%m}", size=32)
            time.sleep(0.1)

    if not a.text:
        import listen
        stt = listen.transcriber(a.model, a.en_model, a.lang)

    def tone(t):
        if not a.text:
            speak.play(t, a.speaker, a.volume, abort=hung_up)

    def say(text):
        speak.say(text, a.speaker, a.volume, abort=hung_up)

    def out(img):
        show("Skriver ut...")
        job = printer.encode(layout.to_rows(img))
        if a.printer:
            printer.send(job, a.printer)
        else:
            os.makedirs("out", exist_ok=True)
            path = f"out/{datetime.now():%Y%m%d-%H%M%S-%f}.png"
            layout.from_rows(printer.decode(job)).save(path)  # decoded from the job bytes: dot-for-dot what prints
            print("preview:", path)

    def hear(wait):
        if a.text:
            return input("(call) " if chat else "? ").strip()
        show("I samtale" if chat else "Lytter...")
        audio = listen.record(a.mic, a.threshold, wait=wait, abort=hung_up)
        if audio is None:
            return None
        tone(speak.BLIP)
        return stt(audio)[0].strip()

    chat, last = False, None  # last (question, answer), for "print that"
    while True:
        try:
            if hung_up():
                chat = False
                wait_for_lift()
                tone(speak.DIAL)
            q = hear(CHAT_IDLE if chat else None)
            if q is None:  # hung up, or nobody spoke during a call
                chat = False
                tone(speak.BUSY)
                continue
            cmd = command(q, password)
            print(f"> {q}" + (f"   [{cmd}]" if cmd else ""))
            show("Tenker..." if not cmd or cmd == "chat" else cmd.capitalize(), q)
            if cmd == "print":  # the recipe itself if the last answer used one, else the last answer
                if cookbook.last:
                    out(layout.recipe(cookbook.last.read_text(encoding="utf-8")))
                elif last:
                    out(layout.recipe(last[1]) if layout.is_recipe(last[1]) else layout.answer(*last))
            elif chat:
                if cmd == "bye":
                    say("Bye!" if re.search("bye|hang", q, re.I) else "Ha det!")
                    tone(speak.BUSY)
                    chat = False
                else:
                    ans, dollars = agent.ask(q, voice=True)
                    print(f"{ans}\n[~${dollars:.4f}, ${agent.spent:.4f} since start]\n")
                    last = (q, ans)
                    show("Weatherboy", ans)
                    say(ans)
            elif cmd == "chat":
                tone(speak.RINGBACK)
                say("Hallo, det er Weatherboy. Hva lurer du på?")
                chat = True
            elif cmd in web.CARDS:  # weather, departures, flights, art
                out(web.CARDS[cmd]())
            elif len(q.split()) >= 2:  # ponytail: drops coughs and whisper's one-word hallucinations
                ans, dollars = agent.ask(q)
                print(f"{ans}\n[~${dollars:.4f}, ${agent.spent:.4f} since start]\n")
                icon, ans = layout.theme(ans)  # the "tema: x" line becomes the receipt's icon
                last = (q, ans, None, icon)    # layout.answer(question, text, when, icon), for "skriv ut"
                if layout.is_recipe(ans):
                    out(layout.recipe(ans))
                elif cookbook.last:  # it read or drafted a recipe but only talked about it: print the recipe itself
                    out(layout.recipe(cookbook.last.read_text(encoding="utf-8")))
                else:
                    out(layout.answer(q, ans, icon=icon))
        except (KeyboardInterrupt, EOFError):
            break
        except Exception as e:  # keep the handset alive; one bad API call shouldn't kill the loop
            print(f"error: {e!r}")
            show("Feil", repr(e))
            tone(speak.BUSY)


if __name__ == "__main__":
    main()
