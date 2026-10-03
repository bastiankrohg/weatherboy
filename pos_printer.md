# Star TSP100III as a phoneline output surface — decisions and evidence

Written 2026-09-07. Everything marked **[correction]** contradicts something in
the brief; everything marked **[uncertain]** is a guess I could not verify.

---

## 0. Three corrections before anything else

### [correction] The TSP100III does not support CloudPRNT. At all.

Not on the LAN model, not on the WLAN model, not via firmware. Star's own
CloudPRNT compatibility list is: mC-Print2, mC-Print3, mC-Label2, mC-Label3,
**TSP100IV**, TSP100IV SK, and the TSP650II/700II/800II/SP700 families via the
HI01X/HI02X interface boards. The III series is absent from both Star's EMEA
product page and the CloudPRNT Protocol Guide's own supported-models section.

CloudPRNT arrived with the **IV** generation. If polling architecture is what
you want, you want a TSP143IV, which is a different (and newer, and more
expensive) machine.

This removes one of the two stated reasons for choosing LAN.

### [correction] Every TSP143III has an internal power supply.

Including the USB model. Star's own spec sheet lists "Power Supply: Internal
(included)" across the range, and retail listings for the TSP143IIIU say
"Internal Power Supply" explicitly. There is no proprietary brick to be missing
on any variant — what a used unit needs is a bog-standard IEC C13 kettle lead,
which you already own several of.

The external-brick arrangement you're remembering is the older TSP100 / TSP100II
ECO. Worth checking the seller's photos to confirm which generation you're
actually looking at, since "TSP100" gets used loosely.

### [confirmed] Everything else in your brief holds.

Model suffix determines interface and is not upgradeable. Linux support via
`starcupsdrv` is real. ESC/POS emulation switching needs Windows tooling — treat
raster as the only mode. 576 dots, 203dpi, 80mm, auto-cutter. `StarTSPImage`
exists and works.

---

## 1. USB vs LAN

**Buy the USB model (TSP143IIIU).** I'll argue against your instinct.

Your two reasons for LAN were CloudPRNT (which does not exist on this
generation) and physical separation of printer from Pi. Only the second
survives, and it is weaker than it looks.

**The wall-mount argument cuts the other way.** The printer needs mains power
wherever it goes. You are not saving a cable run by choosing LAN — you are
swapping "one USB cable to the Pi" for "one mains cable *and* one Ethernet
cable, or one mains cable and a WiFi association that has to survive router
reboots forever". A wall-mounted printer next to a mains socket is 3-5m from
anything; a 5m USB-A cable costs 60 NOK and carries data and nothing else.

**LAN buys you an unattended failure mode.** Raw port 9100 has no handshake and
no acknowledgement. `sock.sendall()` returning means the TCP stack accepted your
bytes, not that anything printed. Out of paper, cover open, printer asleep — all
of them look like success. On USB, a write to `/dev/usb/lp0` fails loudly when
the device is gone. For a thing in a hallway that you will not be watching, the
transport that tells you the truth is worth more than the one that is tidier.

**Driving it from your desktop too is the one real point for LAN.** But you can
have that with USB and less state: share it from the Pi over CUPS, which is one
`cupsctl --share-printers` and one `lpadmin`. The Pi is already always-on and
already the thing that owns the printer. A second machine talking to the printer
*directly* over the network is a coordination problem (two jobs interleaving mid-
raster produces garbage; the printer has no locking); a second machine talking
to it *through the Pi's queue* is a solved problem.

**Cost.** Used TSP143IIIU units are consistently cheaper than LAN ones, because
POS buyers want LAN and the USB units come off shelves in bulk.

**When I'd change my mind:** if the only used unit near you at a good price is a
LAN one, buy it — the code in this package makes the transport a one-line
change, and none of the above is worth paying a premium over. `SocketTransport`
is written and tested. Just don't pay *more* for LAN.

**Avoid the BI (Bluetooth) and W (WiFi) models.** BI is pairing-managed-by-phone
territory and awkward from Linux. W is fine technically but adds a wireless link
that will eventually drop at the worst time, for no benefit over a cable in a
hallway.

---

## 2. Buying used: what to check

The reassuring numbers first. Star rates the TSP143III print head at **100km /
60 million lines MCBF** and the auto-cutter at **2 million cuts**.

