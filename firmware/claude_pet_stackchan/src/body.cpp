// K151 body choreography: head servos, LEDs, top touch. Includes
// M5StackChan.h (and through it M5Unified's global `M5`); must never include
// board_compat.h — see hal_m5.h. The face lives in eyes.cpp.
#include "body.h"
#include "chirp.h"
#include "gaze.h"
#include <M5StackChan.h>

// ---- servo limits (degrees) ----
// M5Stack advises 5..85 on the pitch axis; the BSP itself allows 0..90.
// Every pitch write goes through clampPitch(). Yaw is free on the bus but
// the choreography stays inside ±YAW_MAX so the cable loom never binds.
static const int PITCH_MIN = 5;
static const int PITCH_MAX = 85;
static const int YAW_MAX   = 60;

// ---- poses (degrees) — bench-tune these on the real robot ----
// Pitch 0 is the bottom of the servo range (chin down), 90 is straight up.
// PITCH_LEVEL is the "looking at the desk owner" gaze; it depends on how the
// head was zeroed (NVS servo/zero_pos_2), so treat 45 as a starting guess.
static const int PITCH_LEVEL     = 45;
static const int PITCH_SLEEP     = 10;   // inside 5..15 per the port spec
static const int PITCH_ATTENTION = 70;
// Toucher tilt/turn. BSP moveX(+) is "turn left" from the robot's point of
// view, i.e. toward a person standing at the screen's right. Flip the sign
// if the bench shows the head turning away.
// Sign convention, bench-verified 2026-09-05 with the camera spike: BSP +yaw
// turns the head to the ROBOT'S RIGHT, i.e. the viewer's LEFT. A toucher on
// the screen's right (side = +1) is therefore reached with a NEGATIVE yaw.
static const int YAW_TOWARD_SCREEN_RIGHT = -12;  // heart tilt
static const int YAW_FOUND_YOU           = -28;  // attention "found you" turn
static const uint32_t FOUND_YOU_HOLD_MS  = 12000;

static int clampPitch(int deg) {
  if (deg < PITCH_MIN) return PITCH_MIN;
  if (deg > PITCH_MAX) return PITCH_MAX;
  return deg;
}
static int clampYaw(int deg) {
  if (deg < -YAW_MAX) return -YAW_MAX;
  if (deg > YAW_MAX)  return YAW_MAX;
  return deg;
}

// ---- keyframe player ----
// A sequence is a list of {delay from sequence start, yaw, pitch, speed}.
// KEEP leaves that axis at its last commanded target. bodyUpdate() steps the
// player; nothing here blocks.
static const int8_t KEEP = 127;
struct Key { uint16_t atMs; int8_t yaw; int8_t pitch; uint16_t speed; };

static const Key* seq      = nullptr;
static uint8_t    seqN     = 0;
static uint8_t    seqI     = 0;
static uint32_t   seqStart = 0;
static int        curYaw   = 0;
static int        curPitch = PITCH_LEVEL;
static Key        dyn[8];            // scratch for sequences built at runtime

// ---- motion tween ----
// The BSP already runs a critically-damped spring per servo (stiffness from
// the `speed` arg: k = 10 + s^2 * 640, c = 2*sqrt(k), 50 Hz task) but every
// move is a one-shot target, so big jumps snap. We keep the spring as the
// servo-side follower (auto-angle-sync OFF so streamed targets do not
// teleport the animation) and glide the TARGET ourselves: headTo() records
// a glide from the last commanded pose along easeInOutCubic; stepTween()
// streams the interpolated pose at 25 Hz with the servo speed scaled to the
// step size (100..600) so the spring tracks each 40 ms step without lag.
// Glide time = max(300 ms, 6 ms/deg of the larger axis), scaled by the
// sequence's speed hint (500 = 1x; 900 = snappy 150 ms floor).
static float    cmdYaw = 0, cmdPitch = 0;      // pose streamed to the servos
static float    fromYaw = 0, fromPitch = 0;    // glide start
static uint32_t glideStart = 0, glideMs = 0;
static bool     gliding = false;
static uint32_t lastStreamMs = 0;
static float    sentYaw = 1e9f, sentPitch = 1e9f;
static bool     driftOn = false;               // micro-drift while awake

static float easeInOutCubic(float t) {
  return t < 0.5f ? 4.0f * t * t * t : 1.0f - powf(-2.0f * t + 2.0f, 3.0f) / 2.0f;
}

