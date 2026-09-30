"""The ESP32 in the F615 base, over WiFi UDP: hook switch in, LCD frames out. Firmware: esp32/main.py.

    python phone.py      print hook changes and draw a test pattern on the LCD (bring-up, contrast tuning)

The ESP32 broadcasts "HOOK 1"/"HOOK 0" on every change and every 2 s, so neither side configures an IP:
we learn its address from those packets and send "LCD" + a page-major 1-bit frame back.
ponytail: no auth. Anyone on the LAN can fake "off hook", but that only turns the mic on, which is what
running without a hook switch does all the time anyway. Add a shared key if the LCD ever shows private data.
"""
import socket
import threading
import time

import numpy as np

PORT = 7615
W, H = 128, 64   # the F615 glass is probably 128x64; must match esp32/main.py
STALE = 10       # seconds without a heartbeat before we assume the ESP32 is gone (and treat it as hung up)


def frame(img):
    """PIL image W x H -> ST7565-style bytes: 8-row pages top to bottom, one byte per column, LSB = top row."""
    assert img.size == (W, H), f"LCD image is {img.size}, want {(W, H)}"
    dark = np.asarray(img.convert("L")) < 128
    return np.packbits(dark.reshape(H // 8, 8, W), axis=1, bitorder="little").tobytes()


class Phone:
    def __init__(self, port=PORT):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("", port))
        self.addr, self.up, self.seen = None, False, 0.0
        threading.Thread(target=self._listen, daemon=True).start()

    def _listen(self):
        while True:
            data, addr = self.sock.recvfrom(64)
            if data in (b"HOOK 0", b"HOOK 1"):
                self.addr, self.up, self.seen = (addr[0], PORT), data == b"HOOK 1", time.time()

    def lifted(self):
        return self.up and time.time() - self.seen < STALE

    def show(self, img):
        if self.addr:
            self.sock.sendto(b"LCD" + frame(img), self.addr)


if __name__ == "__main__":
    from PIL import ImageDraw
    import layout
    p, was = Phone(), None
    print(f"waiting for the ESP32 on UDP {PORT} (Windows: allow python through the firewall on private networks)")
    while True:
        if p.addr and p.lifted() != was:
            was = p.lifted()
            print(f"{p.addr[0]}: {'off hook' if was else 'on hook'}")
            img = layout.lcd("Løftet!" if was else "Lagt på", "æøå ÆØÅ 0123456789 the quick brown fox")
            # 1-dot border: all four edges visible = no column offset; text reads normally = flips are right
            ImageDraw.Draw(img).rectangle((0, 0, W - 1, H - 1), outline=0)
            p.show(img)
        time.sleep(0.1)
