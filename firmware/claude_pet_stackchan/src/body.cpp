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
// the choreography stays inside ±YAW_MAX.
//
// The yaw servo (Feetech SCS0009, id 1) is configured by the BSP with
// angleLimit -1280..1280 in tenths of a degree — ±128° in position mode, and
// that is the whole swivel there is: a full 360° turn needs continuous
// rotation (PWM mode), which has no position feedback and would wind the
// neck loom, so it is not what an unattended desk robot should do. ±120
// keeps 8° of margin under the servo's own clamp and lets buddy look behind
// its own shoulders on both sides, which is most of a room.
static const int PITCH_MIN = 5;
static const int PITCH_MAX = 85;
static const int YAW_MAX   = 120;

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
int  bodyCmdYawDeg()   { return (int)lroundf(cmdYaw); }
int  bodyCmdPitchDeg() { return (int)lroundf(cmdPitch); }

static bool exploring = false;   // host drives the head; sleep pose not applied
static void onEnterSilent(PersonaState s, uint32_t now);

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
// "Where are you?" search, two rows so a face above the current gaze is
// found: row 1 at pitch 45 sweeps left to right across the full ±100 the
// neck allows, row 2 at pitch 65 comes back right to left, then centre.
// ~5.7 s, repeated every 5 s while the owner is wanted and not found. The
// wide sweep is the point: the owner is often not in front of the desk.
static const int PITCH_SCAN_LOW = 45, PITCH_SCAN_HIGH = 65;
static const Key SEQ_ATTN_SCAN[] = {
  {0,   -100, PITCH_SCAN_LOW,  500}, {900,  -45, PITCH_SCAN_LOW,  400},
  {1400,   0, PITCH_SCAN_LOW,  400}, {1900,  45, PITCH_SCAN_LOW,  400},
  {2400, 100, PITCH_SCAN_LOW,  500}, {3000, 100, PITCH_SCAN_HIGH, 400},
  {3500,  45, PITCH_SCAN_HIGH, 400}, {4000, -45, PITCH_SCAN_HIGH, 500},
  {4600,-100, PITCH_SCAN_HIGH, 500}, {5200,   0, PITCH_SCAN_HIGH, 500} };
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
static uint32_t nextExploreAt = 0;   // next self-driven look-around while exploring
static AgentState agentState  = AG_IDLE;
static uint32_t agentSince    = 0;    // when the current agent phase began
static uint32_t nextAgentBeat = 0;    // next micro-motion of the current agent phase
static const MoodExpr* mood   = nullptr;
static uint8_t  moodKindSeen  = 0xFF;
static uint32_t moodKindAt    = 0;
static uint32_t moodSince(uint32_t now) { return now - moodKindAt; }
static bool     touchedFlag   = false;
static bool     newViewFlag   = false;
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
  // Two pitch rows (45 then 65) in both attention and listening: a face
  // above the current gaze is the common miss (bench: head aimed at the wall).
  play(SEQ_ATTN_SCAN, NKEYS(SEQ_ATTN_SCAN), now);
  return true;
}

void bodySetExplore(bool on) {
  if (on == exploring) return;
  exploring = on;
  if (!on && lastState != 0xFF && !listening && agentState == AG_IDLE) onEnterSilent((PersonaState)lastState, millis());
}

bool bodyTakeTouched() { bool t = touchedFlag; touchedFlag = false; return t; }
bool bodyTakeNewView() { bool t = newViewFlag; newViewFlag = false; return t; }

static const Key SEQ_AGENT_DONE[]  = { {0, KEEP, PITCH_LEVEL - 10, 500}, {350, KEEP, PITCH_LEVEL + 4, 500},
                                       {700, KEEP, PITCH_LEVEL, 400} };
static const Key SEQ_AGENT_ERROR[] = { {0, -14, KEEP, 700}, {220, 14, KEEP, 700}, {440, -8, KEEP, 700},
                                       {660, 0, PITCH_LEVEL, 500} };