Do the arithmetic on a retired café unit: 300 receipts/day × 150mm = 45m/day, so
~16km/year of head travel and ~110k cuts/year. Five years of that is **80% of
rated head life and 27% of cutter life**. So a genuinely hard-worked unit *can*
be near the end of its head — this is not a "these things last forever" story,
and the head is the part you cannot cheaply replace.

Your own usage will be trivial by comparison: at the default budget in
`policy.py` (3000mm/day, and realistically far less) you'd take about 90 years
to reach 100km.

**Ask the seller for a self-test print** (hold FEED while powering on). It is
one photo and it tells you almost everything:

| What to look for | What it means |
|---|---|
| Vertical white streaks running the full length | Dead heating elements. **Walk away** — not repairable at sane cost |
| Uniformly faint print | Could be density setting, could be a worn head. Ask them to try a fresh roll first |
| Patchy light/dark bands across the width | Head pressure or a dirty platen. Usually cleanable |
| Ragged or partial cut | Cutter blade wear or a jam. Sometimes recoverable, sometimes not |
| Firmware version on the self-test | Note it, but see below |

**Other checks:**

- **Confirm the exact model from the label**, not the listing text. "TSP100III"
  in a listing tells you nothing; you need `TSP143IIIU` / `TSP143IIILAN` etc.
  from the sticker underneath.
- **Confirm the generation.** TSP100 / TSP100II / TSP100III / TSP100IV all get
  listed as "Star TSP100". The III has an internal PSU and an IEC socket.
- **Power cable**: just an IEC C13 lead. Non-issue.
- **Cutter test**: ask them to print and cut twice. A cutter that jams is the
  most common failure and the most annoying in a hallway.
- **Cover latch and paper-out sensor**: cheap plastic, and a broken cover switch
  means the printer thinks it's open forever.
- **Roll spindle / paper guide**: the 58mm guide insert is often lost. You want
  80mm anyway, so ignore.
- **Firmware**: I would **not** pay any attention to firmware version, and I
  would **not** update it. There is no CloudPRNT to unlock, updating requires
  Star's Windows utility, and raster mode has been stable across the entire III
  series. Leave it alone. **[uncertain]** I have not verified whether any III
  firmware revision changes raster behaviour; I've found no reports that any
  does.
- **Price sanity**: if a used one is close to a new TSP143IV, buy the IV instead
  — you get CloudPRNT, USB-C and LAN in one box, and a warranty.

---

## 3. Driving it from Python

**Raw raster over a plain byte stream. Not CUPS, not CloudPRNT.**

CloudPRNT is off the table (§0). Between CUPS and raw:

CUPS + `starcupsdrv` makes sense when you have a document (a PDF, a PostScript
file) and need something to rasterise it. You don't. You are generating pixels
already — you have Pillow, you have a fixed 576-dot column, and the printer's
native input format *is* packed pixels. Putting a driver in the middle means
compiling Star's source on the Pi, and then rendering → PDF → Ghostscript →
raster to arrive at the bytes you could have written directly.

The entire Star raster protocol is:

```
1B 2A 72 41              ESC * r A     enter raster mode
1B 2A 72 50 30 00        ESC * r P 0   continuous page length
  62 48 00 <72 bytes>    b n1 n2 data  one dot row, then auto line feed
  ... repeated ...
1B 2A 72 42              ESC * r B     quit; runs EOT (feed + cut)
```

That's it. `raster.py` implements it in about 60 lines, byte-identical to what
`StarTSPImage` emits so you can diff against a known-good implementation.

**Why not just depend on `StarTSPImage`?** Two things in its source make it the
wrong default here:

1. It force-resizes every image to 576 wide. Harmless if you already render at
   576, but it hides the case where you didn't.
2. It force-applies Floyd-Steinberg dithering to **everything**, including text.
   At 203dpi a glyph stem is 1-3 dots. Dithering turns type into gravel. It has
   no way to say "this page is text, threshold it".

So this package separates halftoning policy from encoding and keeps the
encoding identical. If you ever suspect the encoder, `pip install StarTSPImage`
and compare the bytes.

**Testing without the printer: the decoder is the whole trick.** `raster.decode()`
turns the byte stream back into a PIL image. `FakePrinter` uses it to write a
PNG per job. So the development loop is:

```sh
python3 demo.py && open samples/*.png
```

and what you're looking at is dot-for-dot what the head would fire — including
the halftoning, the clipping you didn't notice, and the 300mm of blank paper you
accidentally emitted. Round-trip losslessness is asserted in
`tests/test_raster.py`.

