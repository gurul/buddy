#pragma once
// buddy's own ears: a loudness reading from the CoreS3 microphone, taken
// while it explores.
//
// The camera tells the host what the room looks like; this tells it whether
// the room is quiet. A desk that has been silent all afternoon and a desk
// where a door just banged look identical in a 320x240 frame.
//
// The catch, and the reason this module exists rather than a few lines in
// main.cpp: on the CoreS3 the ES7210 microphone codec and the AW88298 speaker
// amplifier sit on the SAME I2S bus (BCK G34, WS G33; M5Unified configures
// both with pin_bck = 34). Only one of them can hold the port. M5Unified's
// Mic_Class and Speaker_Class each install and uninstall the I2S driver on
// begin()/end(), so a mic that keeps the bus is a robot that has lost its
// voice — and the chirps are half of buddy's personality.
//
// So the speaker owns the bus by default and the ear only borrows it: one
// short window at a time, never while a chirp is playing or pending, and the
// speaker is handed back before this function returns. If the mic cannot be
// started at all the whole feature switches itself off after one warning and
// the audio path is left exactly as it was.
//
// STATUS, 2026-09-06: on this board the ES7210 hands back digital silence.
// The mic reports itself enabled with the pins M5Unified assigns for the
// CoreS3 (port 1, BCK 34, WS 33, DIN 14, MCK 0, magnification 2, stereo,
// 16 kHz) and every sample is exactly zero; M5.Mic.begin() also refuses
// outright during setup, before the camera has touched anything. The prime
// suspect is the internal I2C bus: the ES7210 is configured by register
// writes over M5.In_I2C, and look.cpp calls M5.In_I2C.release() and hands
// pins 11/12 to the camera's own SCCB driver. Sorting that out means reading
// the ES7210 datasheet and reworking who owns that bus, which is not
// something to guess at. Until then the host treats an all-zero reading as
// "no ear" rather than "a silent room" (hearing.py), so nothing downstream
// invents a hush buddy cannot actually hear.
//
// Reports to the host, at most every kHearPeriodMs while explore mode is on:
//   {"sound":{"rms":0..100,"peak":0..100,"quiet":0..100}}
// rms and peak are this window; quiet is the running floor buddy has learned
// for this room, so the host can tell "loud" from "loud for here".
#include <stdint.h>

// Allocates the sample buffer in PSRAM. Safe to call when there is no mic:
// hearing then reports itself unavailable and never touches the audio path.
void hearingBegin();

// Call once per loop(). Takes a window only when `exploring` is true, the
// speaker is idle, and the period has elapsed. Never blocks for more than
// kHearWindowMs plus a small settle.
void hearingUpdate(bool exploring, uint32_t nowMs);

// True once the mic has been proven to work (or false after a failure).
bool hearingAvailable();

// The last window, 0..100 each. Zero until the first successful sample.
uint8_t hearingRms();
uint8_t hearingPeak();
uint8_t hearingQuiet();
