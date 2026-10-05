// Weatherboy screen: Waveshare ESP32-S3-Touch-LCD-1.46 (412x412 round, SPD2010 display + touch).
// Shows what the handset is doing as a glowing orb that changes colour and motion with each state (asleep
// on the hook, listening - it ripples with your voice -, thinking, speaking, printing),
// and says "TAP" over USB when the glass is touched. The host (screen.py) drives it over the USB-C cable:
//
//   S <state>\t<title>\t<text>\n   state: idle listen think speak print error
//   L <0-255>\n                    mic level while listening
//   W <symbol>\t<temp>\t<wind>\t<from>\n   the weather: MET symbol code, °C, m/s, degrees the wind comes from.
//                                  Icon, temperature and a wind arrow for 30 s, the orb turns into the weather.
//   ?\n                            -> "WEATHERBOY SCREEN <fps>", so the host knows which port this is
//
// Build: arduino-cli compile --fqbn esp32:esp32:esp32s3:PSRAM=opi,FlashSize=16M,CDCOnBoot=cdc esp32s3
// Needs the GFX_Library_for_Arduino and U8g2 libraries (U8g2 only for its fonts: æøå).
#include <Arduino_GFX_Library.h>
#include <U8g2lib.h>
#include <Wire.h>

#define BL 5
#define TP_INT 4
#define SDA 11
#define SCL 10
#define EXPANDER 0x20  // TCA9554: EXIO1 = touch reset, EXIO2 = LCD reset
#define TOUCH 0x53
#define C 206          // centre of the 412x412 glass
#define R 206

Arduino_DataBus *bus = new Arduino_ESP32QSPI(21 /* CS */, 40 /* SCK */, 46, 45, 42, 41 /* D0-D3 */);
Arduino_GFX *panel = new Arduino_SPD2010(bus, GFX_NOT_DEFINED /* reset is on the expander */);
Arduino_Canvas *gfx = new Arduino_Canvas(412, 412, panel);  // in PSRAM; one flush per frame, no flicker

String state = "idle", title = "Weatherboy", text = "";
float level = 0, shown = 0;  // mic level as sent, and as drawn (smoothed)
uint32_t frames = 0;         // for the fps in the "?" reply

// ---- touch: the SPD2010's own little protocol, after Waveshare's Touch_SPD2010.cpp --------------------
void tpWrite(uint16_t reg, uint8_t lo, uint8_t hi) {
  Wire.beginTransmission(TOUCH);
  Wire.write(reg >> 8); Wire.write(reg & 0xFF); Wire.write(lo); Wire.write(hi);
  Wire.endTransmission();
  delayMicroseconds(200);
}

int tpRead(uint16_t reg, uint8_t *buf, int n) {
  Wire.beginTransmission(TOUCH);
  Wire.write(reg >> 8); Wire.write(reg & 0xFF);
  if (Wire.endTransmission(true)) return 0;
  int got = Wire.requestFrom(TOUCH, n), i = 0;
  while (Wire.available() && i < n) buf[i++] = Wire.read();
  delayMicroseconds(200);
  return got;
}

