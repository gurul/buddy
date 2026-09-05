// FluxGarage RoboEyes on a 1-bit LovyanGFX canvas. The ONLY TU that
// includes FluxGarage_RoboEyes.h (bare DEFAULT/N/E/S/W/ON/OFF macros).
#include "eyes.h"
#include "board_compat.h"
#include <FluxGarage_RoboEyes.h>

extern TFT_eSprite spr;

// RoboEyes wants an Adafruit_GFX-shaped target: clearDisplay(), display(),
// fillRoundRect(), fillTriangle(). LGFX_Sprite has the last two; add the
// first two. Colours are palette indices (BGCOLOR 0, MAINCOLOR 1).
class EyesCanvas : public LGFX_Sprite {
 public:
  using LGFX_Sprite::LGFX_Sprite;
  void clearDisplay() { fillSprite(0); }
  void display() {}                    // pushed by eyesTick(), not per draw
};

static EyesCanvas          canvas(&spr);
static RoboEyes<EyesCanvas> eyes(canvas);

// Geometry: two 96x96 eyes, radius 22, 36 px apart → 228 px of a 320 px
// row, centred by RoboEyes in the 320x204 canvas (54 px above/below).
// Listening grows them to 110 tall, busy squints to 67.
static const int EYE_W = 96, EYE_H = 96, EYE_R = 22, EYE_GAP = 36;
static const int EYE_H_BUSY = 67, EYE_H_LISTEN = 110;

struct EyesKey {
  uint8_t state = 0xFF; bool attn = false, listen = false, hot = false; int8_t side = 0;
  bool operator!=(const EyesKey& o) const {
    return state != o.state || attn != o.attn || listen != o.listen || hot != o.hot || side != o.side;
  }
};
static EyesKey  cur;
static uint8_t  lastPos      = 0xFF;
static uint32_t peekAt       = 0;    // sleep: next brief eye-open
static uint32_t peekCloseAt  = 0;


// ---- eye colour ----
static uint16_t eyeColor565 = 0x07FF;   // until eyesBegin picks one
static uint16_t bgColor565  = 0x0000;

