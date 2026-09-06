#pragma once
// M5StackChan K151 body: head servos, 12 RGB LEDs, three top touch zones.
// The face is eyes.cpp (RoboEyes); the body only reports head angles to it.
//
// Choreography lives here so it can be tuned without touching the render
// loop. Everything is non-blocking: bodyUpdate() is called once per loop()
// and only issues servo targets; the BSP Motion task animates them at 50 Hz.
// This header pulls in no M5 library so board_compat.cpp can include it.
#include <stdint.h>
#include "persona.h"
#include "mood.h"

// After halBegin(). Servo power on, goHome() once, torque managed by the
// BSP auto-release. Arms the top touch sensor 3 s later (see bodyUpdate).
void bodyBegin();

// Drive head, LEDs and face expression from the pet's state.
// `needsAttention` is the base state (a session is blocked on the human),
// not a one-shot overlay, so a level-up celebrate cannot silence the
// attention scan.
void bodyUpdate(PersonaState active, bool needsAttention, uint32_t now);

// All 12 LEDs one colour. Called through ledSet() (which caches unchanged
// colours; every call here is an I2C write to the PY32 expander).
void bodySetLed(uint8_t r, uint8_t g, uint8_t b);

// LED policy inputs, set each loop before bodyUpdate(): the user's LED
// toggle (settings().led) and whether a push-to-talk hold is live (solid
// blue overrides every state colour — it is the "mic is live" indicator).
void bodyLedPolicy(bool enabled, bool micLive);

// Top touch sensor, zone 0=front 1=middle 2=back, intensity 0 (idle) .. 3.
// Returns 0 until the sensor is armed (recalibrated 3 s after boot, once
// the servo 5 V rail has settled) so a stale baseline cannot fire a
// phantom push-to-talk.
uint8_t bodyTouchZone(uint8_t zone);

// Which side of the pet the last touch came from: -1 = screen left,
// +1 = screen right, 0 = straight on (top touch sensor). In attention the
// head turns to that side and holds the gaze for 12 s ("found you"); the
// heart one-shot tilts toward it.
void bodyNoteToucher(int8_t side);

// External gaze request (future camera leaf): turn to yawDeg (clamped to
// ±60) and hold for holdMs, overriding the attention scan / idle sway the
// same way a toucher does. holdMs 0 cancels a hold.
void bodyLookAt(int8_t yawDeg, uint16_t holdMs);
// Same with an explicit pitch (camera gaze). Pitch is clamped 5..85.
void bodyLookAt(int8_t yawDeg, int8_t pitchDeg, uint16_t holdMs);

// gaze.cpp: run one "where are you?" sweep now (far left, near left, near
// right, far right, centre; ~3 s). Pitch stays at the listening value while
// listening, else the attention value. Ignored (returns false) while a gaze
// hold or another sequence is active. A later bodyLookAt() cancels it.
bool bodySearchSweep();

// Listening pose, driven by the host dictation key ({"cmd":"listen"}) or the
// on-screen push-to-talk hold. On: face the user (toward the last toucher
// side if seen within 30 s, else centre), pitch PITCH_LEVEL+15, balloon
// "listening...", periodic behaviours paused. Off: the current state's pose
// and balloon come back (attention resumes its scan). LEDs come from
// bodyLedPolicy(micLive), which main.cpp sets from the same condition.
void bodyListen(bool on);

// For the eyes (eyes.cpp): last commanded head angles in degrees, and the
// toucher side while a gaze hold / listening pose is pinned on the user
// (-1 screen left, +1 screen right, 0 none).
int    bodyYawDeg();
int    bodyPitchDeg();
int8_t bodyGazeSide();
// True while a glide is in progress OR the servos report motion. The vision
// quarantine must use this, not the servo flag alone.
bool   bodyMoving();
// The pose actually streamed to the servos right now (mid-glide value), for
// tagging camera frames with the pose at capture.
int    bodyCmdYawDeg();
int    bodyCmdPitchDeg();

// Explore mode ({"cmd":"mode","explore":true}): the host drives the head
// with look cmds; the sleep pose is not applied and the LEDs breathe dim
// white. explore=false restores the state's pose.
void   bodySetExplore(bool on);

// Host voice / computer-control conversation ({"cmd":"agent","state":..}):
// the robot acts the phase out (see AgentState in persona.h). AG_IDLE
// restores the persona state's pose. Never overrides ATTENTION or a card.
void   bodySetAgent(AgentState s);
// The mood engine's expression while exploring (main.cpp owns the engine):
// LED colour and pulse, look-around tempo / amplitude / pitch bias, and one
// chirp per feeling change. nullptr = not exploring: the plain explore LED.
void   bodySetMood(const MoodExpr* e);
// Edge flags for the mood engine, cleared on read: a touch on the pet since
// the last call; the look-around started a new glance since the last call.
bool   bodyTakeTouched();
bool   bodyTakeNewView();
