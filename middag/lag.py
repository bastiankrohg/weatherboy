"""Folkemat og fanteri (søndag 22.11.2026): lager alle kjøkkenbillettene som PNG + middag/plan.json.

    python middag/lag.py            -> middag/billettar/*.png og middag/plan.json

Billettene er ferdige bilder (576 punkt brede) som sjekkes inn, så maskinen som skriver ut bare trenger
middag.py. Denne fila trenger Pillow og DejaVu-fontene; menyen (meny.png) lages fra en SVG med rsvg-convert.
"""
import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent
OUT = HERE / "billettar"
W, M = 576, 26
LEAD = 8  # minutter før oppstart billetten skrives ut
FD = "/usr/share/fonts/truetype/dejavu/"


def F(size, w="r"):
    name = {"r": "DejaVuSans.ttf", "b": "DejaVuSans-Bold.ttf", "c": "DejaVuSansCondensed-Bold.ttf",
            "i": "DejaVuSans-Oblique.ttf"}[w]
    return ImageFont.truetype(FD + name, size)


def wrap(text, f, width):
    out, cur = [], ""
    for word in text.split():
        t = (cur + " " + word).strip()
        if cur and f.getlength(t) > width:
            out.append(cur); cur = word
        else:
            cur = t
    return out + ([cur] if cur else [])


class Paper:
    def __init__(self):
        self.img = Image.new("L", (W, 6000), 255)
        self.d = ImageDraw.Draw(self.img)
        self.y = 0

    def bar(self, left_small, left_big, right_big="", right_small="", h=104):
        d = self.d
        d.rectangle([0, self.y, W, self.y + h], fill=0)
        d.text((M, self.y + 10), left_small, font=F(22, "b"), fill=255)
        d.text((M - 2, self.y + 30), left_big, font=F(62, "c"), fill=255)
        if right_big:
            fb = F(44, "c")
            d.text((W - M - fb.getlength(right_big), self.y + 24), right_big, font=fb, fill=255)
        if right_small:
            fs = F(20, "b")
            d.text((W - M - fs.getlength(right_small), self.y + 74), right_small, font=fs, fill=255)
        self.y += h + 18

    def text(self, s, f, x=M, width=None, gap=6, center=False):
        width = width or (W - x - M)
        for line in wrap(s, f, width):
            lx = (W - f.getlength(line)) / 2 if center else x
            self.d.text((lx, self.y), line, font=f, fill=0)
            self.y += f.size + gap

    def head(self, s):
        self.y += 8
        f = F(19, "b")
        x = M
        for ch in s:
            self.d.text((x, self.y), ch, font=f, fill=0)
            x += f.getlength(ch) + 3
        self.d.line([x + 8, self.y + 11, W - M, self.y + 11], fill=0, width=2)
        self.y += 32

    def dashed(self, gap_before=10):
        self.y += gap_before
        for x in range(M, W - M, 14):
            self.d.line([x, self.y, x + 7, self.y], fill=0, width=2)
        self.y += 14

    def keys(self, rows):
        fl, fv = F(23), F(31, "b")
        for label, value in rows:
            lw = W - 2 * M
            vw = fv.getlength(value)
            avail = lw - vw - 18
            label_lines = wrap(label, fl, avail) if vw < lw * 0.62 else None
            if label_lines and max(fl.getlength(l) for l in label_lines) > avail:
                label_lines = None
            top = self.y
            if label_lines:
                for i, ln in enumerate(label_lines):
                    self.d.text((M, self.y + 6 + i * 28), ln, font=fl, fill=0)
                self.d.text((W - M - vw, self.y), value, font=fv, fill=0)
                self.y += max(40, 10 + 28 * len(label_lines))
            else:
                self.text(label, fl, gap=4)
                self.text(value, fv, x=M + 20, gap=4)
                self.y += 4
            self.d.line([M, self.y, W - M, self.y], fill=0, width=1)
            self.y += 8

    def steps(self, items, numbered=True):
        f, fn = F(25), F(25, "b")
        for i, s in enumerate(items, 1):
            tag = f"{i}." if numbered else "–"
            self.d.text((M, self.y), tag, font=fn, fill=0)
            self.text(s, f, x=M + 40, gap=5)
            self.y += 6

    def boxes(self, items):
        f, fb = F(25), F(25, "b")
        for s in items:
            self.d.rectangle([M, self.y + 3, M + 24, self.y + 27], outline=0, width=3)
            bold, _, rest = s.partition("|")
            x = M + 38
            if rest:
                self.d.text((x, self.y), bold, font=fb, fill=0)
                x2 = x + fb.getlength(bold) + 10
                lines = wrap(rest.strip(), f, W - M - x2)
                if lines:
                    self.d.text((x2, self.y), lines[0], font=f, fill=0)
                    self.y += 32
                    for ln in lines[1:]:
                        self.d.text((x, self.y), ln, font=f, fill=0); self.y += 30
                else:
                    self.y += 32
            else:
                self.text(bold, f, x=x, gap=5)
            self.y += 6

    def warn(self, items):
        f = F(24, "b")
        for s in items:
            lines = wrap("⚠ " + s, f, W - 2 * M - 24)
            h = len(lines) * 31 + 16
            self.d.rectangle([M, self.y, W - M, self.y + h], fill=0)
            for i, ln in enumerate(lines):
                self.d.text((M + 12, self.y + 8 + i * 31), ln, font=f, fill=255)
            self.y += h + 8

    def big_box(self, s):
        f = F(30, "b")
        lines = wrap(s, f, W - 2 * M - 28)
        h = len(lines) * 38 + 20
        self.d.rectangle([M, self.y, W - M, self.y + h], outline=0, width=4)
        for i, ln in enumerate(lines):
            self.d.text((M + 14, self.y + 10 + i * 38), ln, font=f, fill=0)
        self.y += h + 10

    def foot(self, nxt, small):
        self.dashed(14)
        if nxt:
            fb, f = F(24, "b"), F(24)
            self.d.text((M, self.y), "NESTE", font=fb, fill=0)
            self.text(nxt, f, x=M + 96, gap=4)
        f = F(18)
        self.y += 6
        self.d.text((M, self.y), small, font=f, fill=0)
        self.y += 40

    def png(self):
        return self.img.crop((0, 0, W, self.y + 10))


DAYS = {"lør": ("LØRDAG 21.11", datetime(2026, 11, 21)), "søn": ("SØNDAG 22.11", datetime(2026, 11, 22))}

