"""python test_middag.py - the dinner tickets' timetable, offline: no printer, nothing really printed."""
import tempfile
from datetime import datetime
from pathlib import Path

from PIL import Image

import middag
import printq

tmp = Path(tempfile.mkdtemp())
middag.DATA, middag.SENT = tmp, tmp / "middag_sendt.json"
printed = []
printq.print_or_queue = lambda img, label, source, printer_ip: (
    printed.append((label, img.size)) or {"printed": True, "id": None, "why": None})

plan = middag.load()
assert plan["test"] and plan["plan"], "lag billettene først: python middag/lag.py"
for e in plan["test"] + plan["plan"]:  # every ticket exists, is 576 dots wide and not absurdly long
    img = Image.open(middag.HERE / e["png"])
    assert img.width == 576 and 200 < img.height < 4800, (e["id"], img.size)
ids = [e["id"] for e in plan["test"] + plan["plan"]]
assert len(ids) == len(set(ids))
assert all(e["_start"] - e["_print_at"] == middag.timedelta(minutes=8) for e in plan["plan"])

# Thursday evening: the test pack goes out once, in order, and nothing scheduled
middag.tick(None, datetime(2026, 11, 19, 19, 0))
assert [l for l, _ in printed] == [f"Middag · {e['label']}" for e in plan["test"]]
n = len(printed)
middag.tick(None, datetime(2026, 11, 19, 19, 1))
assert len(printed) == n

# but never at night, and not after Friday
middag.SENT.unlink()
printed.clear()
middag.tick(None, datetime(2026, 11, 19, 23, 30))
middag.tick(None, datetime(2026, 11, 21, 9, 0) - middag.timedelta(hours=3))
assert printed == []

# Saturday 07:52: the 08:00 ticket, once
middag.tick(None, datetime(2026, 11, 21, 7, 52))
assert [l for l, _ in printed] == ["Middag · 08:00 Rengjøring + etikettsjekk"]
middag.tick(None, datetime(2026, 11, 21, 7, 55))
assert len(printed) == 1

# a server that comes back at 10:33 prints what's still current, skips what's long gone
middag.tick(None, datetime(2026, 11, 21, 10, 33))
labels = [l for l, _ in printed[1:]]
assert "Middag · 10:20 Løk ut · sil kraften" in labels and "Middag · 10:40 Fiskekraft (eget utstyr)" in labels
assert "Middag · 08:45 Panna cotta: kok og hell" not in labels

# Sunday: the service tickets come 8 minutes early
printed.clear()
middag.tick(None, datetime(2026, 11, 22, 16, 57))
assert "Middag · 17:05 SERVER V" in [l for l, _ in printed]

# after the dinner, the scheduler doesn't start at all
saved = middag.END
middag.END = datetime(2000, 1, 1)
assert middag.start(None) is None
middag.END = saved
print("ok")
