"""Star TSP143IIILAN: raster encode/decode + raw TCP 9100. Stdlib only, so it runs on a bare Pi.

    python printer.py <ip>          print the smoke-test page (head test, dot-gain stripes, ruler, greys)
    python printer.py <ip> status   ready, or what's wrong (cover open, out of paper, ...)

A row is 72 bytes = 576 dots, MSB = leftmost dot, bit 1 = black. See pos_printer.md section 3.
"""
import socket
import sys
import threading

DOTS = 576
ROW = DOTS // 8

START = b"\x1b*rA" + b"\x1b*rP0\x00"  # enter raster mode, continuous page length
END = b"\x1b*rB"                      # quit raster mode; runs EOT (feed + cut)


def encode(rows):
    out = bytearray(START)
    for r in rows:
        assert len(r) == ROW, f"row is {len(r)} bytes, want {ROW}"
        out += b"b" + ROW.to_bytes(2, "little") + r
    return bytes(out + END)


def decode(data):
    """Inverse of encode(): what the head would fire, row by row."""
    assert data.startswith(START) and data.endswith(END), "not a raster job from encode()"
    body, rows, i = data[len(START):-len(END)], [], 0
    while i < len(body):
        assert body[i:i + 1] == b"b", f"unexpected byte at {i}"
        n = int.from_bytes(body[i + 1:i + 3], "little")
        rows.append(body[i + 3:i + 3 + n])
        i += 3 + n
    return rows


PROBLEMS = [(2, 0x20, "cover open"), (2, 0x08, "offline"), (3, 0x04, "mechanical error"),
            (3, 0x08, "cutter error"), (3, 0x20, "unrecoverable error"), (5, 0x08, "out of paper")]


class PrinterError(Exception):
    pass


def problems(asb):
    """Star automatic status (ASB) bytes -> what's wrong, [] when ready. Byte/bit map from Star's status spec.
    Seen on the real unit: 23 86 00.. when ready. With the cover open it refuses connections instead."""
    return [name for i, bit, name in PROBLEMS if len(asb) > i and asb[i] & bit]


_lock = threading.Lock()  # one connection at a time: the phone loop and the web page share the printer


def connect(host, port=9100, timeout=5):
    """The first connection after the printer has idled a while sometimes times out; the next one works."""
    try:
        return socket.create_connection((host, port), timeout=timeout)
    except socket.timeout:
        return socket.create_connection((host, port), timeout=timeout)


def status(host, port=9100):
    """The ASB it volunteers on connect; b"" if it accepted the connection but stayed silent (busy)."""
    with _lock, connect(host, port) as s:
        try:
            return s.recv(64)
        except socket.timeout:
            return b""


def send(data, host, port=9100):
    """Raises PrinterError when the printer reports it can't print, instead of losing the job silently.

    The TSP143IIILAN sends its status unasked on connect. Closing a socket with those bytes unread makes
    Windows reset the connection (RST instead of FIN), and the job hangs unprinted until the next one. So: read the
    status, send, half-close, and drain until the printer hangs up.
    ponytail: status is checked before the job only; paper running out mid-receipt still goes unnoticed.
    """
    with _lock, connect(host, port) as s:
        s.settimeout(2)
        try:
            asb = s.recv(64)
        except socket.timeout:  # a printer that doesn't volunteer status: print blind
            asb = b""
        if bad := problems(asb):
            raise PrinterError(", ".join(bad))
        s.settimeout(10)
        s.sendall(data)
        s.shutdown(socket.SHUT_WR)
        s.settimeout(3)
        try:
            while s.recv(64):  # status updates while it prints; empty = printer closed its side
                pass
        except socket.timeout:
            pass


def pack(bits):
    return bytes(sum(bits[i + j] << (7 - j) for j in range(8)) for i in range(0, DOTS, 8))


def smoketest():
    bayer = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]
    blank, black = bytes(ROW), b"\xff" * ROW
    rows = [blank] * 40 + [black] * 48 + [blank] * 24  # full-width band: dead elements show as white streaks
    for w in (1, 2, 3, 4):  # w-dot lines on w-dot gaps: where they close up is your dot gain
        rows += [pack([(x // w) % 2 == 0 for x in range(DOTS)])] * 32 + [blank] * 12
    rows += [pack([x % 64 == 0 or (y < 12 and x % 8 == 0) for x in range(DOTS)]) for y in range(24)]  # ruler, 1mm/8mm
    rows += [blank] * 12
    rows += [pack([bayer[y % 4][x % 4] < x * 16 // DOTS for x in range(DOTS)]) for y in range(64)]  # 16-step greys
    return rows + [blank] * 8


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[2] == "status":
        asb = status(sys.argv[1])
        print(asb.hex(" "), "->", ", ".join(problems(asb)) or "ready" if asb else "connected, but no status (busy?)")
    elif len(sys.argv) == 2:
        send(encode(smoketest()), sys.argv[1])
        print("sent. no paper? see pos_printer.md section 8 bring-up.")
    else:
        sys.exit(__doc__)