# ------------------------------------------------------------------ kjøkkenbillettene (bokmål)
# (dag, start, tittel, rett, varighet, nøkkeltall [(etikett, verdi)], gjør [...], timere ["X|tekst"], obs [...])
PREP = [
    ("lør", "08:00", "Rengjøring + etikettsjekk", None, "45 min",
     [("Kjøleskap", "≤ 4 °C"), ("Brett", "kjøtt · fisk · grønt")],
     ["Vask benk, brett, komfyr, ovnsbrett, tortillapresse, blender, sil, sous vide-kar.",
      "Etiketter på brett og bokser (maskeringstape).",
      "Sjekk etiketter: soyasaus, doubanjiang, XO, miso, chinkiang, shio koji, mirin, fiskesaus, "
      "bokhvetemel, rismel, bakepulver, masa, gelatin, plantefløte, soyadrikk, sjokolade, karbonadedeig.",
      "Alt som feiler eller er uklart: i NEI-boksen."],
     ["Mel/glutenvarer, egg og svin bort fra benken"],
     ["EGG: alvorlig allergi. Ingen egg på kjøkkenet i helgen.", "Vanlig soyasaus = hvete. Bruk GF tamari."]),
    ("lør", "08:45", "Panna cotta: kok og hell", "VII", "30 min",
     [("Plantefløte (soya)", "400 ml"), ("Soyadrikk", "120 ml"), ("Sukker", "55 g"),
      ("Vaniljestang", "1 stk"), ("Gelatin storfe/fisk", "8 g ≈ 1,5 %"), ("Varm til", "80 °C"),
      ("Dybde i formen", "25 mm"), ("Form", "≈ 15 × 15 cm")],
     ["Bløtlegg gelatin i kaldt vann (plater 5 min / pulver i 40 ml).",
      "Varm fløte, soyadrikk, sukker, vanilje og en klype salt til 80 °C. IKKE kok.",
      "Av platen, rør inn klemt gelatin. Sil.",
      "40 ml i et shotglass i fryseren som settetest. Resten i formen med plastfolie, 25 mm dypt.",
      "Rest i ramekin = testbit for PLA-formen (ikke til servering)."],
     ["15 min|settetest i fryseren", "20 min|avkjøl på benken, så kjøleskap (flatt, dekket)"],
     ["Gelatin må være storfe/fisk, ikke svin."]),
    ("lør", "09:15", "Løk i ovnen", "III", "5 min",
     [("Ovn", "190 °C"), ("Små løk, hele, med skall", "7 stk"), ("Tid", "50–70 min")],
     ["Hele løk med skall rett på brettet."],
     ["50 min|sjekk: spyd skal gli gjennom"], []),
    ("lør", "09:20", "Soppkraft i trykkokeren", "S1", "10 min",
     [("Tørket shiitake", "10 g"), ("Soppstilker/avskjær", "100–150 g"), ("Vann", "1,2 l"),
      ("Høyt trykk", "30 min"), ("Utløsning", "naturlig")],
     ["1 løk (halvert, med skall), ½ gulrot, 1 selleri, timian, persillestilker, 6 pepperkorn.",
      "Skjær stilkene av soppen nå. Hatter tilbake i kjøleskapet.", "Ikke salt."],
     ["30 min|trykk, så naturlig utløsning"], []),
    ("lør", "09:30", "Sylteloke + syltede grønnsaker", "II · VI", "25 min",
     [("Risedikk", "150 ml"), ("Vann", "150 ml"), ("Sukker", "35 g"), ("Salt", "9 g"),
      ("Østerssopp, blanchert", "60 g · 30 s")],
     ["Varm til alt er oppløst. Noen pepperkorn + en timiankvist.",
      "Krukke 1 (II): 1 gulrot i tynne skiver, 1 selleri, 1 sjalott i ringer, østerssopp.",
      "Krukke 2 (VI): ¼ rødløk i tynne strimler.", "Hell lake over, lokk, kjøleskap."],
     [], []),
    ("lør", "10:00", "Bærreduksjoner", "IV · VII", "25 min",
     [("Tyttebær (IV)", "250 g"), ("+ sukker / vann", "40 g / 50 ml"), ("Kok", "10 min"),
      ("Reduser til", "≈ 100 ml"), ("Solbær (VII)", "150 g"), ("+ sukker / vann", "35 g / 20 ml"),
      ("Kok", "5 min"), ("For tynn?", "½ ts potetmel")],
     ["To små kjeler. Kok, press gjennom sil.", "Hold av noen hele tyttebær til pynt.",
      "Potetmel røres ut i 1 ts kaldt vann, kok 30 s."],
     ["10 min|tyttebær", "5 min|solbær"], []),
    ("lør", "10:20", "Løk ut · sil kraften", "III · S1", "15 min",
     [("Kraft", "isbad → ≤ 4 °C")],
     ["Løk ut av ovnen, avkjøl på brettet.", "Sil soppkraften (ikke klem). Grunn boks i isbad, så kjøleskap."],
     [], []),
    ("lør", "10:40", "Dashi: etikett + smakstest", "V · S2", "15 min",
     [("Pulver: laktose?", "sjekk!"), ("Pulver: hvete?", "sjekk!"), ("Test", "100 ml, litt sterkere"),
      ("Reserve: kombu", "10 g/l · 60 °C · 20 min"), ("+ katsuobushi", "10–15 g/l · 80–85 °C · 2 min")],
     ["Les etiketten på dashipulveret. Laktose eller hvete = NEI.", "Bland 100 ml litt sterkere enn pakken, smak.",
      "Bonito-basert pulver: kombu kan tilsettes. Kombu-basert: katsuobushi."],
     [], ["Hondashi og lignende inneholder ofte laktose."]),
    ("lør", "11:00", "Kjøtt: trim + marinadetest", "IV", "20 min",
     [("Shio koji", "30 ml"), ("Einebær, knust", "6 stk"), ("Grov pepper", "½ ts"),
      ("Timian", "2 kvister"), ("Testbiter", "2 × 20 g"), ("Test", "30 + 60 min")],
     ["Trim sølvhinne. Avskjær til viltsausen.", "Skjær 2 testbiter. Marinér begge (kjøleskap).",
      "Hovedfileten pakkes tett, kjøleskap."],
     ["30 min|testbit 1 ut", "60 min|testbit 2 ut"],
     ["Ikke stikk hull i kjøttet: hel muskel er trygt rosa bare når overflaten er stekt."]),
    ("lør", "11:20", "Viltsaus", "IV · S3", "15 min aktivt",
     [("Olje", "10 ml"), ("Sjalott", "2 stk"), ("Einebær", "5 stk"), ("Rødvin / sake", "100 / 50 ml"),
      ("Soppkraft", "500 ml"), ("Trekk", "30–40 min"), ("Mål", "150–200 ml")],
     ["Brun avskjæret hardt. Sjalott, farge.", "Einebær + timian 30 s. Deglaser, reduser nesten tørt.",
      "Soppkraft inn, trekk."],
     ["35 min|sil og reduser"], []),
    ("lør", "11:35", "Stek testbit 1 (30 min)", "IV", "5 min",
     [("Panne", "rykende varm")],
     ["Tørk av marinaden, klapp tørr, stek alle sider, smak.", "Noter: for salt? passe? for lite?"],
     ["Notér: 30 min = ______"], []),
    ("lør", "12:05", "Stek testbit 2 (60 min)", "IV", "5 min",
     [("Panne", "rykende varm")],
     ["Samme som testbit 1. Velg tiden.", "Skriv vinnertiden her og på søndagsbilletten 15:15."],
     ["Marinadetid søndag: ______ min"], []),
    ("lør", "12:10", "Reduser viltsausen", "IV · S3", "15 min",
     [("Mål", "150–200 ml"), ("Tykk nok?", "dekker en skje"), ("Ellers", "½ ts potetmel")],
     ["Sil. Reduser til blank og fyldig.", "Ikke salt ferdig ennå (tyttebær kommer søndag). Avkjøl."], [], []),
    ("lør", "12:30", "LUNSJ. Sett deg ned.", None, "30 min", [], ["Spis noe. Drikk vann."], [], []),
    ("lør", "13:00", "Løkene: hul ut + duxelles + fyll", "III", "60 min",
     [("Lokk", "øverste ¼"), ("Vegg igjen", "2–3 lag · 6–8 mm"), ("Sjampinjong + østers", "250 + 100 g"),
      ("Olivenolje", "20 ml"), ("Sake + soppkraft", "30 ml + 2 ss"), ("Steketid", "15–20 min"),
      ("Gir", "≈ 160 g · 25–30 g/løk")],
     ["Skjær av lokket, skrell papirskall, skjær en flis av bunnen så den står.",
      "Ta ut innerlagene med teskje. Kjernen hakkes inn i duxellesen.",
      "Panne middels-høy: olje, 1 sjalott, 1 fedd hvitløk, kjernene 3 min → sopp + timian.",
      "Stek til HELT tørt, nøttebrunt. Sake + kraft, tørt igjen. Salt, pepper, persille.",
      "Fyll løkene i haug. Lokk ved siden av. Dekk til, kjøleskap."],
     [], []),
    ("lør", "14:00", "Sellerirot: purè + terninger", "IV", "20 min aktivt",
     [("Purè: sellerirot", "300 g"), ("+ soppkraft / vann", "200 / 100 ml"), ("Kok", "15–20 min"),
      ("Kjør med olivenolje", "30 ml"), ("Terninger 1,5 cm", "150 g"), ("Ovn", "200 °C · 25–30 min")],
     ["Purè: kok mykt, kjør silkemykt med olje (og litt kokevann). Ingen fløte, intet smør.",
      "Terninger: olje, salt, timian, til kantene er brune."],
     ["18 min|purè", "25 min|terninger"], []),
    ("lør", "14:30", "Svidd purre", "III", "15 min",
     [("Skiver", "1,5 cm"), ("Panne", "tørr, veldig varm"), ("+ vann / olje", "2 ss / 1 ts"),
      ("Lokk på", "6–8 min")],
     ["Snittsiden ned til den er svart.", "Vann + olje, lokk, til den er myk. I boks, kjøleskap."],
     ["7 min|purre med lokk"], []),
    ("lør", "14:50", "Grønn urteolje", "V · S5", "20 min aktivt",
     [("Dill", "25 g"), ("Persille", "15 g"), ("Gressløk", "noen strå"), ("Rapsolje", "100 ml"),
      ("Blanchér", "10 s → isvann"), ("Olje varm til", "60 °C"), ("Kjør", "2–3 min"), ("Kaffefilter", "30–60 min")],
     ["Klem urtene knusktørre i et kjøkkenhåndkle.", "Ikke klem filteret: da blir den grumsete.",
      "OGSÅ NÅ: legg 12 g tørket shiitake i bløt til XO-en kl. 15:15."],
     ["45 min|sjekk filteret"], []),
    ("lør", "15:15", "Skog-XO + skogsjord", "II · S8 · S9", "40 min",
     [("Shiitake (bløtlagt)", "12 g tørr"), ("Sjalott / hvitløk", "2 / 3 fedd"), ("Olje", "60 ml"),
      ("Svak varme", "15–20 min"), ("Tamari GF / fiskesaus", "1 ss / 1 ts"), ("Mirin / sukker", "1 ss / 1 ts"),
      ("Bokhvetekjerner, ristet", "30 g"), ("Svart sesam / shiitakepulver", "10 g / 5 g")],
     ["XO: shiitake, sjalott, hvitløk i olje til dypt brunt. Chili ½–1 ts 30 s.",
      "Tamari, fiskesaus, mirin, sukker, fres 1 min. Avkjøl, 5 g hvit sesam.",
      "Jord: rist kjerner og sesam tørt, kvern 5 g shiitake, 1 g salt + 1 g sukker, puls grovt."],
     ["18 min|XO-en"], ["Kun hvis XO/doubanjiang på glass feilet etikettsjekken (sannsynlig)."]),
    ("lør", "16:00", "Skumbaser: sopp + løk", "II · III", "30 min",
     [("Soppkraft (redusert fra 400)", "250 ml"), ("+ soppbiter, trekk", "40 g · 10 min"), ("Løk, ristet med III-løkene", "2 stk"),
      ("+ vann", "250 ml"), ("Olje per base", "15 ml"), ("Xantan per base", "0,6–0,8 g"), ("eller gelatin", "2,5 g (1 %)")],
     ["Kjør hver base glatt, sil GJENNOM klut/kaffefilter (fiber tetter dysen).",
      "Rør inn xantan (eller gelatin). Litt for salt: skum demper smak.", "Kjøleskap. Kaffe mens de kjøles.",
      "Sjekk at panna cottaen har satt seg."],
     [], ["Bare N₂O-patroner. Ingen eggehvite, ingen fløte."]),
    ("lør", "16:30", "Blini: tørrblanding + testbatch", "I", "30 min",
     [("Bokhvetemel GF", "52 g"), ("Rismel", "30 g"), ("Potetmel", "15 g"), ("Bakepulver", "4,5 g"),
      ("Salt / sukker", "1,5 / 3 g"), ("Test: ⅓ blanding +", "≈ 45 ml kullsyrevann"),
      ("Per blini", "1 toppet ts ≈ 7 g · 4 cm"), ("Steking", "≈ 1 min + 40 s")],
     ["Bland alt tørt (×1,5). Ta ⅓ til test med kullsyrevann + 5 ml olje.",
      "Røre som tykk yoghurt. Middels varme, lett oljet panne.", "Smak med og uten rogn."],
     ["Væske brukt: ______ ml"], ["Ingen egg i røra."]),
    ("lør", "17:00", "Tacofyll + masatest", "VI", "35 min",
     [("Karbonadedeig (100 % storfe)", "100 g"), ("Spisskummen", "½ ts"), ("Røkt paprika", "½ ts"),
      ("Tomatpuré", "1 ts"), ("Masatest", "15 g + ≈ 22 ml vann"), ("Tortilla i panne", "30 / 40 / 15–20 s")],
     ["Brun kjøttet hardt og fint. ½ sjalott, 1 fedd hvitløk, krydder, puré, kok tørt. Salt + lime.",
      "Kjøl raskt i grunn boks.", "Masa: press 1 tortilla, stek i tørr, varm panne. Fungerer den = ekte masa harina."],
     [], ["Ikke svin: sjekk at deigen ikke er blandingsdeig."]),
    ("lør", "17:35", "Testfrityr sopp + sprø sjalott", "II · III", "30 min",
     [("Olje sopp", "180 °C"), ("Mel: potetmel / rismel", "30 / 10 g"), ("Tid sopp", "2–3 min"),
      ("Olje sjalott", "160–170 °C"), ("Sjalott i rismel", "2 stk / 10 g"), ("Tid sjalott", "2–4 min")],
     ["Stek 3 testklaser sopp: noter tiden.", "Senk varmen, stek sjalottringer gylne. Tørk på papir, salt.",
      "Sjalott i lufttett boks med kjøkkenpapir."],
     ["Tid sopp: ______"], ["Egen olje: ikke brukt til noe panert eller svin."]),
    ("lør", "18:05", "Nøkken: innpakningstest", "V", "30 min",
     [("Mandolin", "1 mm"), ("Blanchér", "5–10 s → isvann"), ("Testtorsk sous vide", "60 °C · 20 min"),
      ("Bånd per porsjon", "5–6, vekselvis"), ("Overlapp", "≈ 3 mm"), ("Varm etter innpakking", "3–5 min tildekket")],
     ["Skjær noen bånd av grønn squash og gul squash. Blanchér, isvann, tørk GODT.",
      "Legg stripete matte på plastfolie, rull rundt torsken med folien, trekk ut folien.",
      "Varm, skjær i to: pene striper? glir båndene? tørr fisk?"],
     ["20 min|testtorsken", "Blancheringstid: ____ s"], ["Fiskebrett. Vask etterpå."]),
    ("lør", "18:35", "Sjokoladeflak", "VII", "10 min",
     [("Mørk sjokolade", "40 g"), ("Mikro", "20 s om gangen"), ("Tykkelse", "2 mm")],
     ["Smør utover bakepapir, kjøl, knekk i 6–8 flak (≈ 4 cm)."], [], []),
    ("lør", "18:45", "PLA-formtest på ramekinen", "VII", "10 min",
     [], ["Stikk ut av testramekinen. Sjekk form og slipp.", "Denne biten spises IKKE."], [],
     ["PLA-formen er prototype. Bjørnene som serveres stikkes med stålform."]),
    ("lør", "18:55", "Rydd, merk, sjekk", None, "45 min",
     [],
     ["Merk alt med rett + dato/klokke. Kjøleskapskart.", "Gå gjennom lørdagskveld-lappen (egen billett)."],
     [], []),
    ("lør", "19:40", "Dekk fram utstyr. Ferdig for i dag.", None, "30 min",
     [], ["Tallerkener, skåler, tekanne, skjeer, tacoholdere, stålform, pinsett, termometer."],
     [], []),

    ("søn", "08:30", "Oppsett: ett brett per rett", None, "30 min",
     [("Brett", "I · II · III · IV · V · VI · VII")],
     ["Kaffe. Kjøleskapet ut på benken i rekkefølge, merket I–VII.", "Les allergilappen én gang til."], [], []),
    ("søn", "08:52", "Stikk ut bjørnene", "VII", "25 min",
     [("Fryser først", "15 min"), ("Form", "STÅL"), ("Bjørner", "6 stk")],
     ["Formen med panna cotta i fryseren nå.", "Dypp stålformen i varmt vann, TØRK den.",
      "Rett ned, ikke vri. Løft formen, palett under, over på bakepapirlapp.",
      "Dekket, tilbake i kjøleskapet. Rester = kokkens."],
     ["15 min|form ut av fryseren"], ["Ikke PLA-formen på mat som serveres."]),
    ("søn", "09:30", "Tortilladeig", "VI", "20 min",
     [("Masa harina", "60 g"), ("Varmt vann", "80–90 ml"), ("Salt", "1 g"), ("Hvile", "15 min"),
      ("Kuler", "8 × 9–10 g")],
     ["Som lekedeig: ikke klissete, sprekker ikke når du klemmer den flat.", "Test én. Pakk deigen tett."],
     ["15 min|hvile"], []),
    ("søn", "10:00", "Sopp: riv klaser", "II", "15 min",
     [("Maitake + østers", "220 g")], ["Bite-store klaser i bolle, kjøleskap. IKKE mel dem ennå.", "Rør i skogsjorda."],
     [], []),
    ("søn", "10:30", "Blini: vei opp vått", "I", "15 min",
     [("Kaldt kullsyrevann", "85–95 ml"), ("Olje", "10 ml"), ("Dillkrem: soyayoghurt", "60 g"),
      ("Dilltupper", "14 stk")],
     ["Vått og tørt hver for seg til 15:15.", "Dillkrem (valgfri): silt yoghurt + sitronskall + salt + hakket dill."],
     [], []),
    ("søn", "11:00", "Torsk: porsjonér", "V", "20 min",
     [("Torsk", "6 × 50 g"), ("Salt totalt", "3 g · 15 min"), ("Olivenolje per pose", "5 ml"),
      ("Poser", "2 flate")],
     ["Porsjonér, salt, vent 15 min, tørk.", "I poser (vannforskyvning holder). Kjøleskap på is."],
     ["15 min|tørk av torsken"], ["Fiskebrett. Vask etterpå."]),
    ("søn", "11:30", "Squashbånd + maitake", "V · IV", "20 min",
     [("Mandolin", "1 mm"), ("Blanchér", "testtiden (5–10 s)"), ("Grønn + gul", "≈ 20 bånd hver")],
     ["Skjær bånd på langs (dropp kjernen). Blanchér, isvann, tørk på håndklær.",
      "Valgfritt: stek maitake brun med litt olje + timian (IV)."], [], []),
    ("søn", "12:00", "Lunsj (liten!)", None, "30 min", [], ["Det kommer mye mat senere."], [], []),
    ("søn", "12:45", "Dashi + smak til viltsaus", "V · IV · VI", "35 min",
     [("Vann", "700 ml"), ("Dashipulver", "pakkedose × 1,25"), ("Trekk", "70–80 °C · 20–30 min"),
      ("+ svidd sjalott, svidd vårløk", "1 + 2"), ("+ fenikel", "1 skive"), ("Til slutt", "salt + dråper risedikk"),
      ("Viltsaus + tyttebær", "start 2 ss")],
     ["Dashi: aldri kok. Sil fint, kaffefilter hvis uklar. Smak til. La stå i kjelen.",
      "Viltsaus: kok opp, tyttebær, salt, dråpe risedikk.", "Tacofyll: smak."], ["25 min|sil dashien"], []),
    ("søn", "13:30", "Ut av kjøleskapet", "III · IV", "5 min",
     [], ["Fylte løk, sellerirot-terninger og purre ut på benken (tildekket)."], [], []),
    ("søn", "14:00", "Tortillas · squashmatter", "VI · V", "30 min",
     [("Press til", "≈ 7 cm · 1,5 mm"), ("Stikk ut", "5,5–6 cm"), ("Tørr, varm panne", "30 / 40 / 15–20 s"),
      ("Squashmatter", "6 stk · 5–6 bånd"), ("Vekselvis", "grønn / gul")],
     ["Press, stikk ut, stek (snu, snu, den skal puste seg opp). Klut + folie.",
      "Legg 6 stripete matter på plastfolie. Stable med folie mellom. Kjøleskap."], [], []),
    ("søn", "14:30", "Rydd · klargjør", None, "15 min",
     [("Sous vide", "fylt"), ("Frityrolje", "i kjelen, AV")], ["Ren benk. Oppvaskmaskin."], [], []),
    ("søn", "14:45", "Serveringsoppsett", None, "15 min",
     [], ["Tallerkener per rett. Tekanne. Tacoholdere med bakepapir. Pinsett, skjeer, termometer, kluter."],
     [], []),
    ("søn", "15:10", "Ovn + sous vide på", None, "2 min",
     [("Ovn", "180 °C"), ("Sous vide", "60 °C")], ["Torsken går i badet 16:40."], [], []),
    ("søn", "15:15", "Blinirøre · kjøtt i marinade · skum", "I · IV · II · III", "20 min",
     [("Blini", "14 stk · ≈ 10 min"), ("Marinadetid kjøtt", "fra testen: ___ min"), ("Skumbaser varm til", "60 °C"),
      ("Sifong", "maks fyllstrek · 1 N₂O"), ("Ristes", "6–8 ganger"), ("Holdes i badet", "60 °C")],
     ["Rør vått i tørt, hvil 5 min, stek.", "Kjøttet i pose med marinaden, i kjøleskapet.",
      "Fyll sifongen(e), lad, rist, i sous vide-badet. Prøvesprut i en kopp."],
     ["___ min|kjøttet ut av marinaden"], ["Andre sifong? Ellers: tøm, skyll, fyll løkskum etter II."]),
]

