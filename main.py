"""Weatherboy: speak into the handset, get a receipt - or say the password and have a phone call.

    python main.py --text                          type instead of talking; PNG previews land in out/
    python main.py --mic USB --speaker USB --printer 192.168.0.108 --phone
    python phone.py                                ESP8266 hook switch over the USB cable, + LCD bring-up
    python screen.py                               the round touch screen: every animation, prints taps
    http://<this machine>:8615                     web page: all receipts, Claude, recipes, photos
    python main.py --gui                           ...and the voice as an orb in a window on this machine
    python listen.py USB                           live mic level, to pick --threshold
    python speak.py hei, tester en to              Piper voice through the default output
    python printer.py 192.168.0.108                 stdlib smoke-test page
    python printq.py 192.168.0.108                  what's waiting for the printer, or print it all
"""
import argparse
import math
import os
import sys
import time
from datetime import datetime

import agent
import orb
import printq
import router
import web

CHAT_IDLE = 45  # seconds of silence before the phone call hangs up


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--printer", help="printer IP; omit to write PNG previews to out/ instead")
    p.add_argument("--text", action="store_true", help="type instead of talking (replies still play as audio)")
    p.add_argument("--mic", help="input device index or name substring (see python listen.py)")
    p.add_argument("--speaker", help="output device index or name substring; the handset earpiece")
    p.add_argument("--volume", type=float, default=0.5, help="earpiece gain, 0-1")
    p.add_argument("--threshold", type=float, default=0.02, help="RMS level that counts as speech")
    p.add_argument("--pause", type=float, default=2.5,
                   help="seconds of quiet that end what you said: longer lets you stop and think mid-sentence")
    p.add_argument("--model", default="NbAiLab/nb-whisper-small", help="Norwegian model (nb-whisper-base on a Pi)")
    p.add_argument("--en-model", default="small", help="English model: stock tiny/base/small/medium")
    p.add_argument("--lang", default="auto", choices=["auto", "no", "en"], help="auto picks per utterance")
    p.add_argument("--password", default=",".join(router.PASSWORD), help="comma-separated words that start a call")
    p.add_argument("--phone", action="store_true",
                   help="the ESP in the F615: listen only while off hook (ESP8266 over USB, ESP32 over WiFi)")
    p.add_argument("--phone-port", help="serial port of the ESP8266 (default: the first that looks like an ESP)")
    p.add_argument("--screen", nargs="?", const="auto", metavar="PORT",
                   help="the round ESP32-S3 touch screen over USB: what it's doing; a tap stops it talking")
    p.add_argument("--web", type=int, default=web.PORT, help="web page port, 0 to turn it off")
    p.add_argument("--gui", action="store_true", help="show the voice as an orb in a window (the page at /orb)")
    p.add_argument("--tunnel", help="also run this Cloudflare tunnel for the web page, e.g. weatherboy")
    a = p.parse_args()
    # an emoji in an answer must not crash the loop on a cp1252 console; flush each line for service logs
    sys.stdout.reconfigure(errors="replace", line_buffering=True)
    password = [w.strip().lower() for w in a.password.split(",")]

    if a.web:
        web.start(a.web, a.printer, a.tunnel)
        print(web.banner())
        if a.gui:
            orb.open_window(f"http://localhost:{a.web}/orb")
    while True:
        try:
            return run_voice(a, password)  # returns on Ctrl-C or the end of typed input
        except Exception as e:  # noqa: BLE001 - no sound card, a speech model that won't download...: the page stays up
            print(f"voice: {e!r} - trying again in 60 s; the web page keeps running")
            orb.set("error", label="Stemmen virker ikke nå")
            time.sleep(60)