static uint16_t rgb565(uint8_t r, uint8_t g, uint8_t b) {
  return (uint16_t)(((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3));
}
// HSV (h 0..359, s/v 0..1) -> RGB888.
static void hsvToRgb(float h, float s, float v, uint8_t* r, uint8_t* g, uint8_t* b) {
  float c = v * s, x = c * (1.0f - fabsf(fmodf(h / 60.0f, 2.0f) - 1.0f)), m = v - c;
  float rr = 0, gg = 0, bb = 0;
  if      (h <  60) { rr = c; gg = x; }
  else if (h < 120) { rr = x; gg = c; }
  else if (h < 180) { gg = c; bb = x; }
  else if (h < 240) { gg = x; bb = c; }
  else if (h < 300) { rr = x; bb = c; }
  else              { rr = c; bb = x; }
  *r = (uint8_t)((rr + m) * 255.0f + 0.5f);
  *g = (uint8_t)((gg + m) * 255.0f + 0.5f);
  *b = (uint8_t)((bb + m) * 255.0f + 0.5f);
}

static void pickEyeColor() {
  uint8_t r, g, b;
  if (EYE_COLOUR_OVERRIDE >= 0) {
    r = (EYE_COLOUR_OVERRIDE >> 16) & 0xFF; g = (EYE_COLOUR_OVERRIDE >> 8) & 0xFF; b = EYE_COLOUR_OVERRIDE & 0xFF;
  } else {
    uint32_t seed = esp_random();
    randomSeed(seed);                         // the rest of the pet's random() too
    float h = (float)(seed % 360);
    float s = 0.6f  + (float)((seed >> 9)  % 401) / 1000.0f;   // 0.60..1.00
    float v = 0.75f + (float)((seed >> 18) % 251) / 1000.0f;   // 0.75..1.00
    hsvToRgb(h, s, v, &r, &g, &b);
  }
  eyeColor565 = rgb565(r, g, b);
  Serial.printf("[eyes] colour #%02X%02X%02X\n", r, g, b);
}

uint16_t eyesColor() { return eyeColor565; }

void eyesBegin() {
  canvas.setPsram(false);              // 4,960 bytes: internal RAM, fast palette push
  canvas.setColorDepth(1);
  canvas.createSprite(EYES_W, EYES_H);
  eyes.begin(EYES_W, EYES_H, 50);
  eyes.setWidth(EYE_W, EYE_W);
  eyes.setHeight(EYE_H, EYE_H);
  eyes.setBorderradius(EYE_R, EYE_R);
  eyes.setSpacebetween(EYE_GAP);
  eyes.setDisplayColors(0, 1);         // palette indices: 0 bg, 1 eye
  pickEyeColor();
  canvas.setBitmapColor(eyeColor565, bgColor565);
  eyes.open();
  cur = EyesKey();
}

void eyesSetBackground(uint16_t bgRgb565) {
  bgColor565 = bgRgb565;
  canvas.setBitmapColor(eyeColor565, bgColor565);   // 1bpp: index 1 = fg, 0 = bg
}

// Full re-apply for a state; every knob is set so no previous state leaks.
static void applyState(const EyesKey& k, uint32_t now) {
  bool sleepy = false;
  eyes.setHeight(EYE_H, EYE_H);
  eyes.setSweat(OFF);
  eyes.setHFlicker(OFF, 0);
  eyes.setCuriosity(OFF);
  if (k.listen) {
    eyes.setMood(DEFAULT);
    eyes.open();
    eyes.setHeight(EYE_H_LISTEN, EYE_H_LISTEN);
    eyes.setAutoblinker(ON, 5, 1);
    eyes.setIdleMode(OFF);
  } else switch ((PersonaState)k.state) {
    case P_SLEEP:
      sleepy = true;
      eyes.setMood(TIRED);
      eyes.setAutoblinker(OFF);
      eyes.setIdleMode(OFF);
      eyes.close();
      peekAt = now + 20000; peekCloseAt = 0;
      break;
    case P_IDLE:
      eyes.setMood(DEFAULT);
      eyes.open();
      eyes.setAutoblinker(ON, 3, 2);
      eyes.setIdleMode(ON, 2, 2);
      break;
    case P_BUSY:
      eyes.setMood(DEFAULT);
      eyes.open();
      eyes.setCuriosity(ON);
      eyes.setHeight(EYE_H_BUSY, EYE_H_BUSY);   // squint: focused
      eyes.setAutoblinker(ON, 1, 1);           // ~1.5 s ±1 (integer seconds API)
      eyes.setIdleMode(ON, 1, 1);
      break;
    case P_ATTENTION:
      eyes.setMood(ANGRY);
      eyes.open();
      eyes.setHFlicker(ON, 2);
      eyes.setAutoblinker(ON, 1, 1);
      eyes.setIdleMode(OFF);
      eyes.setSweat(k.hot ? ON : OFF);         // destructive prompt pending
      break;
    case P_CELEBRATE:
      eyes.setMood(HAPPY);
      eyes.open();
      eyes.setAutoblinker(ON, 3, 2);
      eyes.setIdleMode(OFF);
      eyes.anim_laugh();
      break;
    case P_HEART:
      eyes.setMood(HAPPY);
      eyes.open();
      eyes.setCuriosity(ON);
      eyes.setAutoblinker(ON, 3, 2);
      eyes.setIdleMode(OFF);
      break;
    case P_DIZZY:
      eyes.setMood(DEFAULT);
      eyes.open();
      eyes.setHFlicker(ON, 3);
      eyes.setAutoblinker(OFF);
      eyes.setIdleMode(OFF);
      eyes.anim_confused();
      break;
  }
  if (!sleepy) peekAt = 0;
  lastPos = 0xFF;                      // force a position re-apply next eyesLookAt
}

void eyesSet(PersonaState s, bool needsAttention, bool listening, bool hotPrompt, int8_t gazeSide) {
  EyesKey k;
  k.state = (uint8_t)s; k.attn = needsAttention; k.listen = listening;
  k.hot = hotPrompt && (s == P_ATTENTION || needsAttention); k.side = gazeSide;
  if (!(k != cur)) return;
  bool sideOnly = k.state == cur.state && k.attn == cur.attn && k.listen == cur.listen && k.hot == cur.hot;
  cur = k;
  if (sideOnly) { lastPos = 0xFF; return; }   // gaze handled by eyesLookAt
  applyState(k, millis());
}

// Head yaw/pitch → eye position band. +yaw is the robot's left, i.e. toward
// a person at the screen's right, so the eyes go E. Head up (attention) uses
// the N row. When the head is centred, a remembered toucher side still makes
// the eyes glance that way.
static bool cardUp = false;
void eyesCardUp(bool up) {
  if (up == cardUp) return;
  cardUp = up;
  lastPos = 0xFF;                      // re-evaluate the row on the next eyesLookAt
}

void eyesLookAt(int8_t yawDeg, int8_t pitchDeg) {
  // Head up (attention) or a card owning the lower band: N row (y 0..96).
  bool up = pitchDeg >= 62 || cardUp;
  // +yaw = robot's right = viewer's left (bench 2026-09-05), so the eyes go W.
  int8_t h = yawDeg >= 20 ? -1 : yawDeg <= -20 ? 1 : cur.side;
  uint8_t pos = DEFAULT;
  if (up)      pos = h > 0 ? NE : h < 0 ? NW : N;
  else if (h)  pos = h > 0 ? E : W;
  if (pos == lastPos) return;
  lastPos = pos;
  // Applied only on a band change, so idle mode (IDLE/BUSY) keeps wandering
  // between changes and a card clearing always re-centres the eyes.
  eyes.setPosition(pos);
}
void eyesLookAt(int8_t yawDeg) { eyesLookAt(yawDeg, 45); }

void eyesTick(uint32_t now) {
  // Sleep: a brief peek every ~20 s so the pet reads as asleep, not off.
  if (peekAt && (int32_t)(now - peekAt) >= 0) {
    eyes.open();
    peekCloseAt = now + 600;
    peekAt = now + 18000 + random(5000);
  }
  if (peekCloseAt && (int32_t)(now - peekCloseAt) >= 0) {
    eyes.close();
    peekCloseAt = 0;
  }
  eyes.update();                       // redraws at most every 20 ms
  canvas.pushSprite(&spr, 0, EYES_Y);  // palette → RGB565 into the frame
}

const char* eyesStatusText(PersonaState s, bool listening) {
  if (listening) return "listening...";
  switch (s) {
    case P_SLEEP:     return "zzz";
    case P_BUSY:      return "working...";
    case P_ATTENTION: return "needs you!";
    case P_CELEBRATE: return "done!";
    case P_HEART:     return "<3";
    case P_DIZZY:     return "@_@";
    default:          return "";
  }
}