static void headTo(int yawDeg, int pitchDeg, int speed) {
  curYaw   = clampYaw(yawDeg);
  curPitch = clampPitch(pitchDeg);
  fromYaw = cmdYaw; fromPitch = cmdPitch;
  float dmax = fmaxf(fabsf(curYaw - fromYaw), fabsf(curPitch - fromPitch));
  float base = fmaxf(300.0f, 6.0f * dmax);
  float factor = 500.0f / (float)(speed < 100 ? 100 : speed);   // speed hint: 500 = 1x
  if (factor < 0.3f) factor = 0.3f;
  if (factor > 2.5f) factor = 2.5f;
  float ms = base * factor;
  float floorMs = speed >= 800 ? 150.0f : 300.0f;
  if (ms < floorMs) ms = floorMs;
  glideMs = (uint32_t)ms;
  glideStart = millis();
  gliding = true;
}

bool bodyMoving() { return gliding || M5StackChan.Motion.isMoving(); }

// Advance the glide, add the idle micro-drift, stream to the BSP at 25 Hz.
static void stepTween(uint32_t now) {
  float y = cmdYaw, p = cmdPitch;
  if (gliding) {
    float t = glideMs ? (float)(now - glideStart) / (float)glideMs : 1.0f;
    if (t >= 1.0f) { t = 1.0f; gliding = false; }
    float e = easeInOutCubic(t);
    y = fromYaw + (curYaw - fromYaw) * e;
    p = fromPitch + (curPitch - fromPitch) * e;
    cmdYaw = y; cmdPitch = p;
  }
  // Perlin-ish micro-drift: two sines at unrelated periods (5.3 s / 4.1 s),
  // ±1.5 deg yaw, ±1 deg pitch. Only while awake and not gliding, so the
  // head never looks parked but a sleeping pet stays still (torque releases).
  if (driftOn && !gliding) {
    float ty = (float)now / 1000.0f;
    y += 1.5f * sinf(ty * (6.2832f / 5.3f)) * 0.7f + 1.5f * sinf(ty * (6.2832f / 7.9f)) * 0.3f;
    p += 1.0f * sinf(ty * (6.2832f / 4.1f) + 1.0f);
  }
  if (now - lastStreamMs < 40) return;                // 25 Hz max
  float step = fmaxf(fabsf(y - sentYaw), fabsf(p - sentPitch));
  if (step < 0.05f) return;                           // nothing new: let torque release
  lastStreamMs = now;
  int speed = (int)(100.0f + step * 120.0f);          // ~1 deg step -> 220, 4 deg -> 580
  if (speed < 100) speed = 100;
  if (speed > 600) speed = 600;
  sentYaw = y; sentPitch = p;
  float cy = fminf(fmaxf(y, (float)-YAW_MAX), (float)YAW_MAX);
  float cp = fminf(fmaxf(p, (float)PITCH_MIN), (float)PITCH_MAX);   // drift can never leave 5..85
  M5StackChan.Motion.move((int)lroundf(cy * 10.0f), (int)lroundf(cp * 10.0f), speed);   // 0.1 deg units
}

static void play(const Key* k, uint8_t n, uint32_t now) {
  seq = k; seqN = n; seqI = 0; seqStart = now;
}

static void stepSeq(uint32_t now) {
  while (seq && seqI < seqN && (int32_t)(now - (seqStart + seq[seqI].atMs)) >= 0) {
    const Key& k = seq[seqI++];
    int y = (k.yaw   == KEEP) ? curYaw   : k.yaw;
    int p = (k.pitch == KEEP) ? curPitch : k.pitch;
    headTo(y, p, k.speed);
  }
  if (seq && seqI >= seqN) seq = nullptr;
}

// ---- fixed sequences ----
static const Key SEQ_SLEEP[]     = { {0, 0, PITCH_SLEEP, 150} };
static const Key SEQ_LEVEL[]     = { {0, 0, PITCH_LEVEL, 250} };
static const Key SEQ_ATTN_UP[]   = { {0, 0, PITCH_ATTENTION, 500} };
// "Where are you?" search: far left (hold 0.9 s), near left, near right,
// far right, centre. ~3 s, repeated every 5 s while a session waits.
static const Key SEQ_ATTN_SCAN[] = {
  {0,    -40, PITCH_ATTENTION, 500}, {900,  -12, PITCH_ATTENTION, 400},
  {1400,  15, PITCH_ATTENTION, 400}, {1900,  40, PITCH_ATTENTION, 500},
  {2500,   0, PITCH_ATTENTION, 400} };
static const Key SEQ_NOD[]       = { {0, KEEP, PITCH_LEVEL - 8, 400}, {450, KEEP, PITCH_LEVEL, 400} };
static const Key SEQ_CELEBRATE[] = {
  {0, 20, PITCH_LEVEL + 10, 900}, {150, -20, KEEP, 900}, {300, 12, KEEP, 900},
  {450, -12, KEEP, 900}, {600, 0, PITCH_LEVEL, 600} };