void bodySetAgent(AgentState s) {
  if (s == agentState) return;
  uint32_t now = millis();
  agentState = s;
  agentSince = now;
  seq = nullptr;
  gazeUntil = 0; gazePending = false;
  nextAgentBeat = now + 600;
  switch (s) {
    case AG_WAKE:      headTo(0, PITCH_LEVEL + 12, 700); chirpPlay(CHIRP_WAKE, true); break;
    case AG_LISTENING: headTo((now - toucherAt < 30000) ? toucherSide * YAW_FOUND_YOU : 0, PITCH_LEVEL + 15, 500); break;
    case AG_THINKING:  headTo(random(2) ? 14 : -14, PITCH_LEVEL + 6, 300); nextAgentBeat = now + 2500; break;
    case AG_SPEAKING:  headTo(0, PITCH_LEVEL + 10, 400); break;
    case AG_WORKING:   headTo(0, 30, 400); nextAgentBeat = now + 700; break;   // eyes down at the desk
    case AG_ASKING:    headTo(0, PITCH_ATTENTION, 600); chirpPlay(CHIRP_LISTEN, true); break;
    case AG_DONE:      play(SEQ_AGENT_DONE, NKEYS(SEQ_AGENT_DONE), now); chirpPlay(CHIRP_OK, true); break;
    case AG_ERROR:     play(SEQ_AGENT_ERROR, NKEYS(SEQ_AGENT_ERROR), now); chirpPlay(CHIRP_NO, true); break;
    case AG_IDLE:
    default:
      if (lastState != 0xFF && !listening) onEnterSilent((PersonaState)lastState, now);
      break;
  }
}

// Micro-motions that keep an agent phase alive: typing glances while
// working, a bob while speaking, a slow side-to-side while thinking.
static void agentBeat(uint32_t now) {
  if (seq || (int32_t)(now - nextAgentBeat) < 0) return;
  switch (agentState) {
    case AG_WORKING:
      dyn[0] = { 0, (int8_t)random(-6, 7), (int8_t)(28 + random(7)), 300 };
      play(dyn, 1, now);
      nextAgentBeat = now + 700 + random(500);
      break;
    case AG_SPEAKING:
      dyn[0] = { 0, KEEP, (int8_t)(PITCH_LEVEL + 6 + random(9)), 350 };
      play(dyn, 1, now);
      nextAgentBeat = now + 500 + random(300);
      break;
    case AG_THINKING:
      dyn[0] = { 0, (int8_t)(curYaw > 0 ? -14 : 14), (int8_t)(PITCH_LEVEL + 6), 250 };
      play(dyn, 1, now);
      nextAgentBeat = now + 2500 + random(1500);
      break;
    default: break;
  }
}

static ChirpKind moodChirpKind(uint8_t c) {
  switch (c) {
    case MOODCHIRP_CURIOUS:  return CHIRP_CURIOUS;
    case MOODCHIRP_SURPRISE: return CHIRP_SURPRISE;
    case MOODCHIRP_SIGH:     return CHIRP_SIGH;
    case MOODCHIRP_WARBLE:   return CHIRP_WARBLE;
    default:                 return CHIRP_STARTLE;
  }
}