// true while a finger is on the glass. Each call also walks the chip through its boot states.
bool touched() {
  static bool down = false;
  uint8_t st[4], hdp[64], hs[8];
  if (tpRead(0x2000, st, 4) < 4) return false;
  bool exist = st[0] & 0x01, gesture = st[0] & 0x02, aux = st[0] & 0x08;
  bool bios = st[1] & 0x40, cpu = st[1] & 0x20, run = st[1] & 0x08;
  int len = min(st[3] << 8 | st[2], (int)sizeof hdp);
  if (bios) { tpWrite(0x0200, 0x01, 0x00); tpWrite(0x0400, 0x01, 0x00); }                       // clear INT, start CPU
  else if (cpu) { tpWrite(0x5000, 0, 0); tpWrite(0x4600, 0, 0); tpWrite(0x0200, 0x01, 0x00); } // point mode, start
  else if (run && len == 0) tpWrite(0x0200, 0x01, 0x00);
  else if (exist || gesture) {
    tpRead(0x0003, hdp, len);
    if (exist && hdp[4] <= 0x0A) down = len >= 10 && hdp[8] != 0;  // first finger's weight: 0 = lifted
    for (int i = 0; i < 4; i++) {                                    // drain the rest, then clear INT
      tpRead(0xFC02, hs, 8);
      if (hs[5] == 0x82) { tpWrite(0x0200, 0x01, 0x00); break; }
      if (hs[5] != 0x00) break;
      tpRead(0x0003, hdp, min(hs[2] | hs[3] << 8, (int)sizeof hdp));
    }
  } else if (run && aux) tpWrite(0x0200, 0x01, 0x00);
  return down;
}

void expander(uint8_t reg, uint8_t v) {
  Wire.beginTransmission(EXPANDER); Wire.write(reg); Wire.write(v); Wire.endTransmission();
}

// ---- the orb --------------------------------------------------------------------------------------
// A glowing blob, shaded per pixel on a 206x206 grid and doubled up into the canvas. Every state is a
// target look (colours, size, wobble, swirl, glow); the orb glides towards it, so changes morph instead
// of cut. Units below are full-screen pixels from the centre.
#define N 206                 // shading grid, half the glass each way
uint8_t *distT, *angT;        // per grid pixel: distance (px, capped at 255) and angle (0-255 = full turn)
int8_t sinT[256];             // sin, -127..127
uint16_t pal[256];            // brightness -> colour, rebuilt each frame from the current look
uint8_t glowT[256], hiT[256]; // falloff outside the edge; the soft highlight towards the middle
int16_t edge[256];            // the orb's radius at each angle, this frame

struct Look { float glow[3], core[3], radius, wobble, spin, swirl, halo, jitter; };
//                    glow colour        core colour        radius wob  spin  swirl halo jitter
const char *NAMES[] = {"idle", "listen", "think", "speak", "print", "error"};
const Look LOOKS[] = {
  {{40, 25, 120},  {90, 130, 255},  62, 0.05, 0.4, 0.3, 0.8, 0},  // idle: small, slow, deep blue - asleep
  {{0, 130, 130},  {110, 255, 210}, 88, 0.07, 0.8, 0.5, 1.2, 0},  // listen: wakes up teal, ripples with your voice
  {{150, 30, 140}, {255, 170, 60},  80, 0.10, 3.0, 1.0, 1.0, 0},  // think: magenta and amber, swirling fast
  {{30, 70, 220},  {170, 225, 255}, 92, 0.06, 1.2, 0.6, 1.4, 0},  // speak: big and bright, pulsing
  {{140, 100, 50}, {255, 235, 200}, 78, 0.04, 0.6, 0.4, 1.0, 0},  // print: warm paper white
  {{170, 0, 10},   {255, 90, 60},   74, 0.12, 1.5, 0.7, 1.1, 1},  // error: red, jittery
  // the weather, same order as SUN.. in sky()
  {{200, 100, 0},  {255, 215, 90},  84, 0.05, 0.5, 0.4, 1.5, 0},  // sun: amber, a wide warm halo
  {{60, 65, 80},   {185, 190, 205}, 86, 0.09, 0.4, 0.6, 1.1, 0},  // cloud: grey and lumpy
  {{10, 45, 140},  {90, 150, 255},  84, 0.08, 0.9, 0.7, 1.1, 0},  // rain: blue
  {{110, 120, 150}, {240, 245, 255}, 84, 0.05, 0.3, 0.3, 1.4, 0}, // snow: white, very calm
  {{80, 20, 140},  {255, 225, 120}, 84, 0.10, 1.5, 0.9, 1.0, 0.6}, // thunder: purple, flickering
  {{50, 55, 60},   {150, 155, 160}, 90, 0.03, 0.2, 0.2, 2.0, 0},  // fog: dim, all halo
  {{20, 25, 90},   {170, 180, 255}, 80, 0.04, 0.3, 0.3, 1.2, 0},  // clear night: moonlight
};
enum { SUN, PARTLY, CLOUD, RAIN, SNOW, THUNDER, FOG, MOON, MOONCLOUD };
String wxSymbol;              // the weather overlay: MET symbol, and what to write
float wxTemp, wxWind, wxFrom;
uint32_t wxUntil = 0;

