# Weatherboy phone base: MicroPython on an ESP8266 inside the F615. Hook switch out over the USB
# serial cable, which phone.py on the machine running the server reads.
#
# No WiFi on purpose: the ESP and that machine would be two clients on the same router, and ours
# keeps them apart (AP isolation), which is why the first version of this never got through.
# A cable does not care what the router does, and works with the internet down.
#
# Sends "HOOK 1\n" / "HOOK 0\n" on every change and every 2 s, so the host needs no address, no
# port and no pairing - and can tell a lifted handset from a board that has stopped talking.
# The host may send "L" + a 2-byte little-endian length + that many bytes (an LCD frame). This
# board has no glass wired up, so frames are read and dropped, but the framing is settled now so
# the host side never has to change when it does.
#
# Pins: the hook switch goes between GPIO5 (D1) and GND. Not D5, as the Arduino version used -
# on an ESP8266 that is GPIO14, the hardware SPI clock, and it is wanted if the F615 glass ever
# gets wired up. GPIO0, 2 and 15 are strapping pins and GPIO1/3 are the UART this board just gave
# away, so GPIO5 is the only comfortable one. It also leaves 2, 4, 13, 14, 15 and 16 free for
# that LCD: SPI is fixed to 12/13/14, so a display still fits alongside this.
#
# Needs boot.py next to it on the board - it hands UART0 over, so the REPL stops eating the
# messages. See the README for flashing it and for getting the bootloader back.
from time import sleep_ms, ticks_diff, ticks_ms

from machine import Pin

from boot import uart

# --- hook switch: between GPIO5 (D1) and GND ---------------------------------------------------------------
HOOK = Pin(5, Pin.IN, Pin.PULL_UP)
OFF_HOOK_LEVEL = 1         # 1: handset down closes the switch to GND (usual). Flip to 0 if lifting reads "on hook".
# A broken hook wire floats high = "off hook" = mic always on, i.e. back to how it works with no hook switch.
BEAT = 2000                # ms between heartbeats: the host treats 10 s of silence as "board gone, treat as hung up"

buf = bytearray()


def read_frames():
    """Swallow whatever the host sent, keeping the framing intact: 'L' + a 2-byte little-endian
    length + that many bytes. An LCD would be drawn from the payload; there is none on this board."""
    global buf
    chunk = uart.read(64)
    if not chunk:
        return
    buf += chunk
    while buf:
        if buf[0] != ord("L"):     # vendor boot noise, or a partial header: resync on the next byte
            del buf[0]
            continue
        if len(buf) < 3:
            return                  # wait for the whole length field
        n = int.from_bytes(buf[1:3], "little")
        if len(buf) < 3 + n:
            return                  # and for the whole frame
        del buf[:3 + n]


def hook_up():
    a = HOOK.value()
    sleep_ms(30)               # debounce: two reads 30 ms apart must agree
    return a == OFF_HOOK_LEVEL if HOOK.value() == a else None


state, sent = None, 0
while True:
    up = hook_up()
    if up is not None and (up != state or ticks_diff(ticks_ms(), sent) > BEAT):
        state, sent = up, ticks_ms()
        uart.write(b"HOOK 1\n" if up else b"HOOK 0\n")
    read_frames()
    sleep_ms(10)