# servering: (start, rett-tall, navn, varmt, kaldt, tallerken [...], sist, drikke, ved bordet, obs)
SERVE = [
    ("15:30", "I", "Pannekaka", "blini (lune)", "rogn, dillkrem",
     ["Liten kald tallerken", "2 blini", "dillkrem", "rogn", "dilltupp", "2–3 nigellafrø", "sitronskall"],
     "Rogn + dill i siste liten", "Highball", "", "Rognfri blini til egg-gjesten hvis det ikke er avklart."),
    ("15:45", "II", "Stabb-marsj", "sprø sopp, soppskum (60 °C)", "syltede grønnsaker",
     ["Mørk tallerken", "prikk skog-XO", "soppklase", "syltede biter lent mot", "skogsjord", "timian",
      "liten sky soppskum VED SIDEN"],
     "Skogsjord + skum (de mykner det sprø)", "Highball", "", ""),
    ("16:05", "III", "Peer Gynt", "løk, purre, løkskum (60 °C)", "–",
     ["Varm liten tallerken", "løk midt", "lokket lent mot", "1 purreskive", "sprø sjalott på fyllet",
      "vårløk", "liten sky løkskum ved siden"],
     "Løkskummet (ikke over lokket)", "Highball / bro til det røde", "De løfter lokket: ingen kjerne.", ""),
    ("16:35", "IV", "Doriske reinloin'er", "kjøtt, saus, purè, terninger", "–",
     ["Varm tallerken", "sveip purè", "3 skiver (8 mm)", "3 sellerirot-terninger", "sopp", "SAUS", "3 tyttebær",
      "timian"],
     "Sausen", "Gammel Bordeaux", "Saus fra liten mugge (valgfritt)", ""),
    ("17:05", "V", "Nøkken", "dashi ≈ 85 °C, innpakket torsk", "eple",
     ["Varm grunn skål", "innpakket torsk midt, sømmen ned", "litt eple ved siden", "dråper urteolje rundt",
      "(fenikeldusk)"],
     "Dashien: ved bordet", "Spansk hvitvin", "Hell 70–90 ml RUNDT torsken, lavt og sakte. Ikke skum.",
     "Skålene ut først, så én person heller for alle."),
    ("17:30", "VI", "Egil", "tortilla, fyll ≥ 75 °C", "syltet rødløk",
     ["Bakepapir i holderen", "varm tortilla", "10–12 g fyll", "3 løkstrimler", "1 blad", "lime ved siden"],
     "Bladet", "Rest av hvitvinen / Voulez-vous", "Del ut holderne. Ikke forklar navnet.", ""),
    ("17:55", "VII", "Kong Valemon", "–", "alt",
     ["Kald mørk tallerken", "bjørn (palett, papir ut)", "solbær 10–15 g VED SIDEN", "sjokoladeflak lent mot"],
     "Sjokoladeflaket", "«Voulez-vous»", "", ""),
]

