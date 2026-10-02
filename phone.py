"""The ESP in the F615 base: hook switch in, LCD frames out. Firmware: esp32/main.py (WiFi) or
esp8266/main.py (USB serial).

    python phone.py               print hook changes and draw a test pattern on the LCD (bring-up)
    python phone.py --port COM5   name the serial port instead of guessing

Two links, one interface. Serial if an ESP8266 is plugged in, else UDP for an ESP32 on WiFi. Both
speak the same thing: the board announces "HOOK 0"/"HOOK 1" on every change and every 2 s, and the
host sends an LCD frame back. Neither side is configured with an address, so nothing has to be kept
in step - the ESP32 broadcasts, and the serial link just needs the port.

WiFi does not work on ours: the ESP and this machine would be two clients on the same router, and
the router keeps clients apart. Hence the cable. The ESP32 variant is left in place for networks
that don't do that.

ponytail: no auth. Anyone on the LAN can fake "off hook", but that only turns the mic on, which is
what running without a hook switch does all the time anyway. Add a shared key if the LCD ever
shows private data.
"""
import argparse
import socket
import threading
import time

import numpy as np

PORT = 7615
W, H = 128, 64   # the F615 glass is probably 128x64; must match the firmware
STALE = 10       # seconds without a heartbeat before we assume the ESP is gone (and treat it as hung up)

# The USB-serial bridges an ESP board ships with, so a Bluetooth or Arduino port on the same
# machine isn't mistaken for the phone.
USB_SERIAL = {0x1A86: "CH340", 0x10C4: "CP210", 0x0403: "FTDI", 0x2341: "Arduino", 0x2A03: "Arduino", 0x1B4F: "SparkFun"}
NAMES = tuple(USB_SERIAL.values())


def guess_port():
    """The first plugged-in port that looks like an ESP board, or None. Needs pyserial, which is
    an optional extra - without it we quietly fall back to WiFi, so --phone still runs without one."""
    try:
        import serial.tools.list_ports as lp
    except ImportError:
        return None
    for p in lp.comports():
        if p.vid in USB_SERIAL or any(n in (p.description or "") for n in NAMES + ("ESP", "USB-SERIAL", "Silicon Labs")):
            return p.device
    return None


class UdpLink:
    """An ESP32 on WiFi. It broadcasts "HOOK 0/1" to everyone, which is also how we learn its
    address; LCD frames go back to wherever the last heartbeat came from."""

    def __init__(self, port=PORT):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("", port))
        self.addr, self.where = None, f"UDP {port}"

    def lines(self):
        while True:
            data, addr = self.sock.recvfrom(64)
            self.addr, self.where = (addr[0], PORT), f"UDP {PORT} from {addr[0]}"
            yield data.strip()

    def send_frame(self, data):
        """The ESP32 firmware wants b"LCD" + exactly W*H//8 bytes."""
        if self.addr:
            self.sock.sendto(b"LCD" + data, self.addr)


class SerialLink:
    """An ESP8266 on a USB cable. Lines are newline-terminated; the LCD frame is length-prefixed,
    the same idea as printer.encode's b"b" + a 2-byte length + rows."""

    def __init__(self, port, baudrate=115200, timeout=2):
        import serial

        self.ser = serial.Serial(port, baudrate, timeout=timeout)
        # The board's auto-reset circuit hangs EN off RTS and GPIO0 off DTR; leaving both asserted
        # holds it in reset (or in the bootloader). De-assert them and let it run.
        self.ser.dtr = False
        self.ser.rts = False
        self.where = f"serial {port}"

    def lines(self):
        while True:
            raw = self.ser.read_until(b"\n", 64)  # vendor boot noise arrives first; Phone ignores it
            if raw:
                yield raw.strip()

    def send_frame(self, data):
        """The ESP8266 firmware wants b"L" + a 2-byte little-endian length + the frame - the same
        envelope printer.encode uses for raster rows, so a short LCD frame can follow a long one."""
        self.ser.write(b"L" + len(data).to_bytes(2, "little") + data)


def open_link(port=None):
    """Serial if there's a board on the cable (or one was named), else UDP for an ESP32 on WiFi.
    An explicitly named port that won't open is an error; nothing plugged in just isn't."""
    if port:
        return SerialLink(port)
    if found := guess_port():
        return SerialLink(found)
    return UdpLink(PORT)


def frame(img):
    """PIL image W x H -> controller bytes: 8-row pages top to bottom, one byte per column, LSB = top row."""
    assert img.size == (W, H), f"LCD image is {img.size}, want {(W, H)}"
    dark = np.asarray(img.convert("L")) < 128
    return np.packbits(dark.reshape(H // 8, 8, W), axis=1, bitorder="little").tobytes()


class Phone:
    def __init__(self, link=None):
        self.link = link or open_link()
        self.up, self.seen = False, 0.0
        threading.Thread(target=self._listen, daemon=True).start()

    def _listen(self):
        for line in self.link.lines():
            if line in (b"HOOK 0", b"HOOK 1"):
                self.up, self.seen = line == b"HOOK 1", time.time()

    def lifted(self):
        return self.up and time.time() - self.seen < STALE

    def show(self, img):
        self.link.send_frame(frame(img))


if __name__ == "__main__":
    from PIL import ImageDraw
    import layout
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", help="serial port of the ESP8266 (default: the first one that looks like an ESP)")
    a = ap.parse_args()
    p = Phone(open_link(a.port))
    print(f"waiting for the ESP on {p.link.where} (Windows: allow python through the firewall on private networks)")
    was = None
    while True:
        if p.lifted() != was:
            was = p.lifted()
            print(f"{p.link.where}: {'off hook' if was else 'on hook'}")
            img = layout.lcd("Løftet!" if was else "Lagt på", "æøå ÆØÅ 0123456789 the quick brown fox")
            # 1-dot border: all four edges visible = no column offset; text reads normally = flips are right
            ImageDraw.Draw(img).rectangle((0, 0, W - 1, H - 1), outline=0)
            p.show(img)
        time.sleep(0.1)
