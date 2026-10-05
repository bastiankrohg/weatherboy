"""The round touch screen (Waveshare ESP32-S3-Touch-LCD-1.46) on a USB-C cable: shows what the handset is
doing, and a tap on the glass stops Weatherboy talking. Firmware: esp32s3/esp32s3.ino.

    python screen.py              cycle through every state, print taps (bring-up)
    python screen.py --port COM3

The board's own USB port is the serial line (Espressif's VID 0x303A), so it is never mistaken for the
ESP8266 phone base, which sits behind a CH340/CP210 bridge. Lines out: "S state\\ttitle\\ttext" and
"L 0-255" (mic level); lines in: "TAP".
The glass is small, so the orb carries the message: words only for the clock, printing and errors, and
weather as an icon, a temperature and a wind arrow ("W symbol\ttemp\twind\tfrom").

With follow=True it mirrors orb.py (the same state the web page's orb shows) from a thread of its own, so
main.py doesn't have to tell it anything but the weather.

A screen that is unplugged or crashes must never stop the handset, so every write swallows serial errors
and the port is reopened on the next update a few seconds later.
"""
import argparse
import threading
import time

ESPRESSIF = 0x303A
STATES = ("idle", "listen", "think", "speak", "print", "error")
DAYS = "mandag tirsdag onsdag torsdag fredag lørdag søndag".split()
WORDS = {"idle", "print", "error"}  # the only states that show text; the rest is just the orb


def guess_port():
    import serial.tools.list_ports as lp
    return next((p.device for p in lp.comports() if p.vid == ESPRESSIF), None)


class Screen:
    def __init__(self, port=None, follow=False):
        self.port, self.ser, self.tried, self.taps, self.sent = port, None, 0.0, 0, 0.0
        self._wlock = threading.Lock()  # the follow thread and main.py's weather write at the same time
        self._open()
        if follow:
            threading.Thread(target=self._follow, daemon=True).start()

    def _follow(self, fps=20):
        """Mirror orb.py: its mode, its label when that's worth a word, and the voice level (mic or speech)."""
        import orb
        last, level = None, -1.0
        while True:
            o = orb.snapshot()
            state, label, text = o["mode"], o["label"], ""
            if state == "idle":  # on the hook: a clock
                now = time.localtime()
                label, text = time.strftime("%H:%M", now), f"{DAYS[now.tm_wday]} {time.strftime('%d.%m', now)}"
            elif label.startswith(("Skriver ut", "Lagt i kø")):
                state = "print"
            if (state, label, text) != last:
                last = (state, label, text)
                self.show(state, label, text)
            if abs(o["level"] - level) > 0.02:
                level = o["level"]
                self._write(f"L {int(level * 255)}")
            time.sleep(1 / fps)

    def _open(self):
        import serial
        self.tried = time.time()
        try:
            port = self.port or guess_port()
            if not port:
                return
            s = serial.Serial()
            s.port, s.baudrate, s.timeout = port, 115200, 1
            s.dtr = s.rts = False  # on the S3's own USB, RTS without DTR resets the chip
            s.open()
            self.ser = s
            threading.Thread(target=self._listen, args=(s,), daemon=True).start()
        except Exception as e:  # noqa: BLE001 - no screen is fine
            print(f"screen: {e}")

    def _listen(self, s):
        try:
            while self.ser is s:
                if s.readline().strip() == b"TAP":
                    self.taps += 1
        except Exception:  # noqa: BLE001 - unplugged; _write notices and reopens
            pass

    @property
    def where(self):
        return self.ser.port if self.ser else "not found"

    def _write(self, line):
        if not self.ser and time.time() - self.tried > 5:
            self._open()
        with self._wlock:
            if self.ser:
                try:
                    self.ser.write(line.encode("utf-8") + b"\n")
                except Exception:  # noqa: BLE001
                    self.ser = None

    def show(self, state, title="", text=""):
        if state not in WORDS:
            title = text = ""
        elif state == "print":
            text = ""  # "Skriver ut" says it; the details are on the paper
        clean = lambda s: " ".join(str(s).split())  # no tabs or newlines inside a field
        self._write(f"S {state}\t{clean(title)}\t{clean(text)}")

    def weather(self, hour):
        """One hour of agent.forecast: shown as icon, temperature and wind for 30 s."""
        self._write(f"W {hour['symbol'] or 'cloudy'}\t{hour['temp']:.1f}\t{hour['wind']:.1f}\t{hour.get('from') or 0:.0f}")

    def level(self, x):
        """0-1, how loud the mic is. Throttled: the animation smooths it anyway."""
        if time.time() - self.sent > 0.05:
            self.sent = time.time()
            self._write(f"L {int(max(0.0, min(1.0, x)) * 255)}")


if __name__ == "__main__":
    import math
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", help="serial port (default: the first Espressif USB device)")
    sc = Screen(ap.parse_args().port)
    print(f"screen: {sc.where}")
    demo = {"idle": ("14:05", "mandag 05.10"), "listen": ("Lytter...", ""),
            "think": ("Tenker...", "Hvordan blir været i Bergen i morgen?"),
            "speak": ("", "I Bergen blir det regn i morgen, rundt elleve grader og frisk bris fra sørvest. "
                          "Ta med paraply - og gjerne en ekstra genser."),
            "print": ("Skriver ut...", ""), "error": ("Feil", "Skriveren svarer ikke")}
    taps = 0
    while True:
        for st in STATES + ("rain", "partlycloudy_day", "clearsky_night", "snow"):
            if st in STATES:
                sc.show(st, *demo[st])
            else:  # the weather overlay, a few kinds
                sc.weather({"symbol": st, "temp": -3.4 if st == "snow" else 12.6, "wind": 7.2, "from": 225})
            for i in range(80):  # 4 s each
                if st == "listen":
                    sc.level(abs(math.sin(i / 6)) * (0.3 + 0.7 * (i % 20 > 8)))
                if sc.taps != taps:
                    taps = sc.taps
                    print("TAP")
                time.sleep(0.05)
