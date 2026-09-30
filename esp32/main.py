# Weatherboy phone base: MicroPython on an ESP32 inside the F615.
# Hook switch -> UDP broadcast "HOOK 0/1"; UDP "LCD"+frame from the host -> the phone's own LCD.
# Needs wifi.py next to it on the board, with two lines:  SSID = "..."  and  PASSWORD = "..."
# Pins are for a classic ESP32 DevKit; change them for C3/S3 boards.
import socket
import time

import network
from machine import SPI, Pin

from wifi import PASSWORD, SSID

PORT = 7615
W, H = 128, 64             # must match phone.py

# --- hook switch: between HOOK_PIN and GND --------------------------------------------------------------
HOOK = Pin(27, Pin.IN, Pin.PULL_UP)
OFF_HOOK_LEVEL = 1         # 1: handset down closes the switch to GND (usual). Flip to 0 if lifting reads "on hook".
# A broken hook wire floats high = "off hook" = mic always on, i.e. back to how it works with no hook switch.
BACKLIGHT = Pin(26, Pin.OUT)  # drives the LCD's red/black LED pair through a transistor + resistor

# --- LCD: assumes an ST7565 / ST7567 / UC1701-family controller wired for 4-wire SPI ------------------------
# Unverified for the F615 glass. See the README's LCD bring-up section before trusting any of these.
CS, A0, RST = Pin(5, Pin.OUT, value=1), Pin(4, Pin.OUT), Pin(17, Pin.OUT, value=1)
spi = SPI(2, baudrate=4_000_000, polarity=0, phase=0, sck=Pin(18), mosi=Pin(23))
CONTRAST = 0x20            # 0-63: raise until pixels are black, lower if the background turns grey
BIAS_1_7 = False           # 1/9 bias suits most 64-row glass; try True if contrast never looks right
FLIP_X, FLIP_Y = False, True
COL_OFFSET = 0             # 132-column controllers driving 128-column glass often need 4 when FLIP_X


def lcd_cmd(*b):
    A0(0)
    CS(0)
    spi.write(bytes(b))
    CS(1)


def lcd_init():
    RST(0)
    time.sleep_ms(10)
    RST(1)
    time.sleep_ms(10)
    lcd_cmd(0xA3 if BIAS_1_7 else 0xA2, 0xA1 if FLIP_X else 0xA0, 0xC8 if FLIP_Y else 0xC0, 0x40,
            0x2C)          # booster on
    time.sleep_ms(50)
    lcd_cmd(0x2E)          # + regulator
    time.sleep_ms(50)
    lcd_cmd(0x2F, 0x25, 0x81, CONTRAST, 0xA6, 0xA4, 0xAF)  # + follower, resistor ratio, contrast, normal, on


def lcd_show(buf):
    for page in range(H // 8):
        lcd_cmd(0xB0 | page, 0x10 | (COL_OFFSET >> 4), COL_OFFSET & 0x0F)
        A0(1)
        CS(0)
        spi.write(buf[page * W:(page + 1) * W])
        CS(1)


def hook_up():
    a = HOOK.value()
    time.sleep_ms(30)      # debounce: two reads 30 ms apart must agree
    return a == OFF_HOOK_LEVEL if HOOK.value() == a else None


wlan = network.WLAN(network.STA_IF)
wlan.active(True)
try:
    wlan.config(reconnects=-1)  # let the WiFi driver reconnect forever by itself (MicroPython 1.20+)
except (ValueError, OSError):
    pass
wlan.connect(SSID, PASSWORD)
while not wlan.isconnected():
    time.sleep_ms(200)
print("wifi", wlan.ifconfig()[0])

lcd_init()
lcd_show(bytes(W * H // 8))
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    s.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_BROADCAST", 0x20), 1)
except OSError:
    pass
s.bind(("0.0.0.0", PORT))
s.setblocking(False)

state, sent = None, 0
while True:
    up = hook_up()
    if up is not None and (up != state or time.ticks_diff(time.ticks_ms(), sent) > 2000):
        state, sent = up, time.ticks_ms()
        BACKLIGHT(up)
        try:
            s.sendto(b"HOOK 1" if up else b"HOOK 0", ("255.255.255.255", PORT))
        except OSError:    # wifi dropped; it reconnects on its own and the heartbeat retries in 2 s
            pass
    try:
        data, _ = s.recvfrom(W * H // 8 + 16)
        if data[:3] == b"LCD" and len(data) == 3 + W * H // 8:
            lcd_show(memoryview(data)[3:])
    except OSError:        # nothing waiting
        pass