int sky() {  // MET symbol code -> icon
  const String &s = wxSymbol;
  bool night = s.endsWith("_night");
  if (s.indexOf("thunder") >= 0) return THUNDER;
  if (s.indexOf("snow") >= 0 || s.indexOf("sleet") >= 0) return SNOW;
  if (s.indexOf("rain") >= 0) return RAIN;
  if (s.startsWith("fog")) return FOG;
  if (s.startsWith("cloudy")) return CLOUD;
  if (s.startsWith("partlycloudy") || s.startsWith("fair")) return night ? MOONCLOUD : PARTLY;
  return night ? MOON : SUN;
}

bool weather() { return wxUntil && millis() < wxUntil; }
Look now = LOOKS[0];
float phase = 0, kick = 0;    // swirl phase; kick: the shockwave when the state changes (1 -> 0)

int target() {  // an index, not a Look&: Arduino's generated prototypes come before the struct
  if (weather()) {
    int k = sky();
    return 6 + (k == MOON || k == MOONCLOUD ? 6 : k == PARTLY ? 0 : k > PARTLY ? k - 1 : 0);
  }
  for (int i = 1; i < 6; i++)
    if (state == NAMES[i]) return i;
  return 0;
}

void orbSetup() {
  distT = (uint8_t *)malloc(N * N);
  angT = (uint8_t *)malloc(N * N);
  for (int y = 0; y < N; y++)
    for (int x = 0; x < N; x++) {
      float dx = (x - N / 2 + 0.5) * 2, dy = (y - N / 2 + 0.5) * 2;
      distT[y * N + x] = min(255.0f, sqrtf(dx * dx + dy * dy));
      angT[y * N + x] = (uint8_t)((atan2f(dy, dx) / TWO_PI + 1) * 256);
    }
  for (int i = 0; i < 256; i++) sinT[i] = 127 * sin(i * TWO_PI / 256);
}

uint16_t mix(const float *a, const float *b, float k) {
  return gfx->color565(a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k);
}