# fyr-billetter under middagen: (start, tittel, rett, nøkkeltall, gjør, timere, obs)
FIRE = [
    ("15:35", "Frityr sopp · varmeskap", "II · III",
     [("Olje", "180 °C"), ("Sopp", "2 omganger · 2–3 min"), ("Ovn: løk + terninger + purre", "180 °C · folie"),
      ("Oppvarming", "15–20 min")],
     ["Mel klasene (30 g potetmel + 10 g rismel + 2 g salt), rist av overskudd.", "Stek, avrenn, salt med én gang.",
      "Løk, sellerirot-terninger og purre inn i ovnen."],
     ["18 min|løkene"], []),
    ("15:55", "Glaser løk · purè", "III · IV",
     [("Glasur: rød miso / mirin", "15 g / 15 ml"), ("+ risedikk / sukker", "5 ml / 5 g"), ("Uten folie", "3–5 min"),
      ("Fyll", "≥ 75 °C")],
     ["Pensle løk og fyll. Tilbake uten folie.", "Varm purèen i kjele (spe med kraft)."],
     ["4 min|glasur"], []),
    ("16:05", "Kjøtt: stek → ovn", "IV",
     [("Ovn ned til", "100 °C (døra opp 2 min)"), ("Stek alle sider", "45–60 s · ≈ 4 min"),
      ("Trekk ut ved kjerne", "50–52 °C"), ("Tid i ovn", "10–20 min")],
     ["Tørk av marinaden, klapp HELT tørr.", "Rykende varm panne + 15 ml olje. Alle sider og endene.",
      "På rist i ovnen med termometer.", "Viltsausen til kok."],
     ["12 min|sjekk kjernen"], ["Bruk termometeret, ikke klokka."]),
    ("16:25", "Kjøtt ut · hvil · skåler inn", "IV · V",
     [("Hvile", "8–10 min"), ("Ender på", "54–56 °C"), ("Skiver", "8 mm")],
     ["Kjøttet løst dekket.", "Ovn av, døra på gløtt 2 min, så V-skålene inn (lune, ikke varme).",
      "Varm IV-tallerkenene."],
     ["9 min|skjær kjøttet"], []),
    ("16:40", "Torsk i badet · eple", "V",
     [("Sous vide", "60 °C"), ("Tid", "20–25 min (tåler 40)"), ("Eple", "fin terning 2–3 mm")],
     ["Torskeposene i badet.", "Ta sifongene ut av badet.", "Eple i fine terninger, i sitronvann."],
     ["20 min|torsken er klar"], ["60 °C i kjernen er det parasittsikre trinnet."]),
    ("16:55", "Pakk inn torsken · dashi på kanna", "V",
     [("Dashi", "≈ 85 °C, ikke kok"), ("Kanne", "skoldet først"), ("Varm innpakket torsk", "3–5 min tildekket"),
      ("Per skål", "70–90 ml")],
     ["Torsken ut på papir. Rull hver bit i en squashmatte med folien, trekk ut folien, skjær endene.",
      "Inn i den avslåtte ovnen, tildekket.", "Skold tekanna, tøm, fyll dashi."], ["4 min|torsken ut"], []),
    ("17:20", "Taco: varm opp", "VI",
     [("Fyll", "≥ 75 °C"), ("Tortilla", "10–15 s per side")], ["Tørr panne. Bakepapir i holderne."], [], []),
    ("17:50", "Bjørnene ut", "VII",
     [("Solbær", "10–15 g / tallerken")], ["Kalde tallerkener. Bjørn på med paletten."], [], []),
    ("18:15", "Ekstranummer: Journey juice", None, [], ["Romkameraten tar den. Du er ferdig. Skål!"], [], []),
]