static const Key SEQ_DIZZY[]     = {
  {0, 20, PITCH_LEVEL, 900}, {200, -20, KEEP, 900}, {400, 20, KEEP, 900}, {600, -20, KEEP, 900},
  {800, 20, KEEP, 900}, {1000, -20, KEEP, 900}, {1200, 0, PITCH_LEVEL, 500} };
#define NKEYS(a) ((uint8_t)(sizeof(a) / sizeof((a)[0])))

// ---- state ----
static uint8_t  lastState   = 0xFF;
static uint32_t enteredAt   = 0;
static uint32_t nextIdleAt  = 0;
static uint32_t nextNodAt   = 0;
static uint32_t nextScanAt  = 0;
static int8_t   toucherSide = 0;
static uint32_t toucherAt   = 0;
static bool     listening   = false;
static int8_t   listenSide  = 0;
static bool     ledEnabled  = true;
static bool     micLive     = false;
// gaze hold (toucher / bodyLookAt): overrides the periodic behaviours
static int      gazeYaw     = 0;
static int      gazePitch   = -1;      // -1: keep the state's pitch
static uint32_t gazeUntil   = 0;
static bool     gazePending = false;   // a new gaze target waits to be applied
static int8_t   gazeSide    = 0;       // toucher side behind the current hold, 0 for bodyLookAt
// top touch arming
static uint32_t touchArmAt  = 0;
static bool     touchArmed  = false;

static bool gazeHeld(uint32_t now) { return (int32_t)(now - gazeUntil) < 0; }

int    bodyYawDeg()   { return curYaw; }
int    bodyPitchDeg() { return curPitch; }
int8_t bodyGazeSide() {
  if (listening) return listenSide;
  return gazeHeld(millis()) ? gazeSide : 0;
}
static const int PITCH_LISTEN = PITCH_LEVEL + 15;

bool bodySearchSweep() {
  uint32_t now = millis();
  if (seq || gazeHeld(now)) return false;
  int8_t pitch = (int8_t)(listening ? PITCH_LISTEN : PITCH_ATTENTION);
  for (uint8_t i = 0; i < NKEYS(SEQ_ATTN_SCAN); i++) {
    dyn[i] = SEQ_ATTN_SCAN[i];
    dyn[i].pitch = pitch;
  }
  play(dyn, NKEYS(SEQ_ATTN_SCAN), now);
  return true;
}

void bodyLookAt(int8_t yawDeg, uint16_t holdMs) {
  if (holdMs == 0) { gazeUntil = 0; gazePending = false; gazeSide = 0; return; }
  gazeYaw     = clampYaw(yawDeg);
  gazePitch   = -1;
  gazeUntil   = millis() + holdMs;
  gazePending = true;
  gazeSide    = 0;
}
void bodyLookAt(int8_t yawDeg, int8_t pitchDeg, uint16_t holdMs) {
  bodyLookAt(yawDeg, holdMs);
  if (holdMs) gazePitch = clampPitch(pitchDeg);
}

void bodyNoteToucher(int8_t side) {
  toucherSide = side;
  toucherAt   = millis();
  gazeNoteTouch(side);     // a touch is an owner observation too
  if (listening) return;   // pose is pinned on the user; keep it
  // Attention: turn toward the toucher and hold ("found you"). Other states
  // only remember the side for the heart tilt; a card drag mid-busy should
  // not yank the head around.
  if (lastState == (uint8_t)P_ATTENTION) {
    bodyLookAt(side * YAW_FOUND_YOU, FOUND_YOU_HOLD_MS);
    gazeSide = side;
  }
}

void bodyLedPolicy(bool enabled, bool mic) { ledEnabled = enabled; micLive = mic; }