**Chunking:** Star's own guidance is 32KB chunks over USB; the firmware's input
buffer is smaller than a typical receipt and one huge write can stall the
endpoint. `FileTransport` does this. `SocketTransport` doesn't need to.

**udev:** `/dev/usb/lp0` is not stable across reboots with more than one printer,
and is root-owned by default. Rule in `transport.py`'s docstring.

---

## 4. Layout on 576 dots

The samples in `samples/` are the argument; this is the reasoning.

**The five tells of a till roll**, all of which are cheap to avoid: centred
monospace; ALL-CAPS banners; `====` and `****` rules; zero whitespace; a footer
that says THANK YOU. Avoid those and you are most of the way there.

**Type.**

- *Sans for text, serif only at display sizes.* At 203dpi one dot is 0.125mm. A
  serif's hairlines and bracket transitions at 26px are sub-dot: they drop out,
  and you get a spotty, damaged texture. Above ~40px they're 2-3 dots and
  survive. So serif is a display face here and never a text face. Body is DejaVu
  Sans at 28px (~3.5mm em, comfortably readable at arm's length).
- *One tracked-out small-caps label per receipt* — `OSLO`, `VASKELISTE · UKE 37`.
  Tracking (drawn char-by-char, since PIL has no letter-spacing) is what turns a
  caps run from shouting into a marker. It replaces the receipt "header" and it
  is the only place caps are allowed.
- *Right-align numbers, left-align labels.* Single cheapest thing that makes a
  table read as typeset. No dot leaders — that's a menu convention and it adds
  texture where you want calm.
- *Wrap by measured pixel width, not character count.* 48 columns is a monospace
  number; `æ` and `i` are not the same width in a proportional face. Measuring
  is why the right edge is straight.

**Whitespace and the cut.** 4mm side margins (leaving a 512-dot column), 8mm
above the first mark, 1mm below the last. The asymmetry is deliberate: the
feed-to-cutter advance gives you ~15mm of blank at the bottom for free, so
fighting it is silly — lean in and let the artifact sit optically high, which is
how a hanging print wants to sit anyway.

Every block's height snaps to a 34-dot baseline grid. Costs nothing and it's
most of the difference between "typeset" and "concatenated".

**Dithering: Atkinson, not Floyd-Steinberg.** For a physical reason. Thermal
dots bleed into their neighbours (dot gain), so a fully-diffused error kernel
comes out muddier on paper than it looked on screen. Atkinson only propagates
6/8 of the error — it deliberately loses some, which blows out highlights and
crushes shadows. That is exactly the correction dot gain needs. It's why 1-bit
Macintosh art still reads well, and why photos dithered with Floyd-Steinberg on
a receipt printer look like wet newspaper.

**The rule that matters most: dither per image, threshold per page.** Layouts
Atkinson-dither their embedded images to pure 0/255, then the whole page is
encoded with a hard threshold. The already-dithered pixels pass through
untouched while the antialiased type gets a clean binarisation. Page-level
dithering would wreck the type; page-level thresholding alone would flatten the
art. Do both, in that order.

**Weather icons are drawn, not fonted.** Emoji fonts are colour bitmaps —
threshold one and you get a blob. Icon fonts are a dependency and a licence. A
cloud is three ellipses and a rectangle: fill the union black, fill an inset
copy white, and what's left is a constant-weight outline with no seams. Minimum
3-dot line weight, because a 1-dot line prints grey and inconsistent and a 2-dot
line closes up to 3 under dot gain anyway.

**The hourly forecast is a sparkline, not a table.** Twelve rows of numbers is a
table; twelve columns of a curve is a shape you read in one glance on your way
out the door. Only the high and the low get labelled. Precipitation gets its own
lane below the curve rather than sharing space with it.

**Language.** Your profile is Norwegian/English/French fluent plus some Korean
and Japanese, and the premise is asking in whatever language you happen to think
in — so the printer needs to not produce tofu boxes. PIL has no font fallback,
so `style.py` picks a face per *line*: any line containing CJK renders entirely
in Noto CJK, which covers Latin adequately. Norwegian diacritics are fine in
DejaVu. Tested in `test_layouts.py`.

**The `answer` layout's one real decision:** the question is set *small, above*
the answer — same size as the timestamp. What goes on the fridge is the answer;
the question is provenance. Setting the question large makes the artifact about
the asking, which is the mistake every receipt-printer-AI project on the
internet makes.

---

## 5. Thermal paper

**Ordinary thermal paper does die.** Weeks to months in daylight. The
leuco-dye/developer reaction is reversible with heat and degrades under UV,
plasticisers, and oils — it is not a storage-conditions problem you can
engineer around with ordinary stock.

**But you don't have to use ordinary stock. This is the actual answer:**

**Koehler Blue4est.** It contains **no chemical developer at all**. Instead of a
dye/acid reaction it uses a physical mechanism: an opaque functional layer turns
transparent under heat, revealing a black layer beneath. Nothing to reverse.
Koehler claims **>35 years** at correct storage, and the datasheet cites 100%
image retention after 24h at 50°C, 24h at 40°C/80%RH, and **24h at 16,000 lux**.
It's phenol- and BPA-free and is the first thermal paper approved for direct
food contact — which matters in a shared hallway where people will actually
handle these.

Caveats:
- Print is dark on a distinctly **blue-green** paper. That's a look; it happens
  to be a rather good one for this project, but it is not neutral white.
- Medium sensitivity, ~48gsm. May want a higher print density setting than the
  factory default. **[uncertain]** Star density lives in a memory switch,
  normally set with the Windows-only futurePRNT utility — I could not confirm
  it's settable from Linux. Verify before committing.
- More expensive than commodity rolls, and less widely stocked. It is a European
  (Black Forest) product so Norwegian availability should be fine.

**So: does fading kill the art-on-the-wall idea? No — but I'd argue the
ephemerality is worth keeping anyway, and here's the honest framing.**

Both readings are defensible and you should pick on purpose rather than by
default:

- *Ephemerality as feature*: a wall of receipts that fades is a wall that empties
  itself. No one has to decide to throw anything away — the ones worth keeping
  get photographed or re-printed, and the rest quietly leave. That's a genuinely
  nice property for a shared flat where nobody wants to be the one who curates.
- *Ephemerality as bug*: you cannot choose to keep something. The good ones die
  with the throwaway ones.

The move that gets you both: **commodity paper as the default, one Blue4est roll
kept aside**, and a "print that again, properly" path for the handful of things
worth keeping. Cheap, reversible, and the decision is made by whoever liked the
receipt rather than by you in advance.

**Other options considered and rejected:**
- *Synthetic/topcoated thermal* — better resistance to water and oils, but the
  same reversible chemistry underneath. Doesn't solve fading.
- *Impact/dot-matrix on plain paper* — genuinely archival, but that's a
  different printer (Star SP700 class), much louder, and slower.
- *"Just photograph the good ones"* — works, but it defeats the whole point of a
  physical artifact.

---

## 6. Display, Printer, or both — and the `ttl` mismatch

**Keep both surfaces. Route by `ttl`. And don't make the printer a `Display`.**

### The mismatch, stated plainly

`Display` describes *a surface that shows the current state*. Its three
distinguishing features — fixed `width`/`height`, `clear()`, and `ttl` — are all
statements about a thing that can be overwritten. A printer cannot be
overwritten:

- `clear()` is either a lie (no-op) or vandalism (feed and cut ~20mm every time
  the router resets state).
- `height = 4` is meaningless; the surface is unbounded downward.
- `ttl` is the worst of the three, because it isn't merely meaningless — **it is
  a correct and useful signal being thrown away.**

### The resolution

That last point is the whole insight. `ttl` already encodes exactly the fact you
need: *does this matter beyond the next minute?* "Listening…", "thinking…", "23
grader" have a ttl. An answer to a question someone asked does not.

So you already have the routing rule; it was just being ignored:

```
ttl > 0   ephemeral status   -> LED matrix. Free to show, free to replace.
ttl == 0  asked for, persists -> matrix, and (with intent) paper.
```

`RoutingDisplay` in `display.py` is nine lines and does exactly this. No new
concept needed.

### Why the printer still gets its own type

`ttl == 0` is also the *default*, so on its own it is not consent — wiring a
printer into a fan-out and letting every default call reach paper is how you
empty a roll in an afternoon.

So: `ReceiptPrinter.emit(image, trigger=..., ...)` is the real API. It takes a
composed document and a *reason*, and returns what it did.
`StarPrinterDisplay(Display)` is a thin adapter over it for the code that only
knows about `Display` — and crucially **it fails closed**: its default trigger
is `AMBIENT`, and the policy denies `AMBIENT` outright. Adding the printer to
your display fan-out therefore prints *nothing at all* until a call site
explicitly opts in. Failing closed is the right default for a device that makes
noise in a hallway.

`show_text` grows one optional keyword. Nothing else in phoneline changes, which
was your constraint — but the honest API is there for the call sites that
actually carry intent.

### Why keep the LED matrix

Because it's good at the thing the printer is worst at: showing that something
is happening. "Listening", "thinking", a tier indicator, the current time — all
of these want a surface you can overwrite forty times a minute for free. If you
delete the matrix, you either lose that feedback entirely or you start printing
it, and printing it is absurd. Two surfaces, opposite jobs, and the interface
already tells you which is which.

---

## 7. Triggering rules, and making the artifact deliberate

### The rules (implemented in `policy.py`)

**The governing principle: the printer only ever responds to a person.** Nothing
autonomous, nothing pushed. Everything else is a refinement of that.

| Rule | Default | Why |
|---|---|---|
| `AMBIENT` trigger | **always denied** | Fails closed. The default path prints nothing |
| Quiet hours | 22:30–07:30 | A handset request inside the window is **held, not dropped** — you get it with your coffee and nobody is woken |
| Minimum interval | 20s | A stuck loop can't empty a roll |
| Rate limit | 8/hour, 30/day | |
| Paper budget | 3000mm/day | ~25 receipts; a roll lasts a month |
| Dedupe | 30 min on the *inputs* | Hash the inputs, not the rendered image — a timestamp in the footer would make every job unique and defeat it entirely |
| `SCHEDULED` jobs | only 07:30–09:30, once/day | If you want a morning weather print, it gets its own window and its own budget, not a loophole in the handset rule |
| Failed send | doesn't spend the budget | Printer off ≠ paper used |

Two things I'd add that aren't code:

- **A physical mute.** A switch on the wall next to the printer that a flatmate
  can flip without asking you. Software quiet hours are your judgement about
  their sleep; a switch is theirs.
- **Ask before the first print of the day.** Not implemented — it needs a
  conversational turn — but "want that on paper?" costs one second and converts
  the printer from a thing that surprises people into a thing people opt into.

### Making the artifact deliberately good

Some of this is in the code, some is design direction:

1. **Print the answer, not the transcript.** Already in the `answer` layout: the
   question is provenance-sized. The thing you'd stick on the fridge is the
   answer.
2. **Make it scarce.** The budget isn't only about cost. A machine that prints
   twenty things a day produces litter; one that prints three produces objects.
   I'd tighten the default further once you see real usage.
3. **Sign it with the place, not the machine.** `GANGEN` reads better than
   `PHONELINE v0.3`. It's an artifact *from the hallway*, and that's the thing
   worth naming.
4. **Give the rota checkboxes.** Already there. A rota you can tick with the pen
   on the hall table is a fundamentally different object from a rota you read —
   it's the one case where paper genuinely beats a screen, so lean all the way
   in rather than printing a screenshot of a list.
5. **Let the art be worth keeping.** One generative print a day, dated, seeded
   deterministically so a good one can be re-printed. That's a series, not
   output.
6. **Make "print that" a second, separate act.** Ask a question, hear the answer,
   then decide. The two-step is what turns the receipt into a choice someone
   made instead of exhaust from a conversation.
7. **Consider batching.** `cut=False` lets you stack several items on one strip
   and cut once. A morning strip with weather, the rota and yesterday's best
   answer is one object and one noise, rather than three.

---

## Sources

- [STAR Graphic Mode Command Specifications Rev. 2.32](https://starmicronics.com/support/Mannualfolder/star_graphic_cm_en.pdf) — raster command set
- [Star CloudPRNT Protocol Guide](https://star-m.jp/products/s_print/sdk/StarCloudPRNT/manual/en/index.html) — supported model list (no TSP100III)
- [CloudPRNT — Star EMEA](https://star-emea.com/products/cloudprnt/) — same, independently
- [TSP143III spec sheet](https://media.starmicronics.com/hubfs/Spec%20Sheets/TSP143III%20Spec%20Sheet_10-7-2022.pdf) — part numbers, internal PSU, 100km head / 2M cuts
- [CUPS Driver Software Manual for Linux Rev. 7.1](https://www.starmicronics.com/support/Mannualfolder/common_tsp_linux_en.pdf) — supported models, socket://…:9100 URI
- [python-StarTSPImage source](https://github.com/geftactics/python-StarTSPImage) — reference byte sequence
- [dalpil/tsp100-print](https://github.com/dalpil/tsp100-print) — LAN raster prior art
- [Koehler Blue4est](https://www.koehlerpaper.com/en/products/Thermal-paper/Blue4est-thermal-paper.php) and [datasheet](https://www.jarltech.com/sites/default/files/documents/product/datasheet/en/thermorolle_blue4est.pdf) — developer-free mechanism, >35yr claim, light-fastness figures
- [Star TSP143III vs TSP143IV](https://www.pos-hardware.co.uk/blogs/product-comparisons/star-tsp143iii-vs-tsp143iv-understanding-connectivity-part-codes-and-pos-performance) — suffix/interface mapping

---

## 8. Hardware in hand (2026-09-29): it's the LAN model

Ports observed on the actual unit: Ethernet marked `100/10BASE`, a USB-A socket
marked `1A 5V`, and an RJ11/RJ12-style connector. That combination identifies it
as a **TSP143IIILAN**.

**The USB-A port is not a print interface.** On the LAN model it is a 5V/1A
device-charging port — Star describes it as "a USB-A port to charge mobile
devices"; retail spec lists it as "Device Charging 5v-1A USB trickle charging",
separately from the interface line, which is Ethernet only. The `1A 5V` marking
is the giveaway: a data port would not be labelled with a current rating. So
§1's USB-vs-LAN argument is moot — the decision was made when the unit was
bought. `SocketTransport` on port 9100.

*(Side use: 5V/1A is enough to power an ESP32 or the LED matrix, not a Pi 4B.)*

**The RJ connector is the cash drawer kick-out (DK) port.** It drives a drawer
solenoid, not data. **Do not plug the F615 handset — or anything else with an RJ
plug — into it.** Same connector family, entirely different electrical world.

**[confirmed] The TSP100/TSP143 series is raster-only in practice.** Owners of
this exact model report that Star Line Mode and device fonts do not print on it
— graphics mode only. Practical consequence: `echo hello | nc <ip> 9100` will
very likely produce nothing, and that is expected behaviour, not a fault. Don't
spend an evening debugging it. Everything goes through the raster encoder.

### Bring-up sequence

1. **Load 80mm paper.** Self-test: power off → hold FEED → power on → release
   FEED when printing starts. Prints firmware version (printer *and* network
   card), interface info, MAC, IP, communication mode, print settings and memory
   switch details — and doubles as the print-head check, since a full-width test
   page shows dead elements as white vertical streaks.
2. **No IP, or a `169.254.x.x` one?** Reset network settings: open the cover,
   hold FEED, power on, release FEED when the blue/red LEDs flash alternately,
   close the cover. Wait for the slow alternating flash, then power off. Back to
   DHCP. Do not power off mid-reset.
3. **Find it.** Star Micronics OUI is `00:11:62`:
   `arp -a | grep -i '00:11:62'`, or `nmap -p 9100 --open 192.168.x.0/24`, or
   the router's DHCP lease table.
4. **`python3 smoketest.py --host <ip>`** — stdlib only, no Pillow, runs on the
   Pi before anything is installed. Prints a head test, dot-gain stripes, a
   576-dot ruler and a 16-step grey staircase.
5. Then `SocketTransport(ip)` and the package proper.

**Give it a DHCP reservation on the router**, not a static IP on the printer.
Same result, reversible, and it does not need Star's Windows utility.

**[uncertain]** The LAN model has an embedded web configuration page (Star's
product page says so, and the reset article mentions a "Network Utility page
password"), but I could not find the URL or port documented. Try
`http://<ip>/` first; the TSP100III online manual at star-m.jp is the place to
look if that fails.
### First prints (2026-09-30)

- **Address:** 192.168.0.108, port 9100, MAC `00:11:62:0f:ed:3f`.
- **Port 9100 isn't silent after all.** The printer sends an 11-byte Star automatic status block
  (`23 86 00 …` when ready) the moment a connection opens. That makes a pre-print check possible:
  `python printer.py <ip> status`.
- **With the cover open it refuses connections** instead of reporting "cover open". The error bits
  in `printer.PROBLEMS` come from Star's spec and haven't been seen on this unit yet.
- **Close the connection cleanly, or the job hangs.** Closing with that status block unread made
  Windows reset the connection, and the printer never processed the end-of-job command. The receipt
  sat in its buffer until the next job pushed it out. `printer.send` now reads the status, sends,
  half-closes, and waits for the printer to hang up; it does that within about 50 ms.