void bodySetMood(const MoodExpr* e) {
  mood = e;
  if (e && (uint8_t)e->kind != moodKindSeen) { moodKindSeen = (uint8_t)e->kind; moodKindAt = millis(); }
  if (e && e->chirp != MOODCHIRP_NONE && exploring && agentState == AG_IDLE && !listening) {
    chirpPlay(moodChirpKind(e->chirp), e->chirp == MOODCHIRP_STARTLE);
    if (e->chirp == MOODCHIRP_STARTLE) {          // the jerk: head back and up, fast
      seq = nullptr;
      dyn[0] = { 0, (int8_t)(curYaw / 2), (int8_t)(PITCH_LEVEL + 20), 900 };
      dyn[1] = { 700, KEEP, (int8_t)(PITCH_LEVEL + 8), 400 };
      play(dyn, 2, millis());
    }
  }
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
  touchedFlag = true;
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
// Twelve WS2812 on the back: index 0-5 the left row, 6-11 the right row.
// Every state is a small animation over the two rows rather than one flat
// colour: a breathing wave that runs along the row, a scanner dot, a
// heartbeat from the middle outward, sparkles, a flash that fades, the two
// rows alternating. Frames are composed at up to 20 Hz and only the LEDs
// that changed are written (each write is an I2C transaction to the PY32
// expander; the refresh strobe is one more).
struct LedFrame { uint8_t c[12][3]; };
static uint8_t  ledLast[12][3];
static bool     ledLastValid = false;
static uint32_t ledLastMs = 0;

static void ledPush(const LedFrame& f, uint32_t now) {
  if (now - ledLastMs < 50) return;                     // 20 Hz max
  ledLastMs = now;
  bool any = false;
  for (int i = 0; i < 12; i++) {
    if (!ledLastValid || f.c[i][0] != ledLast[i][0] || f.c[i][1] != ledLast[i][1] || f.c[i][2] != ledLast[i][2]) {
      M5StackChan.setRgbColor((uint8_t)i, f.c[i][0], f.c[i][1], f.c[i][2]);
      ledLast[i][0] = f.c[i][0]; ledLast[i][1] = f.c[i][1]; ledLast[i][2] = f.c[i][2];
      any = true;
    }
  }
  if (any) M5StackChan.refreshRgb();
  ledLastValid = true;
}

static inline void ledPix(LedFrame& f, int i, uint8_t r, uint8_t g, uint8_t b, float k) {
  if (k < 0) k = 0; if (k > 1) k = 1;
  f.c[i][0] = (uint8_t)(r * k); f.c[i][1] = (uint8_t)(g * k); f.c[i][2] = (uint8_t)(b * k);
}
static inline float ledTri(uint32_t now, uint32_t periodMs, float phase01) {   // 0..1..0
  float t = fmodf((float)(now % periodMs) / (float)periodMs + phase01, 1.0f);
  return t < 0.5f ? t * 2.0f : 2.0f - t * 2.0f;
}
// Breathing wave: each LED along the row is a little later than the one
// before, so the glow travels; both rows in step. `floor` keeps a dim base.
static void patWave(LedFrame& f, uint8_t r, uint8_t g, uint8_t b, uint32_t periodMs, uint32_t now,
                    float floor = 0.15f, float spread = 0.5f) {
  for (int i = 0; i < 12; i++) {
    int pos = i % 6;
    float k = floor + (1.0f - floor) * ledTri(now, periodMs, spread * pos / 6.0f);
    ledPix(f, i, r, g, b, k);
  }
}
// Scanner: one bright dot bouncing along each row with a short tail.
static void patScanner(LedFrame& f, uint8_t r, uint8_t g, uint8_t b, uint32_t periodMs, uint32_t now) {
  float head = ledTri(now, periodMs, 0.0f) * 5.0f;      // 0..5..0
  for (int i = 0; i < 12; i++) {
    float d = fabsf((float)(i % 6) - head);
    ledPix(f, i, r, g, b, d < 0.5f ? 1.0f : d < 1.5f ? 0.35f : d < 2.5f ? 0.08f : 0.0f);
  }
}
// Flash then fade: full at `sinceMs` = 0, gone after `fadeMs`.
static void patFlashFade(LedFrame& f, uint8_t r, uint8_t g, uint8_t b, uint32_t sinceMs, uint32_t fadeMs) {
  float k = sinceMs >= fadeMs ? 0.0f : 1.0f - (float)sinceMs / (float)fadeMs;
  k *= k;
  for (int i = 0; i < 12; i++) ledPix(f, i, r, g, b, k);
}
// Heartbeat: lub-dub from the middle of each row outward, once a second.
static void patHeartbeat(LedFrame& f, uint8_t r, uint8_t g, uint8_t b, uint32_t now) {
  uint32_t t = now % 1000;
  float beat = t < 120 ? 1.0f : t < 260 ? 0.4f : t < 380 ? 0.9f : t < 600 ? 0.25f : 0.12f;
  for (int i = 0; i < 12; i++) {
    float dist = fabsf((float)(i % 6) - 2.5f);         // 0.5 .. 2.5 from the middle
    float k = beat * (1.0f - 0.25f * (dist - 0.5f));
    ledPix(f, i, r, g, b, k);
  }
}
// Sparkle: a few LEDs twinkle, re-rolled every 120 ms.
static void patSparkle(LedFrame& f, uint8_t r, uint8_t g, uint8_t b, uint32_t now, float floor = 0.1f) {
  uint32_t seed = now / 120;
  for (int i = 0; i < 12; i++) {
    uint32_t h = (seed * 2654435761u) ^ ((uint32_t)i * 40503u);
    h ^= h >> 13; h *= 0x5bd1e995u; h ^= h >> 15;
    float k = (h % 100) < 25 ? 1.0f : floor;
    ledPix(f, i, r, g, b, k);
  }
}
// Droop: only the middle two of each row, dim, slow.
static void patDroop(LedFrame& f, uint8_t r, uint8_t g, uint8_t b, uint32_t periodMs, uint32_t now) {
  float k = 0.05f + 0.25f * ledTri(now, periodMs, 0.0f);
  for (int i = 0; i < 12; i++) {
    int pos = i % 6;
    ledPix(f, i, r, g, b, (pos == 2 || pos == 3) ? k : 0.0f);
  }
}
// Alternate: the left row and the right row take turns.
static void patAlternate(LedFrame& f, uint8_t r, uint8_t g, uint8_t b, uint32_t periodMs, uint32_t now) {
  bool left = (now % periodMs) < periodMs / 2;
  for (int i = 0; i < 12; i++) ledPix(f, i, r, g, b, ((i < 6) == left) ? 1.0f : 0.12f);
}
static void patSolid(LedFrame& f, uint8_t r, uint8_t g, uint8_t b) {
  for (int i = 0; i < 12; i++) ledPix(f, i, r, g, b, 1.0f);
}

void ledSet(uint8_t r, uint8_t g, uint8_t b);   // board_compat.cpp (all LEDs, deduped)
static void ledForState(PersonaState s, uint32_t now) {
  LedFrame f = {};
  if (!ledEnabled) { ledPush(f, now); return; }
  if (micLive) { patSolid(f, 0, 30, 90); ledPush(f, now); return; }
  if (agentState != AG_IDLE && s != P_ATTENTION) {
    // The conversation's phase, at a glance: blue breath = listening, cyan
    // scanner = thinking, white sparkle = talking, cyan ripple = working,
    // orange alternating = a question for you, green = done, red = error.
    uint32_t since = now - agentSince;
    switch (agentState) {
      case AG_WAKE:      patFlashFade(f, 90, 90, 90, since, 700); break;
      case AG_LISTENING: patWave(f, 0, 40, 110, 3000, now, 0.2f, 0.4f); break;
      case AG_THINKING:  patScanner(f, 0, 70, 90, 1400, now); break;
      case AG_SPEAKING:  patSparkle(f, 80, 80, 90, now, 0.08f); break;
      case AG_WORKING:   patWave(f, 0, 60, 90, 450, now, 0.1f, 0.8f); break;
      case AG_ASKING:    patAlternate(f, 110, 40, 0, 800, now); break;
      case AG_DONE:      if (since < 900) patScanner(f, 0, 100, 20, 1800, now); else patSolid(f, 0, 60, 10); break;
      case AG_ERROR:     patFlashFade(f, 110, 0, 0, since % 500, 500); break;
      default: break;
    }
    ledPush(f, now);
    return;
  }
  if (exploring && s != P_ATTENTION) {
    if (mood) {
      // The feeling: colour from valence/arousal (mood.cpp), the animation
      // from the kind. Scaled down: twelve of these are bright.
      uint8_t r = (uint8_t)(mood->r * 0.4f), g = (uint8_t)(mood->g * 0.4f), b = (uint8_t)(mood->b * 0.4f);
      uint32_t period = (uint32_t)(1000.0f / mood->pulseHz);
      switch (mood->kind) {
        case MOOD_STARTLED:  patFlashFade(f, 120, 0, 0, moodSince(now), 1500); break;
        case MOOD_SURPRISED: patFlashFade(f, 100, 100, 100, moodSince(now), 900); break;
        case MOOD_AFFECTION: patHeartbeat(f, 110, 20, 60, now); break;
        case MOOD_HAPPY:     patSparkle(f, r, g, b, now, 0.15f); break;
        case MOOD_CURIOUS:   patScanner(f, r, g, b, (uint32_t)(1600.0f / mood->tempo), now); break;
        case MOOD_BORED:     patDroop(f, r, g, b, period, now); break;
        case MOOD_LONELY:    patDroop(f, 40, 20, 80, period, now); break;
        default:             patWave(f, r, g, b, period, now, 0.12f, 0.5f); break;
      }
      ledPush(f, now);
      return;
    }
    patWave(f, 16, 16, 16, 6000, now, 0.15f, 0.5f);   // explore without a mood: slow white wave
    ledPush(f, now);
    return;
  }
  switch (s) {
    case P_ATTENTION: {
      // Orange, urgent: the two rows alternate at 800 ms on top of a pulse.
      float k = 0.35f + 0.65f * ledTri(now, 800, 0.0f);
      patAlternate(f, (uint8_t)(255 * k), (uint8_t)(80 * k), 0, 800, now);
      break;
    }
    case P_BUSY:      patWave(f, 0, 28, 40, 4000, now, 0.5f, 0.5f); break;     // cyan, gentle
    case P_HEART:     patHeartbeat(f, 90, 0, 30, now); break;
    case P_CELEBRATE: patSparkle(f, 0, 90, 15, now, 0.2f); break;
    case P_DIZZY:     patScanner(f, 90, 30, 0, 600, now); break;
    case P_SLEEP:
    case P_IDLE:      patWave(f, 0, 0, 8, 4000, now, 0.0f, 0.5f); break;       // dim blue breathe, dark-room only
    default:          break;
  }
  ledPush(f, now);
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
    case P_SLEEP:     if (!exploring) play(SEQ_SLEEP, NKEYS(SEQ_SLEEP), now);   // explore: head stays where the host put it
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
  if (agentState != AG_IDLE && active != P_ATTENTION) { agentBeat(now); stepSeq(now); return; }
  if (exploring && active != P_ATTENTION) {
    // Host owns the head while one of its `look`s is held. Whenever it is
    // not — between waypoints, and through the host's rest between pan
    // cycles — look around the room on our own: a random glance (yaw up to
    // ±YAW_MAX with the feeling, pitch 15..75, the same band the host pans)
    // held 2..4 s, then another
    // spot, every 4..9 s. Checked against gazeHeld() directly rather than
    // `held`, which ignores the hold in SLEEP where explore still runs.
    // With a mood: amplitude, tempo and a pitch bias follow the feeling
    // (keen = wide and quick, bored = narrow, slow and drooping).
    if (!gazeHeld(now) && !seq && (int32_t)(now - nextExploreAt) >= 0) {
      int   amp   = mood ? mood->amplitude : 90;
      int   bias  = mood ? mood->pitchBias : 0;
      float tempo = mood ? mood->tempo : 1.0f;
      int8_t yaw1 = (int8_t)random(-amp, amp + 1), yaw2 = (int8_t)random(-amp, amp + 1);
      // 15..75: the desk as well as the room. The old band started at 35,
      // which on a head zeroed a little low never looked down at all.
      int8_t pit1 = (int8_t)clampPitch(15 + random(61) + bias), pit2 = (int8_t)clampPitch(15 + random(61) + bias);
      uint16_t speed = (uint16_t)(140 * tempo);
      dyn[0] = { 0, yaw1, pit1, speed };
      dyn[1] = { (uint16_t)((2000 + random(2000)) / tempo), yaw2, pit2, speed };
      play(dyn, 2, now);
      nextExploreAt = now + (uint32_t)((4000 + random(5000)) / tempo);
      newViewFlag = true;
    }
    stepSeq(now);
    return;
  }

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
