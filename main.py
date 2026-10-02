"""Weatherboy: speak into the handset, get a receipt - or say the password and have a phone call.

    python main.py --text                          type instead of talking; PNG previews land in out/
    python main.py --mic USB --speaker USB --printer 192.168.1.50 --phone
    python phone.py                                ESP8266 hook switch over the USB cable, + LCD bring-up
    http://<this machine>:8615                     web page: all receipts, Claude, recipes, photos
    python listen.py USB                           live mic level, to pick --threshold
    python speak.py hei, tester en to              Piper voice through the default output
    python printer.py 192.168.1.50                 stdlib smoke-test page
    python printq.py 192.168.1.50                  what's waiting for the printer, or print it all
"""
import argparse
import os
import sys
import time
from datetime import datetime

import agent
import layout
import printq
import router
import web

CHAT_IDLE = 45  # seconds of silence before the phone call hangs up
DAYS = "mandag tirsdag onsdag torsdag fredag lørdag søndag".split()


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
    p.add_argument("--password", default=",".join(router.PASSWORD), help="comma-separated words that start a call")
    p.add_argument("--phone", action="store_true",
                   help="the ESP in the F615: listen only while off hook (ESP8266 over USB, ESP32 over WiFi)")
    p.add_argument("--phone-port", help="serial port of the ESP8266 (default: the first that looks like an ESP)")
    p.add_argument("--web", type=int, default=web.PORT, help="web page port, 0 to turn it off")
    p.add_argument("--tunnel", help="also run this Cloudflare tunnel for the web page, e.g. weatherboy")
    a = p.parse_args()
    # an emoji in an answer must not crash the loop on a cp1252 console; flush each line for service logs
    sys.stdout.reconfigure(errors="replace", line_buffering=True)
    password = [w.strip().lower() for w in a.password.split(",")]

    if a.web:
        web.start(a.web, a.printer, a.tunnel)
        print(web.banner())
    import speak
    phone = None
    if a.phone:
        import phone as esp
        phone = esp.Phone(esp.open_link(a.phone_port))
        print(f"phone: {phone.link.where}")

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
        show("I samtale" if chat else "Lytter...")
        audio = listen.record(a.mic, a.threshold, wait=wait, abort=hung_up)
        if audio is None:
            return None
        tone(speak.BLIP)
        return stt(audio)[0].strip()

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
            cmd = router.command(q, password)
            print(f"> {q}" + (f"   [{cmd}]" if cmd else ""))
            show("Tenker..." if not cmd or cmd == "chat" else cmd.capitalize(), q)
            r = router.route(q, web.CARDS, call=chat, password=password)  # same rules as /api/voice
            if "image" in r:
                out(r["image"], label=cmd if cmd and cmd != "chat" else (r.get("cmd") or "kvittering"))
            if r["kind"] == "answer":
                print(f"{r['text']}\n[~${r['cost']:.4f}, ${agent.spent:.4f} since start]\n")
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
            show("Feil", repr(e))
            tone(speak.BUSY)


if __name__ == "__main__":
    main()