// ---- LED choreography ----
// Three at-a-glance states: sleep = very dim slow blue breathe, busy =
// steady dim cyan, attention = full-brightness orange pulse on all 12.
// One-shots: heart pink, celebrate green, dizzy off. Written through ledSet()
// (board_compat.cpp), which drops unchanged colours, so the quantised ramps
// below cost one I2C write per step, not one per frame.
void ledSet(uint8_t r, uint8_t g, uint8_t b);   // board_compat.cpp
static void ledForState(PersonaState s, uint32_t now) {
  if (!ledEnabled) { ledSet(0, 0, 0); return; }
  if (micLive)     { ledSet(0, 30, 90); return; }
  switch (s) {
    case P_ATTENTION: {
      // 800 ms triangle between a dim ember and full orange, 16 steps.
      uint32_t ph = (now % 800) * 32 / 800;         // 0..31
      uint32_t k  = ph < 16 ? ph : 31 - ph;         // 0..15..0
      ledSet(40 + k * 215 / 15, 12 + k * 68 / 15, 0);
      break;
    }
    case P_BUSY:      ledSet(0, 28, 40); break;
    case P_HEART:     ledSet(60, 0, 20); break;
    case P_CELEBRATE: ledSet(0, 60, 10); break;
    case P_SLEEP:
    case P_IDLE: {
      // 4 s breathe, 0..6 brightness, 8 steps: visible in a dark room only.
      uint32_t ph = (now % 4000) * 16 / 4000;       // 0..15
      uint32_t k  = ph < 8 ? ph : 15 - ph;          // 0..7..0
      ledSet(0, 0, (uint8_t)(k * 6 / 7));
      break;
    }
    default:          ledSet(0, 0, 0); break;
  }
}

// ---- lifecycle ----
void bodyBegin() {
  M5StackChan.setServoPowerEnabled(true);
  M5StackChan.Motion.setAutoTorqueReleaseEnabled(true);   // release at rest (sleep)
  // Streamed 25 Hz targets: do not teleport the spring to the bus-read angle
  // on every target (that is the BSP's "stutter" case).
  M5StackChan.Motion.setAutoAngleSyncEnabled(false);
  M5StackChan.Motion.goHome(300);
  curYaw = 0; curPitch = 0;
  cmdYaw = 0; cmdPitch = 0; sentYaw = 0; sentPitch = 0; gliding = false;
  lastState = 0xFF;
  // The Si12T baseline is stale right after the servo 5 V rail comes up
  // (bench 2026-09-05: the middle zone read pressed at boot and fired a
  // phantom push-to-talk). Recalibrate once the rail has settled; until
  // then bodyTouchZone() reports idle.
  touchArmAt = millis() + 3000;
  touchArmed = false;
  Serial.printf("[body] servo yaw=%d pitch=%d (0.1deg) bat=%.2fV %.0fmA\n",
                M5StackChan.Motion.getCurrentXAngle(), M5StackChan.Motion.getCurrentYAngle(),
                M5StackChan.getBatteryVoltage(), M5StackChan.getBatteryCurrent());
}

static uint32_t lastAttnChirp = 0;
static bool     quiet = false;       // true while re-entering a state silently
static void say(ChirpKind k, bool force = false) { if (!quiet) chirpPlay(k, force); }

// Head sequence + chirp for a state entry. `fromSleep`: the pet just woke;
// states without a chirp of their own get the wake whistle instead.
static void onEnter(PersonaState s, uint32_t now, bool fromSleep) {
  switch (s) {
    case P_SLEEP:     play(SEQ_SLEEP, NKEYS(SEQ_SLEEP), now);
                      say(CHIRP_SLEEPY); break;
    case P_IDLE:      play(SEQ_LEVEL, NKEYS(SEQ_LEVEL), now);
                      nextIdleAt = now + 8000 + random(7000);
                      if (fromSleep) say(CHIRP_WAKE); break;
    case P_BUSY:      play(SEQ_LEVEL, NKEYS(SEQ_LEVEL), now);
                      nextNodAt = now + 2500;
                      if (fromSleep) say(CHIRP_WAKE); break;
    case P_ATTENTION: play(SEQ_ATTN_UP, NKEYS(SEQ_ATTN_UP), now);
                      nextScanAt = now + 1200;
                      say(CHIRP_ATTENTION, true); lastAttnChirp = now; break;
    case P_CELEBRATE: play(SEQ_CELEBRATE, NKEYS(SEQ_CELEBRATE), now);
                      say(CHIRP_HAPPY); break;
    case P_DIZZY:     play(SEQ_DIZZY, NKEYS(SEQ_DIZZY), now);
                      say(CHIRP_CONFUSED); break;
    case P_HEART:
      dyn[0] = { 0, (int8_t)(toucherSide * YAW_TOWARD_SCREEN_RIGHT), (int8_t)(PITCH_LEVEL + 8), 400 };
      play(dyn, 1, now);
      say(CHIRP_HAPPY);
      break;
  }
}
// Same pose/timers, no chirp: used when listening ends and the unchanged
// state's pose is restored.
static void onEnterSilent(PersonaState s, uint32_t now) {
  quiet = true; onEnter(s, now, false); quiet = false;
}