def hm(day, at):
    h, m = map(int, at.split(":"))
    return DAYS[day][1].replace(hour=h, minute=m)


def render_prep(t, idx, total, nxt, kind="prep"):
    day, at, title, course, dur, keys, steps, timers, obs = t
    p = Paper()
    p.bar(DAYS[day][0], at, f"{idx}/{total}", "MISE EN PLACE" if kind == "prep" else "UNDER MIDDAGEN")
    p.text(title, F(38, "b"), gap=6)
    meta = " · ".join(x for x in (course and f"Rett {course}" if course and not course.startswith("S") else course,
                                  dur and f"≈ {dur}") if x)
    if meta:
        p.text(meta, F(23, "i"))
    if keys:
        p.head("NØKKELTALL")
        p.keys(keys)
    if steps:
        p.head("GJØR")
        p.steps(steps)
    if timers:
        p.head("TIMER")
        p.boxes(timers)
    if obs:
        p.y += 6
        p.warn(obs)
    p.foot(nxt, f"Rampemat · {DAYS[day][0].lower()} · start {at}")
    return p.png()


def render_serve(s, nxt):
    at, num, name, hot, cold, plate, last, drink, table, obs = s
    p = Paper()
    p.bar("SØNDAG 22.11 · SERVERING", f"SERVER {num}", at)
    p.text(name, F(40, "b"), gap=8)
    p.keys([("Varmt", hot), ("Kaldt", cold)])
    p.head("TALLERKEN")
    p.steps(plate)
    p.head("SIST")
    p.big_box(last)
    p.keys([("Drikke", drink)])
    if table:
        p.head("VED BORDET")
        p.text(table, F(26, "b"))
    if obs:
        p.warn([obs])
    p.foot(nxt, f"Rampemat for guttestubbgnagare · rett {num} av VII")
    return p.png()