void orb(float t, float dt) {
  const Look &to = LOOKS[target()];
  float *f = (float *)&now;
  const float *g = (const float *)&to;
  for (int i = 0; i < (int)(sizeof(Look) / sizeof(float)); i++) f[i] += (g[i] - f[i]) * min(1.0f, dt * 3);
  kick = max(0.0f, kick - dt * 0.9f);
  phase += dt * now.spin * 60;

  // the edge: a few slow harmonics, and your voice adds fast ones
  float v = shown, breathe = 1 + 0.04 * sin(t * 1.3) + (state == "speak") * 0.06 * sin(t * 9) * sin(t * 2.3);
  for (int a = 0; a < 256; a++) {
    float th = a * TWO_PI / 256;
    float w = now.wobble * (sin(3 * th + t * 0.9) + 0.6 * sin(5 * th - t * 1.4) + 0.4 * sin(2 * th + t * 0.5))
            + v * 0.22 * (sin(7 * th + t * 11) + 0.7 * sin(11 * th - t * 8))
            + now.jitter * 0.05 * sin(13 * th + t * 30);
    edge[a] = now.radius * (1 + w) * breathe * (1 + v * 0.25 + kick * 0.15);
  }

  // palette: black -> glow -> core -> white
  const float black[3] = {0, 0, 0}, white[3] = {255, 255, 255};
  for (int i = 0; i < 256; i++)
    pal[i] = i < 128 ? mix(black, now.glow, i / 128.0) : i < 210 ? mix(now.glow, now.core, (i - 128) / 82.0)
                                                                 : mix(now.core, white, (i - 210) / 60.0);
  for (int e = 0; e < 256; e++) {
    glowT[e] = 150 * expf(-e / (24 * now.halo)) * (0.85 + 0.15 * sin(t * 2));
    float k = 1 - e / (now.radius * 0.9);
    hiT[e] = k > 0 ? 70 * k * k : 0;
  }

  int swirl = 22 + now.swirl * 26, ph = (int)phase, ring = kick > 0 ? (1 - kick) * 320 : -999;
  uint16_t *fb = gfx->getFramebuffer(), row[412];
  for (int y = 0; y < N; y++) {
    const uint8_t *dRow = distT + y * N, *aRow = angT + y * N;
    for (int x = 0; x < N; x++) {
      int d = dRow[x], a = aRow[x], e = d - edge[a], b;
      if (e >= 0) b = glowT[min(e, 255)];
      else {  // inside: a spiral texture that turns, brighter towards the middle and at the rim
        b = 150 + hiT[d] + ((sinT[(d * 3 + a * 2 - ph) & 255] * swirl) >> 7) + (e > -6 ? (6 + e) * 8 : 0);
      }
      int s = abs(d - ring);
      if (s < 14) b += (14 - s) * 9 * kick;  // the shockwave
      uint16_t c = pal[constrain(b, 0, 255)];
      row[2 * x] = row[2 * x + 1] = c;
    }
    memcpy(fb + (2 * y) * 412, row, sizeof row);
    memcpy(fb + (2 * y + 1) * 412, row, sizeof row);
  }
}

// ---- text ------------------------------------------------------------------------------------------
int textWidth(const String &s) {
  int16_t x, y; uint16_t w, h;
  gfx->getTextBounds(s.c_str(), 0, 100, &x, &y, &w, &h);
  return w;
}

void centred(const String &s, int y, uint16_t colour) {  // with a shadow, to read over the glow
  int x = C - textWidth(s) / 2;
  gfx->setTextColor(RGB565_BLACK);
  gfx->setCursor(x + 2, y + 2);
  gfx->print(s);
  gfx->setTextColor(colour);
  gfx->setCursor(x, y);
  gfx->print(s);
}

// Word-wrapped, centred text that keeps inside the circle: lines near the edge get less room.
void paragraph(const String &s, int top, int lineH, int maxLines, uint16_t colour) {
  int y = top, from = 0, lines = 0;
  while (from < (int)s.length() && lines < maxLines) {
    int dy = abs(y - lineH / 2 - C), room = 2 * sqrt(max(0, (R - 24) * (R - 24) - dy * dy)) - 30;
    int end = from, next = from;
    while (next <= (int)s.length()) {  // grow the line one word at a time while it fits
      int sp = s.indexOf(' ', next);
      if (sp < 0) sp = s.length();
      if (textWidth(s.substring(from, sp)) > room && end > from) break;
      end = sp, next = sp + 1;
      if (sp == (int)s.length()) break;
    }
    String line = s.substring(from, end);
    if (++lines == maxLines && end < (int)s.length()) line += "...";
    centred(line, y, colour);
    from = end + 1, y += lineH;
  }
}

// ---- weather icons, drawn from circles and lines like the apps do ---------------------------------------
void cloud(int x, int y, uint16_t c) {
  gfx->fillCircle(x - 16, y + 4, 13, c);
  gfx->fillCircle(x + 2, y - 6, 18, c);
  gfx->fillCircle(x + 18, y + 5, 12, c);
  gfx->fillRoundRect(x - 29, y + 4, 59, 14, 7, c);
}