def run_voice(a, password):
    """The handset: listen while it's lifted, answer on paper or out loud. Raises if the sound or the speech
    models can't start, which main() turns into a retry rather than taking the web page down with it."""
    import speak
    phone = None
    if a.phone:
        import phone as esp
        phone = esp.shared(a.phone_port)  # the web page's ESP card talks to the same board
        print(f"phone: {phone.link.where}")
    glass = None  # the round touch screen; it follows the orb by itself
    if a.screen:
        import screen as round_screen
        glass = round_screen.Screen(None if a.screen == "auto" else a.screen, follow=True)
        print(f"screen: {glass.where}")

    def hung_up():
        return bool(phone) and not a.text and not phone.lifted()

    def show(title, text=""):
        orb.set(label=title)

    def screen(state):
        """The phone's own screen draws itself (dark when hung up, a phone while lifted); it only needs to
        know when a request is being handled ("think") and when it's done, even if the handset went down."""
        if phone:
            try:
                phone.screen(state)
            except OSError as e:  # the board went away: the voice loop carries on without it
                print(f"phone screen: {e}")

    def wait_for_lift():
        orb.set("idle", heard="", said="")
        while hung_up():
            time.sleep(0.1)

    if not a.text:
        import listen
        model_stt = listen.transcriber(a.model, a.en_model, a.lang)
        web.STT = model_stt  # the page's microphone (/api/listen) uses the same model, loaded once

        def stt(audio):  # one transcription at a time, the handset's or the page's
            with web._stt_lock:
                return model_stt(audio)

    def tone(t):
        if not a.text:
            speak.play(t, a.speaker, a.volume, abort=hung_up)

    def say(text):
        orb.set("speak", said=text)
        taps = glass.taps if glass else 0  # a tap on the round screen stops it mid-sentence
        speak.say(text, a.speaker, a.volume, abort=lambda: hung_up() or bool(glass) and glass.taps != taps,
                  level=orb.level)

    def out(img, label="kvittering"):
        """The same funnel the web page uses: rendered once, printed if the printer takes it, and kept in
        the queue if it doesn't. A receipt asked for at the handset is never lost to a printer that's off -
        the queue drains itself on the server, and this tells the person it did."""
        show("Skriver ut...")
        png, o = web.render(img, bool(a.printer), label=label, source=q and {"text": q}, printer_ip=a.printer)
        if o["id"]:
            jobs, _ = printq.count()
            show("Lagt i kø", f"{jobs} kvitteringer")
            print(f"queued {o['id']}: {o['why']}")
        elif not a.printer:
            os.makedirs("out", exist_ok=True)
            path = f"out/{datetime.now():%Y%m%d-%H%M%S-%f}.png"
            open(path, "wb").write(png)  # decoded from the job bytes: dot-for-dot what prints
            print("preview:", path)
        return o

    def hear(wait):
        if a.text:
            return input("(call) " if chat else "? ").strip()
        orb.set("listen")
        show("I samtale" if chat else "Lytter...")
        # hanging up mid-sentence ends the recording but keeps it: it's still transcribed and acted on
        audio = listen.record(a.mic, a.threshold, silence=a.pause, max_s=60, wait=wait, abort=hung_up, level=orb.level)
        if audio is None:
            return None
        orb.set("think")
        screen("think")
        tone(speak.BLIP)
        # what it caught and what it made of it, in the transcript: when the words don't come through,
        # this says whether it was the sound or the speech-to-text
        peak = 20 * math.log10(max(float(abs(audio).max()), 1e-6))
        noise = 20 * math.log10(max(listen.last_noise, 1e-6))
        orb.note(f"hørte {len(audio) / listen.RATE:.1f} s lyd, topp {peak:.0f} dB, bakgrunnsstøy {noise:.0f} dB")
        t = time.monotonic()
        text, lang = stt(audio)
        text = text.strip()
        orb.note(f"tolket som {lang} på {time.monotonic() - t:.1f} s: " + (f"«{text}»" if text else "ingen ord"))
        print(f"heard {len(audio) / listen.RATE:.1f} s, peak {peak:.0f} dB -> {lang}: {text!r}")
        return text

    chat = False
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
            orb.set("think", heard=q)
            cmd = router.command(q, password)
            print(f"> {q}" + (f"   [{cmd}]" if cmd else ""))
            show("Tenker..." if not cmd or cmd == "chat" else cmd.capitalize(), q)
            r = router.route(q, web.CARDS, call=chat, password=password)  # same rules as /api/voice
            if glass and "image" in r and r["image"].info.get("weather"):
                glass.weather(r["image"].info["weather"])  # icon, temperature and wind on the round screen
            if "image" in r:
                out(r["image"], label=cmd if cmd and cmd != "chat" else (r.get("cmd") or "kvittering"))
            screen("done")  # the answer is in: whatever is said aloud now, the thinking is over
            if r["kind"] == "answer":
                print(f"{r['text']}\n[~${r['cost']:.4f}, ${agent.spent:.4f} since start]\n")
                orb.set(said=r["text"])
            if r["kind"] == "call":
                tone(speak.RINGBACK)
                say(r["say"])
                chat = True
            elif r["kind"] == "hangup":
                say(r["say"])
                tone(speak.BUSY)
                chat = False
            elif r["kind"] == "answer" and chat:
                show("Weatherboy", r["say"])
                say(r["say"])
        except (KeyboardInterrupt, EOFError):
            break
        except Exception as e:  # keep the handset alive; one bad API call shouldn't kill the loop
            print(f"error: {e!r}")
            orb.note(f"feil: {e}")  # in the transcript too: a question that fails mustn't just vanish
            screen("done")
            orb.set("error")
            show("Feil", repr(e))
            tone(speak.BUSY)
            time.sleep(2)  # an error that repeats (the sound card unplugged) mustn't spin the loop


if __name__ == "__main__":
    main()