def render_note(title, small, sections):
    """Statiske lapper: [("NØKKELTALL", [(etikett, verdi)]) | ("BOKSER", [...]) | ("PUNKT", [...]) | ("OBS", [...])]."""
    p = Paper()
    p.bar(small, title, "", "", h=104)
    for kind, data in sections:
        if kind == "TEKST":
            p.text(data, F(25), gap=5); p.y += 6
        elif kind.startswith("#"):
            p.head(kind[1:])
        elif kind == "NØKKEL":
            p.keys(data)
        elif kind == "BOKSER":
            p.boxes(data)
        elif kind == "PUNKT":
            p.steps(data, numbered=False)
        elif kind == "OBS":
            p.warn(data)
    p.foot(None, "Rampemat for guttestubbgnagare · søndag 22. november 2026")
    return p.png()


ALLERGI = render_note("ALLERGI", "LES FØRST · GJELDER ALT", [
    ("OBS", ["EGG: ALVORLIG. Ingen egg, eggehvite, majones, eggpensling. Krysskontakt teller."]),
    ("#GJESTENE", None),
    ("NØKKEL", [("Egg", "ALVORLIG"), ("Gluten", "ingen"), ("Melk", "ingen (alt melkefritt)"),
                ("Nøtter", "ingen (ukjent nøtt)"), ("Svin", "ingen · heller ikke gelatin")]),
    ("#SJEKK ETIKETTEN", None),
    ("PUNKT", ["Soyasaus (hvete) → GF tamari", "Doubanjiang, XO, gochujang: trolig hvete", "Chinkiang-eddik: hveteklid",
               "Miso: ikke bygg (mugi)", "Dashipulver: laktose? hvete?", "Gelatin: storfe/fisk", "Plantefløte: ikke nøtt, havre bare hvis GF",
               "Sjokolade: ingen melk", "Bokhvetemel / masa: «kan inneholde gluten»?", "Kjøttdeig: 100 % storfe"]),
    ("#KJØKKENET", None),
    ("PUNKT", ["Egne brett: kjøtt · fisk · grønt", "Egen frityrolje", "Rene kluter, vask hender ofte",
               "Usikker? Ikke bruk det."]),
    ("#SPØR GJESTENE", None),
    ("BOKSER", ["Er fiskerogn ok for egg-allergikeren?", "Tåles melkeprotein?", "Hvilken nøtt? Kokos ok?",
                "Hvor strengt er «ikke svin»?", "Gravid eller immunsvekket?"]),
])