void sun(int x, int y, int r) {
  uint16_t c = gfx->color565(255, 200, 40);
  for (int i = 0; i < 8; i++) {
    float a = i * PI / 4;
    for (int w = -1; w <= 1; w++)
      gfx->drawLine(x + cos(a) * (r + 6) + w, y + sin(a) * (r + 6), x + cos(a) * (r + 13) + w, y + sin(a) * (r + 13), c);
  }
  gfx->fillCircle(x, y, r, c);
}

void moon(int x, int y, int r) {
  for (int i = 0; i < 360; i += 3) {  // a crescent: the part of one disc outside another
    float a = i * DEG_TO_RAD;
    for (int d = 0; d <= r; d++) {
      int px = x + cos(a) * d, py = y + sin(a) * d, ox = px - x - r / 2, oy = py - y + r / 3;
      if (ox * ox + oy * oy > r * r * 0.75) gfx->drawPixel(px, py, gfx->color565(230, 230, 200));
    }
  }
}

void icon(int x, int y) {
  uint16_t white = gfx->color565(235, 238, 245), grey = gfx->color565(150, 155, 165);
  int k = sky();
  if (k == SUN) sun(x, y, 22);
  if (k == MOON) moon(x, y, 24);
  if (k == PARTLY) sun(x + 16, y - 12, 15);
  if (k == MOONCLOUD) moon(x + 16, y - 12, 17);
  if (k == FOG) {
    for (int i = 0; i < 4; i++) gfx->fillRoundRect(x - 34 + (i % 2) * 8, y - 18 + i * 12, 60, 6, 3, grey);
    return;
  }
  if (k != SUN && k != MOON) cloud(x - (k == PARTLY || k == MOONCLOUD ? 6 : 0), y, k == CLOUD ? grey : white);
  for (int i = 0; i < 3; i++) {  // what falls out of it
    int bx = x - 16 + i * 16, by = y + 28;
    if (k == RAIN) for (int w = 0; w < 3; w++) gfx->drawLine(bx + w, by, bx - 5 + w, by + 12, gfx->color565(90, 160, 255));
    if (k == SNOW) gfx->fillCircle(bx - 2, by + 6, 3, white);
  }
  if (k == THUNDER) {
    uint16_t y2 = gfx->color565(255, 220, 60);
    gfx->fillTriangle(x + 2, y + 18, x - 10, y + 38, x, y + 36, y2);
    gfx->fillTriangle(x, y + 32, x + 10, y + 32, x - 6, y + 54, y2);
  }
}

void arrow(int x, int y, float from) {  // points where the wind blows to, like the apps
  float a = (from + 90) * DEG_TO_RAD;    // screen: 0° = up, clockwise; and "from" north blows south
  float dx = cos(a), dy = sin(a), px = -dy, py = dx;
  uint16_t c = RGB565_WHITE;
  for (int w = -1; w <= 1; w++)
    gfx->drawLine(x - dx * 14 + px * w, y - dy * 14 + py * w, x + dx * 6 + px * w, y + dy * 6 + py * w, c);
  gfx->fillTriangle(x + dx * 16, y + dy * 16, x + dx * 4 + px * 8, y + dy * 4 + py * 8, x + dx * 4 - px * 8, y + dy * 4 - py * 8, c);
}

void drawWeather() {
  icon(C, 92);
  gfx->setFont(u8g2_font_logisoso62_tn);
  String t = String((int)lroundf(wxTemp));
  int w = textWidth(t);
  centred(t, 250, RGB565_WHITE);
  gfx->fillCircle(C + w / 2 + 12, 196, 7, RGB565_WHITE);  // the degree sign (the digit font has none)
  gfx->fillCircle(C + w / 2 + 12, 196, 3, gfx->color565(40, 40, 40));
  gfx->setFont(u8g2_font_helvB24_tf);
  String v = String((int)lroundf(wxWind)) + " m/s";
  int vw = textWidth(v);
  arrow(C - vw / 2 - 8, 316, wxFrom);
  gfx->setTextColor(RGB565_WHITE);
  gfx->setCursor(C - vw / 2 + 18, 326);
  gfx->print(v);
}