void bodyListen(bool on) {
  if (on == listening) return;
  listening = on;
  uint32_t now = millis();
  if (on) {
    seq = nullptr;
    gazeUntil = 0; gazePending = false; gazeSide = 0;
    listenSide = (now - toucherAt < 30000) ? toucherSide : 0;
    headTo(listenSide * YAW_FOUND_YOU, PITCH_LEVEL + 15, 500);   // clamped in headTo
    chirpPlay(CHIRP_LISTEN, true);
  } else if (lastState != 0xFF) {
    // Back to the current state's pose and timers (attention resumes
    // scanning from its entry sequence). Re-entry is silent: the state did
    // not change, so no chirp — handled by the `fromSleep=false` path plus
    // a mute around the call.
    listenSide = 0;
    enteredAt = now;
    PersonaState s = (PersonaState)lastState;
    if (s == P_ATTENTION) { play(SEQ_ATTN_UP, NKEYS(SEQ_ATTN_UP), now); nextScanAt = now + 1200; }
    else onEnterSilent(s, now);
  }
}

static void updateInner(PersonaState active, bool needsAttention, uint32_t now);

void bodyUpdate(PersonaState active, bool needsAttention, uint32_t now) {
  updateInner(active, needsAttention, now);
  driftOn = active != P_SLEEP;
  stepTween(now);                           // every loop, regardless of the early returns above
}

static void updateInner(PersonaState active, bool needsAttention, uint32_t now) {
  if (!touchArmed && (int32_t)(now - touchArmAt) >= 0) {
    M5StackChan.TouchSensor.recalibrate();
    touchArmed = true;
    Serial.println("[body] top touch armed");
  }

  if ((uint8_t)active != lastState) {
    bool fromSleep = lastState == (uint8_t)P_SLEEP;
    lastState = (uint8_t)active;
    enteredAt = now;
    if (!listening) onEnter(active, now, fromSleep);   // listening pins the pose
  }
  ledForState(active, now);
  // Still waiting: nag every 30 s (not every sweep).
  if (active == P_ATTENTION && !listening && now - lastAttnChirp >= 30000) {
    say(CHIRP_ATTENTION);
    lastAttnChirp = now;
  }

  // Gaze hold (toucher / camera) overrides the periodic behaviours below.
  // Sleep ignores it: a sleeping pet does not track. While listening the
  // pitch stays at the listening value; only the yaw follows the owner.
  bool held = gazeHeld(now) && active != P_SLEEP;
  if (held && gazePending) {
    seq = nullptr;
    int pitch = listening ? PITCH_LISTEN
              : gazePitch >= 0 ? gazePitch
              : (active == P_ATTENTION || needsAttention) ? PITCH_ATTENTION : curPitch;
    headTo(gazeYaw, pitch, active == P_ATTENTION ? 600 : 300);
    gazePending = false;
  }
  if (listening) { stepSeq(now); return; }  // no sway / nod while listening; sweeps from gaze.cpp still step

  switch (active) {
    case P_IDLE:
      // Slow yaw sway every 8..15 s, small amplitude, back to centre 2.5 s later.
      if (!held && !seq && (int32_t)(now - nextIdleAt) >= 0) {
        int amp = 8 + random(11);
        if (random(2)) amp = -amp;
        dyn[0] = { 0,    (int8_t)amp, PITCH_LEVEL, 120 };
        dyn[1] = { 2500, 0,           PITCH_LEVEL, 120 };
        play(dyn, 2, now);
        nextIdleAt = now + 8000 + random(7000);
      }
      break;
    case P_BUSY:
      if (!held && !seq && (int32_t)(now - nextNodAt) >= 0) {
        play(SEQ_NOD, NKEYS(SEQ_NOD), now);
        nextNodAt = now + 2500;
      }
      break;
    case P_ATTENTION:
      // Head up on entry. Where to look comes from gaze.cpp: camera live
      // target, else the remembered owner spot, else bodySearchSweep()
      // ("where are you?") every 5 s until the owner is found.
      break;
    default:
      // A one-shot overlay (celebrate/heart/dizzy) over a waiting session:
      // keep the head up so the attention pose survives the overlay.
      if (needsAttention && !held && !seq && now - enteredAt > 1500 && curPitch != PITCH_ATTENTION) {
        play(SEQ_ATTN_UP, NKEYS(SEQ_ATTN_UP), now);
      }
      break;
  }
  stepSeq(now);
}

void bodySetLed(uint8_t r, uint8_t g, uint8_t b) {
  M5StackChan.showRgbColor(r, g, b);
}

uint8_t bodyTouchZone(uint8_t zone) {
  if (!touchArmed || zone > 2) return 0;
  return M5StackChan.TouchSensor.getIntensities()[zone];
}