TEMP = render_note("TEMP + TID", "JUKSELAPP PÅ VEGGEN", [
    ("#TRYGGHET", None),
    ("NØKKEL", [("Kjøleskap", "≤ 4 °C"), ("Oppvarming (fyll, duxelles)", "≥ 75 °C"), ("Varmholding", "≥ 60 °C"),
                ("Kraft og saus", "kok opp"), ("Avkjøling", "isbad · inn innen 90 min")]),
    ("#KJØTT (IV)", None),
    ("NØKKEL", [("Marinade", "30–60 min (test)"), ("Steking", "45–60 s/side"), ("Ovn", "100 °C"),
                ("Trekk ut", "50–52 °C"), ("Hvile", "8–10 min → 54–56 °C"),
                ("Sous vide-plan B", "55–56 °C · maks 3 t")]),
    ("#FISK (V)", None),
    ("NØKKEL", [("Sous vide", "60 °C · 20–25 min"), ("Kjerne (parasitter)", "≥ 60 °C"), ("Dashi-trekk", "70–80 °C · 20–30 min"),
                ("Dashi i kanna", "≈ 85 °C, aldri kok"), ("Squashbånd", "1 mm · 5–10 s")]),
    ("#SKUM (II · III)", None),
    ("NØKKEL", [("Xantan", "0,25–0,3 %"), ("eller gelatin", "1 %"), ("Sifong i badet", "60 °C"), ("Patron", "N₂O, 1 per 0,5 l")]),
    ("#OVN OG OLJE", None),
    ("NØKKEL", [("Løk rå", "190 °C · 50–70 min"), ("Løk oppvarming", "180 °C · 15–20 min"),
                ("Sellerirot-terninger", "200 °C · 25–30 min"), ("Frityr sopp", "180 °C · 2–3 min"),
                ("Sprø sjalott", "160–170 °C · 2–4 min")]),
    ("#FORHOLD", None),
    ("NØKKEL", [("Sylteloke", "1 : 1 eddik/vann"), ("+ per 300 ml", "35 g sukker · 9 g salt"),
                ("Panna cotta gelatin", "1,5 % (8 g / 520 g)"), ("Masa : vann", "60 g : 80–90 ml"),
                ("Urteolje", "25 g dill + 15 g persille : 100 ml")]),
])

FREDAG = render_note("FREDAG", "FØR HELGEN", [
    ("#PÅ JOBB", None),
    ("BOKSER", ["Meny A5 (menu.pdf / menu-enkel.pdf) | 100 %, tykt papir, 6 stk",
                "Bjørneform-prototype | PLA, kanten opp",
                "6 tacoholdere | PLA, 15 % fyll", "Drikkekort A6 | valgfritt"]),
    ("#HANDLE (EN TUR)", None),
    ("BOKSER", ["Fiskehandler: torsk 300–350 g | + skrog 500–700 g, rogn 75–100 g",
                "Rein/hjort 500 g | velg på pris", "Stålform bjørn | ≈ 60 × 44 mm, ≥ 25 mm dyp",
                "Masa harina, GF tamari, einebær, storfegelatin | lett å glemme"]),
    ("#HJEMME", None),
    ("BOKSER", ["Kjøttdeig (+ vilt hvis frosset) | fryser → kjøleskap", "Fryse is i 2 brett",
                "Sende allergispørsmålene | til gjestene"]),
])

HANDLE = render_note("HANDLELISTE", "ÉN TUR · FREDAG", [
    ("#GRØNT + URTER", None),
    ("BOKSER", ["8 små løk", "8–9 sjalott", "1 rødløk", "1 purre", "1 bunt vårløk", "1 sellerirot 600–800 g",
                "2 grønne squash", "2 gule squash", "gressløk", "2 gule løk", "2 grønne epler", "1 liten fenikel", "1 hvitløk", "2 bunter dill", "timian", "persille",
                "2 gulrøtter", "2–3 selleristilker", "2 sitroner", "2 lime", "koriander (valgfri)"]),
    ("#SOPP", None),
    ("BOKSER", ["maitake 300 g", "østerssopp 300 g", "brun sjampinjong 250 g", "tørket shiitake 30 g"]),
    ("#KJØTT + FISK", None),
    ("BOKSER", ["reinsdyr/hjort ytrefilet ≈ 500 g", "torskeloin ≈ 350 g (7 × 50)",
                "rogn 75–100 g (ørret / løyrom)"]),
    ("#BÆR + DESSERT", None),
    ("BOKSER", ["tyttebær frosne 250–300 g", "solbær frosne 150–200 g", "soya-plantefløte 400–500 ml",
                "soyadrikk usøtet 250 ml", "soyayoghurt (valgfri)", "mørk sjokolade uten melk",
                "gelatin storfe/fisk", "vaniljestang"]),
    ("#TØRT", None),
    ("BOKSER", ["bokhvetemel GF", "rismel", "potetmel", "bokhvetekjerner 50 g", "svart sesam 30 g",
                "hvit sesam 15 g", "masa harina (sjekk skapet)"]),
    ("#SAUS + OLJE", None),
    ("BOKSER", ["GF tamari", "rapsolje 1 l", "xantangummi", "dashipulver uten laktose (sjekk!)", "kombu + katsuobushi (reserve)", "einebær", "tomatpuré", "kullsyrevann"]),
    ("#ANNET", None),
    ("BOKSER", ["bakepapir, plastfolie, fryseposer", "kaffefilter", "maskeringstape + tusj", "kjøkkenpapir",
                "stålform bjørn"]),
    ("OBS", ["Fotografer hver etikett du sjekker."]),
])