void frame() {
  static uint32_t last = millis();
  float t = millis() / 1000.0, dt = (millis() - last) / 1000.0;
  last = millis();
  shown += (level - shown) * 0.35;
  orb(t, dt);

  uint16_t grey = gfx->color565(215, 215, 215);
  bool clock = state == "idle" && title.length() <= 5 && title.indexOf(':') > 0;
  if (weather()) drawWeather();
  else if (clock) {  // on the hook: the time over a sleepy orb
    gfx->setFont(u8g2_font_logisoso62_tn);
    centred(title, 237, RGB565_WHITE);
    gfx->setFont(u8g2_font_helvR18_tf);
    centred(text, 330, grey);
  } else {      // the orb is the message; a word or two at most (the host decides)
    gfx->setFont(u8g2_font_helvB24_tf);
    centred(title, 84, RGB565_WHITE);
    gfx->setFont(u8g2_font_helvR18_tf);
    paragraph(text, 308, 27, 3, grey);
  }
  gfx->flush();
  frames++;
}

// ---- the cable ------------------------------------------------------------------------------------
void command(String line) {
  if (line == "?") { Serial.printf("WEATHERBOY SCREEN %.1f fps\n", frames * 1000.0 / millis()); return; }
  if (line.startsWith("L ")) { level = line.substring(2).toInt() / 255.0; return; }
  if (line.startsWith("W ")) {
    int a = line.indexOf('\t'), b = line.indexOf('\t', a + 1), c = line.indexOf('\t', b + 1);
    if (c < 0) return;
    wxSymbol = line.substring(2, a);
    wxTemp = line.substring(a + 1, b).toFloat(), wxWind = line.substring(b + 1, c).toFloat();
    wxFrom = line.substring(c + 1).toFloat();
    wxUntil = millis() + 30000, kick = 1;
    return;
  }
  if (!line.startsWith("S ")) return;
  int a = line.indexOf('\t'), b = line.indexOf('\t', a + 1);
  String s = line.substring(2, a < 0 ? line.length() : a);
  if (s != state) level = 0, kick = 1;  // a shockwave on every change
  if (s == "think" || s == "speak" || s == "error") wxUntil = 0;  // the weather stays while it listens again
  state = s;
  title = a < 0 ? "" : line.substring(a + 1, b < 0 ? line.length() : b);
  text = b < 0 ? "" : line.substring(b + 1);
}

void setup() {
  Serial.begin(115200);
  Serial.setTxTimeoutMs(0);  // nobody reading must not stall the animation
  Wire.begin(SDA, SCL);
  expander(0x03, 0x00);      // all expander pins outputs (they power up high, which is "out of reset")
  expander(0x01, 0xFC);      // touch and LCD reset low...
  delay(50);
  expander(0x01, 0xFF);      // ...and released
  delay(50);
  gfx->begin();
  gfx->setUTF8Print(true);  // æøå arrive as UTF-8; without this each prints as two junk glyphs
  orbSetup();
  pinMode(TP_INT, INPUT_PULLUP);
  ledcAttach(BL, 5000, 8);
  ledcWrite(BL, 200);        // ponytail: fixed brightness; dim at night if it ever bothers anyone
  Serial.println("WEATHERBOY SCREEN");
}

void loop() {
  static String buf;
  static bool was = false;
  static uint32_t lastTp = 0;
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') { buf.trim(); command(buf); buf = ""; }
    else if (buf.length() < 600) buf += c;
  }
  if (millis() - lastTp > 25) {
    lastTp = millis();
    bool now = touched();
    if (was && !now) Serial.println("TAP");  // on lift-off, like a button
    was = now;
  }
  frame();
}