LORDAG_KVELD = render_note("KLAR?", "LØRDAG KVELD · SJEKKLISTE", [
    ("BOKSER", ["Panna cotta satt (+ testbit stukket)", "Solbærsaus", "Sjokoladeflak (eller søn 09:00)",
                "Soppkraft", "Viltsaus redusert · tyttebær", "Dashipulver sjekket / reserve klar",
                "Syltet (II) + rødløk (VI)", "Løk hult ut + fylt · lokk", "Svidd purre · sprø sjalott",
                "Misoglasur blandet / bestemt", "Sellerirot purè + terninger", "Marinadetid: ____ min",
                "Urteolje filtrert", "Innpakningstest ok: ___ s", "Skumbaser silt + kalde", "N₂O-patroner · sifong nr. 2?", "Skog-XO · skogsjord", "Blini tørrblanding testet", "Tacofyll kaldt · masa ok",
                "Alt merket · kjøleskap ≤ 4 °C", "Meny, holdere, stålform klare", "Tallerkener per rett"]),
    ("#IKKE GJORT, MED VILJE", None),
    ("TEKST", "Torsk · squashmatter · dashi · rogn · blini · sprø sopp · tortillas · kjøttsteking"),
])


def overview(day):
    rows = [(at, title) for d, at, title, *_ in PREP if d == day]
    if day == "søn":
        rows += [(at, f"SERVER {num} · {name}") for at, num, name, *_ in SERVE]
        rows += [(at, title) for at, title, *_ in FIRE]
        rows.sort()
    p = Paper()
    p.bar("DAGSPLAN", DAYS[day][0].split()[0], DAYS[day][0].split()[1], "")
    f, fb = F(24), F(24, "b")
    for at, title in rows:
        p.d.text((M, p.y), at, font=fb, fill=0)
        p.text(title, f, x=M + 92, gap=3)
        p.y += 7
    p.foot(None, f"Billettene skrives ut {LEAD} min før hver start.")
    return p.png()


def menu_png():
    """Gjestemenyen som én lang kvittering: lages fra SVG (assets i gangar-menu), rasteret til 576 punkt."""
    svg = HERE / "meny.svg"
    png = HERE / ".meny-tmp.png"
    subprocess.run(["rsvg-convert", "-w", str(W), "-b", "white", str(svg), "-o", str(png)], check=True)
    img = Image.open(png).convert("L").point(lambda v: 0 if v < 175 else 255)
    png.unlink()
    return img


def build():
    OUT.mkdir(exist_ok=True)
    for f in OUT.glob("*.png"):
        f.unlink()
    plan, test = [], []

    def save(name, img, label, at=None, lead=LEAD, group="test"):
        img.save(OUT / f"{name}.png")
        e = {"id": name, "label": label, "png": f"billettar/{name}.png", "group": group}
        if at:
            e["start"] = at.isoformat(timespec="minutes")
            e["print_at"] = (at - timedelta(minutes=lead)).isoformat(timespec="minutes")
        (plan if at else test).append(e)

    # testpakke: alt statisk + menyen + rettkortene
    save("00-meny", menu_png(), "Meny (mat + drikke)")
    save("01-allergi", ALLERGI, "Allergilapp")
    save("02-temp", TEMP, "Temperaturer og tider")
    save("03-fredag", FREDAG, "Fredag")
    save("04-handleliste", HANDLE, "Handleliste")
    save("05-dagsplan-lor", overview("lør"), "Dagsplan lørdag")
    save("06-dagsplan-son", overview("søn"), "Dagsplan søndag")
    save("07-klar-lordag", LORDAG_KVELD, "Lørdagskveld-sjekkliste")

    # tidsplan
    events = []
    for t in PREP:
        events.append((hm(t[0], t[1]), "prep", t))
    for s in SERVE:
        events.append((hm("søn", s[0]), "serve", s))
    for fr in FIRE:
        events.append((hm("søn", fr[0]), "fire", fr))
    events.sort(key=lambda e: (e[0], {"serve": 0, "fire": 1, "prep": 2}[e[1]]))
    by_day = {"lør": [e for e in events if e[0].day == 21], "søn": [e for e in events if e[0].day == 22]}
    for day, evs in by_day.items():
        for i, (at, kind, t) in enumerate(evs):
            nxt = None
            if i + 1 < len(evs):
                n_at, n_kind, n = evs[i + 1]
                n_title = n[2] if n_kind == "prep" else (f"SERVER {n[1]} · {n[2]}" if n_kind == "serve" else n[1])
                nxt = f"{n_at:%H:%M}  {n_title}"
            stamp = f"{ {'lør': 'lor', 'søn': 'son'}[day]}-{at:%H%M}"
            if kind == "prep":
                img, label = render_prep(t, i + 1, len(evs), nxt), f"{at:%H:%M} {t[2]}"
                name = f"{stamp}-mise"
            elif kind == "serve":
                img, label = render_serve(t, nxt), f"{at:%H:%M} SERVER {t[1]}"
                name = f"{stamp}-server-{t[1]}"
                save(f"1{['I','II','III','IV','V','VI','VII'].index(t[1])}-kort-{t[1]}", img,
                     f"Rettkort {t[1]}")  # rettkortene er også i testpakken
            else:
                at_, title, course, keys, steps, timers, obs = t
                img = render_prep(("søn", at_, title, course, None, keys, steps, timers, obs), i + 1, len(evs),
                                  nxt, kind="fire")
                label, name = f"{at:%H:%M} {title}", f"{stamp}-fyr"
            save(name, img, label, at=at, group=day)

    (HERE / "plan.json").write_text(json.dumps({"lead_min": LEAD, "test": test, "plan": plan},
                                               ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(test)} i testpakken, {len(plan)} planlagte billetter")


if __name__ == "__main__":
    build()
